"""Daemon AIS — AISStream.io WebSocket (streaming, pas pull).

Souscription PositionReport + ShipStaticData sur les bbox des 6 corridors
du front (ais_map.yaml). Buffer mémoire MMSI → dernière position connue,
flush parquet périodique (un snapshot horodaté par flush, dédup par MMSI),
reconnexion automatique avec backoff exponentiel et log des coupures.

Géré par scripts/interim_loop.sh (PID file _logs/ais_ws.pid).
Lancement direct : ./venv/bin/python -m collectors.ais_ws
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import websockets
import yaml

from collectors.base import REPO_ROOT, get_logger

MAP_PATH = REPO_ROOT / "api" / "config" / "ais_map.yaml"
WS_URL = "wss://stream.aisstream.io/v0/stream"
DATA_DIR = REPO_ROOT / "data" / "ais"


def load_ais_map(path: Path = MAP_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if len(cfg.get("corridors", [])) != 6:
        raise RuntimeError("ais_map.yaml : 6 corridors attendus (contrat front)")
    return cfg


def corridor_bboxes(cfg: dict) -> list[list[list[float]]]:
    """Corridors (segments lon/lat) → bbox AISStream [[lat1,lon1],[lat2,lon2]]."""
    m = cfg["margin_deg"]
    boxes = []
    for c in cfg["corridors"]:
        (lon_a, lat_a), (lon_b, lat_b) = c["a"], c["b"]
        boxes.append([
            [min(lat_a, lat_b) - m, min(lon_a, lon_b) - m],
            [max(lat_a, lat_b) + m, max(lon_a, lon_b) + m],
        ])
    return boxes


def corridor_of(lon: float, lat: float, cfg: dict) -> str | None:
    m = cfg["margin_deg"]
    for c in cfg["corridors"]:
        (lon_a, lat_a), (lon_b, lat_b) = c["a"], c["b"]
        if (min(lat_a, lat_b) - m <= lat <= max(lat_a, lat_b) + m
                and min(lon_a, lon_b) - m <= lon <= max(lon_a, lon_b) + m):
            return c["name"]
    return None


def ship_kind(type_code: int | None) -> str | None:
    """Code type AIS → TANKER/CARGO (contrat front), sinon None (ignoré)."""
    if type_code is None:
        return None
    if 70 <= type_code <= 79:
        return "CARGO"
    if 80 <= type_code <= 89:
        return "TANKER"
    return None


def select_vessels(state: dict[int, dict], cfg: dict,
                   now: datetime | None = None) -> pd.DataFrame:
    """Buffer MMSI → ~serve_max navires : type connu TANKER/CARGO,
    récence d'abord, répartition par corridor."""
    now = now or datetime.now(timezone.utc)
    rows = [v | {"mmsi": k} for k, v in state.items() if v.get("type")]
    if not rows:
        return pd.DataFrame(columns=["mmsi", "lon", "lat", "type", "corridor", "ts"])
    df = pd.DataFrame(rows).sort_values("ts", ascending=False)
    per_corridor = math.ceil(cfg["serve_max"] / len(cfg["corridors"]))
    kept = (df.groupby("corridor", group_keys=False)
              .head(per_corridor)
              .head(cfg["serve_max"])
              .reset_index(drop=True))
    return kept[["mmsi", "lon", "lat", "type", "corridor", "ts"]]


class AisDaemon:
    def __init__(self) -> None:
        self.log = get_logger("ais")
        self.cfg = load_ais_map()
        key = os.getenv("AISSTREAM_API_KEY", "").strip()
        if not key:
            raise RuntimeError("AISSTREAM_API_KEY manquante dans .env")
        self.key = key
        #: mmsi → {lon, lat, ts, corridor, type}
        self.state: dict[int, dict] = {}
        self.types: dict[int, str] = {}
        self.running = True
        self.msg_count = 0

    # ---------------------------------------------------------- messages

    def handle(self, raw: str | bytes) -> None:
        msg = json.loads(raw)
        kind = msg.get("MessageType")
        meta = msg.get("MetaData") or {}
        mmsi = meta.get("MMSI")
        if mmsi is None:
            return
        if kind == "ShipStaticData":
            t = ship_kind((msg.get("Message", {}).get("ShipStaticData") or {}).get("Type"))
            if t:
                self.types[mmsi] = t
                if mmsi in self.state:
                    self.state[mmsi]["type"] = t
            return
        if kind != "PositionReport":
            return
        lat, lon = meta.get("latitude"), meta.get("longitude")
        if lat is None or lon is None:
            return
        corridor = corridor_of(lon, lat, self.cfg)
        if corridor is None:
            return
        self.msg_count += 1
        self.state[mmsi] = {
            "lon": float(lon), "lat": float(lat),
            "ts": datetime.now(timezone.utc),
            "corridor": corridor,
            "type": self.types.get(mmsi),
        }

    # ------------------------------------------------------------- flush

    def flush(self) -> int:
        sel = select_vessels(self.state, self.cfg)
        if sel.empty:
            return 0
        now = datetime.now(timezone.utc)
        sel = sel.copy()
        sel["snapshot_ts"] = now
        part_dir = DATA_DIR / f"date={now:%Y-%m-%d}"
        part_dir.mkdir(parents=True, exist_ok=True)
        sel.to_parquet(part_dir / f"part-{now:%H%M%S}.parquet", index=False)
        self.log.info("flush", extra={"ctx": {
            "servis": len(sel), "buffer": len(self.state),
            "types_connus": len(self.types), "msgs": self.msg_count}})
        return len(sel)

    # -------------------------------------------------------------- main

    async def run(self) -> None:
        sub = json.dumps({
            "APIKey": self.key,
            "BoundingBoxes": corridor_bboxes(self.cfg),
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        })
        backoff = self.cfg["reconnect_min_s"]
        flush_every = self.cfg["flush_seconds"]
        while self.running:
            try:
                async with websockets.connect(WS_URL, ping_interval=20) as ws:
                    await ws.send(sub)
                    self.log.info("connecté", extra={"ctx": {"bboxes": 6}})
                    backoff = self.cfg["reconnect_min_s"]
                    last_flush = asyncio.get_event_loop().time()
                    async for raw in ws:
                        self.handle(raw)
                        now_t = asyncio.get_event_loop().time()
                        if now_t - last_flush >= flush_every:
                            self.flush()
                            last_flush = now_t
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self.log.warning("coupure WebSocket — reconnexion", extra={"ctx": {
                    "err": str(err)[:120], "backoff_s": backoff}})
                self.flush()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.cfg["reconnect_max_s"])


def main() -> int:
    daemon = AisDaemon()

    def _stop(signum, frame):
        daemon.running = False
        daemon.flush()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        asyncio.run(daemon.run())
    except SystemExit:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
