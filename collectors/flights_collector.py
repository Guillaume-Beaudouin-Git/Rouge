"""Collecteur vols militaires — OpenSky states/all (OAuth2 client-credentials).

Auth, budget crédits et heuristique de filtrage documentés dans
docs/decisions/opensky_mil.md. Cadence cible 10 min (576 crédits/jour =
14 % du quota authentifié). Contrat front MILPTS : [lon, lat, libellé].
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from collectors.base import REPO_ROOT, BaseCollector

MAP_PATH = REPO_ROOT / "api" / "config" / "mil_filter.yaml"
TOKEN_URL = ("https://auth.opensky-network.org/auth/realms/opensky-network"
             "/protocol/openid-connect/token")
STATES_URL = "https://opensky-network.org/api/states/all"
#: refresh anticipé du token à 80 % du TTL
TOKEN_REFRESH_FRACTION = 0.8


def load_mil_filter(path: Path = MAP_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not cfg.get("callsign_prefixes") or not cfg.get("hex_ranges"):
        raise RuntimeError("mil_filter.yaml : préfixes ou plages hex manquants")
    cfg["_prefixes"] = tuple(p.upper() for p in cfg["callsign_prefixes"])
    cfg["_ranges"] = [(r["from"].lower(), r["to"].lower()) for r in cfg["hex_ranges"]]
    return cfg


def is_military(icao24: str | None, callsign: str | None, cfg: dict) -> bool:
    if callsign and callsign.strip().upper().startswith(cfg["_prefixes"]):
        return True
    if icao24:
        h = icao24.strip().lower()
        return any(lo <= h <= hi for lo, hi in cfg["_ranges"])
    return False


def filter_states(states: list[list], cfg: dict) -> pd.DataFrame:
    """states/all brut → points MIL : lon, lat, label, ts, icao24.
    Format OpenSky : [icao24, callsign, origin_country, time_position,
    last_contact, lon, lat, …, on_ground, …]."""
    rows = []
    for s in states:
        icao24, callsign, country = s[0], s[1], s[2]
        lon, lat, on_ground = s[5], s[6], s[8]
        if lon is None or lat is None or on_ground:
            continue
        if not is_military(icao24, callsign, cfg):
            continue
        label = f"{(callsign or icao24).strip().upper()} · {str(country).upper()}"
        rows.append({"icao24": icao24, "lon": float(lon), "lat": float(lat),
                     "label": label, "last_contact": int(s[4] or 0)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # un point par appareil, les contacts les plus récents d'abord, cap front
    df = (df.sort_values("last_contact", ascending=False)
            .drop_duplicates(subset="icao24")
            .head(cfg["serve_max"])
            .reset_index(drop=True))
    return df


class FlightsCollector(BaseCollector):
    name = "flights"
    dataset = "mil"
    timeout = 30.0

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_mil_filter()
        self._token: str | None = None
        self._token_exp = 0.0
        cid = os.getenv("OPENSKY_CLIENT_ID", "").strip()
        sec = os.getenv("OPENSKY_CLIENT_SECRET", "").strip()
        if not cid or not sec:
            raise RuntimeError("OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET manquants dans .env")
        self._creds = (cid, sec)

    # -------------------------------------------------------------- token

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        resp = self.client.post(TOKEN_URL, data={
            "grant_type": "client_credentials",
            "client_id": self._creds[0], "client_secret": self._creds[1],
        })
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        ttl = float(payload.get("expires_in", 1800))
        self._token_exp = time.time() + ttl * TOKEN_REFRESH_FRACTION
        self.log.info("token oauth2", extra={"ctx": {"ttl_s": ttl}})
        return self._token

    # ------------------------------------------------------------ collect

    def collect(self) -> pd.DataFrame:
        resp = self.fetch(STATES_URL,
                          headers={"Authorization": f"Bearer {self._get_token()}"})
        remaining = resp.headers.get("x-rate-limit-remaining")
        payload = resp.json()
        df = filter_states(payload.get("states") or [], self.cfg)
        if df.empty:
            raise RuntimeError("aucun vol militaire visible (filtre vide)")
        df["api_ts"] = payload.get("time")
        self.log.info("états filtrés", extra={"ctx": {
            "total": len(payload.get("states") or []), "mil": len(df),
            "credits_restants": remaining}})
        return df

    def run(self) -> bool:
        try:
            df = self.collect()
            now = datetime.now(timezone.utc)
            df["snapshot_ts"] = now
            part_dir = self.data_dir / f"date={now:%Y-%m-%d}"
            part_dir.mkdir(parents=True, exist_ok=True)
            df.to_parquet(part_dir / f"part-{now:%H%M%S}.parquet", index=False)
            self.log.info("run ok", extra={"ctx": {"points": len(df)}})
            return True
        except Exception:
            self.log.error("run failed — donnée précédente conservée (stale)", exc_info=True)
            return False
        finally:
            if self._client is not None:
                self._client.close()
                self._client = None


if __name__ == "__main__":
    raise SystemExit(0 if FlightsCollector().run() else 1)
