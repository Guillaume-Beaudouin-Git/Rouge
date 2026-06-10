"""Collecteur Polymarket — Gamma API (lecture seule, sans clé).

Marchés actifs uniquement, par catégories front mappées dans
api/config/pm_map.yaml. Chaque collecte écrit un fichier parquet horodaté
(un point de probabilité par marché par collecte) : le lake sert à la fois
l'instantané du front (marchés display) et l'historique complet — en
particulier tous les marchés Fed, pour l'analyse RV vs CME FedWatch.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from collectors.base import REPO_ROOT, BaseCollector

MAP_PATH = REPO_ROOT / "api" / "config" / "pm_map.yaml"
GAMMA_BASE = "https://gamma-api.polymarket.com"


def load_pm_map(path: Path = MAP_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not cfg.get("categories"):
        raise RuntimeError("pm_map.yaml : aucune catégorie définie")
    for key, cat in cfg["categories"].items():
        for field in ("tag_slug", "quota", "anchor"):
            if field not in cat:
                raise RuntimeError(f"pm_map.yaml : champ '{field}' manquant pour '{key}'")
    return cfg


def _yes_price(market: dict) -> float | None:
    """Prix de l'issue Yes en %, ou None si le marché n'est pas binaire propre."""
    try:
        outcomes = json.loads(market.get("outcomes") or "[]")
        prices = json.loads(market.get("outcomePrices") or "[]")
        idx = outcomes.index("Yes") if "Yes" in outcomes else 0
        return float(prices[idx]) * 100
    except (ValueError, IndexError, TypeError):
        return None


def _ends_in_future(market: dict, now: datetime) -> bool:
    raw = market.get("endDate")
    if not raw:
        return True
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")) > now
    except ValueError:
        return True


def harvest(events: list[dict], cat_key: str, cat: dict, now: datetime | None = None) -> list[dict]:
    """Événements Gamma d'une catégorie → lignes normalisées.

    Par événement, le marché le plus traité (vol 24 h) à probabilité non
    décidée (1–99 %) et à échéance future est candidat à l'affichage, dans
    la limite du quota ; si collect_all, tous les marchés actifs sont
    historisés.
    """
    now = now or datetime.now(timezone.utc)
    rows: list[dict] = []
    displayed = 0
    for ev in events:
        markets = [m for m in ev.get("markets", [])
                   if m.get("active") and not m.get("closed") and m.get("acceptingOrders", True)]
        live = [(m, _yes_price(m)) for m in markets]
        live = [(m, p) for m, p in live if p is not None]
        if not live:
            continue
        # un événement dont tous les livres sont décidés (<1 % ou >99 %)
        # ou expirés (en attente de résolution) n'est pas affichable —
        # mais reste historisé si collect_all
        undecided = [(m, p) for m, p in live if 1 <= p <= 99 and _ends_in_future(m, now)]
        best = max(undecided, key=lambda mp: mp[0].get("volume24hr") or 0)[0] if undecided else None
        for m, p in live:
            is_display = best is not None and m is best and displayed < cat["quota"]
            if not (is_display or cat.get("collect_all")):
                continue
            d = m.get("oneDayPriceChange")
            rows.append({
                "category": cat_key,
                "event_title": ev.get("title"),
                "event_slug": ev.get("slug"),
                "market_id": str(m.get("id")),
                "market_slug": m.get("slug"),
                "q": m.get("question"),
                "p": round(p),
                "p_raw": round(p, 2),
                "d": round((d or 0) * 100),
                "vol_num": float(m.get("volumeNum") or 0),
                "vol24h": float(m.get("volume24hr") or 0),
                "end_date": m.get("endDate"),
                "display": is_display,
            })
            if is_display:
                displayed += 1
    return rows


def place(rows: list[dict], cfg: dict) -> list[dict]:
    """Ancre les marchés display sur la carte (anchor + décalage par rang)."""
    ord_global = 0
    for cat_key, cat in cfg["categories"].items():
        rank = 0
        for r in rows:
            if r["category"] != cat_key:
                continue
            if r["display"]:
                r["lon"] = cat["anchor"]["lon"] + rank * 4.0
                r["lat"] = cat["anchor"]["lat"] - rank * 3.0
                r["ord"] = ord_global
                rank += 1
                ord_global += 1
            else:
                r["lon"] = None
                r["lat"] = None
                r["ord"] = None
    return rows


class PmCollector(BaseCollector):
    name = "pm"
    dataset = "pm"
    cache_ttl = 300  # cache 5 min : cadence de collecte cible

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_pm_map()

    def collect(self) -> pd.DataFrame:
        rows: list[dict] = []
        for cat_key, cat in self.cfg["categories"].items():
            events = self.fetch_json(
                f"{GAMMA_BASE}/events",
                params={"closed": "false", "tag_slug": cat["tag_slug"],
                        "order": "volume24hr", "ascending": "false",
                        "limit": self.cfg.get("events_per_category", 25)},
            )
            rows.extend(harvest(events, cat_key, cat))
        rows = place(rows, self.cfg)
        df = pd.DataFrame(rows)
        if df.empty:
            raise RuntimeError("Gamma n'a renvoyé aucun marché actif")
        n_display = int(df["display"].sum())
        expected = sum(c["quota"] for c in self.cfg["categories"].values())
        if n_display != expected:
            self.log.warning("display incomplet", extra={"ctx": {
                "display": n_display, "attendu": expected}})
        return df

    def run(self) -> bool:
        """Un fichier parquet PAR COLLECTE (part-HHMMSS) : la granularité
        temporelle de l'historique des probabilités est la collecte."""
        try:
            df = self.collect()
            now = datetime.now(timezone.utc)
            df["snapshot_ts"] = now
            part_dir = self.data_dir / f"date={now:%Y-%m-%d}"
            part_dir.mkdir(parents=True, exist_ok=True)
            out = part_dir / f"part-{now:%H%M%S}.parquet"
            df.to_parquet(out, index=False)
            self.log.info("run ok", extra={"ctx": {
                "rows": len(df), "display": int(df["display"].sum()),
                "snapshot": now.isoformat(timespec="seconds"), "path": str(out)}})
            return True
        except Exception:
            self.log.error("run failed — donnée précédente conservée (stale)", exc_info=True)
            return False
        finally:
            if self._client is not None:
                self._client.close()
                self._client = None


if __name__ == "__main__":
    raise SystemExit(0 if PmCollector().run() else 1)
