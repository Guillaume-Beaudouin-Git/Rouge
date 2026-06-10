"""Constructeur TDI — 12 divergences pair-based depuis quotes_daily.

Méthode unique (tdi_map.yaml) : spread = log(A) − log(B) sur les sessions
communes, z = (spread courant − moyenne fenêtre) / écart-type fenêtre.
Jambe absente du lake → ligne excluded explicite (z neutre, note dédiée),
jamais de substitution silencieuse.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from collectors.base import REPO_ROOT, BaseCollector
from collectors.fx_builder import load_daily

MAP_PATH = REPO_ROOT / "api" / "config" / "tdi_map.yaml"


def load_tdi_map(path: Path = MAP_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    div = cfg.get("divergences") or []
    if len(div) != 12:
        raise RuntimeError(f"tdi_map.yaml : 12 divergences attendues, {len(div)} trouvées")
    for d in div:
        for f in ("flux", "met", "a", "b"):
            if f not in d:
                raise RuntimeError(f"tdi_map.yaml : champ '{f}' manquant ({d.get('met', '?')})")
    return cfg


def spread_z(a: pd.Series, b: pd.Series, window: int) -> tuple[float, int]:
    """z du log-spread courant vs sa fenêtre. → (z, n_sessions_communes)."""
    j = pd.concat([np.log(a).rename("a"), np.log(b).rename("b")],
                  axis=1, sort=True).dropna()
    spread = j["a"] - j["b"]
    if len(spread) < window + 1:
        raise ValueError(f"historique commun insuffisant ({len(spread)} sessions)")
    win = spread.tail(window + 1)
    base = win.iloc[:-1]
    sd = base.std()
    z = float((win.iloc[-1] - base.mean()) / sd) if sd > 0 else 0.0
    return z, len(spread)


def note_for(d: dict, z: float, window: int) -> str:
    sens = "tendu" if z > 0 else "comprimé"
    return f"{d['a']} vs {d['b']} : spread log {sens} ({abs(z):.1f}σ vs baseline {window} sessions)"


def build_tdi(daily: dict[str, pd.DataFrame], cfg: dict) -> tuple[pd.DataFrame, dict]:
    window = cfg.get("window", 60)
    legs = {leg for d in cfg["divergences"] for leg in (d["a"], d["b"])}
    missing = sorted(l for l in legs if l not in daily)
    rows = []
    for d in cfg["divergences"]:
        w = d.get("window", window)
        if d["a"] in daily and d["b"] in daily:
            z, _ = spread_z(daily[d["a"]].set_index("session")["close"],
                            daily[d["b"]].set_index("session")["close"], w)
            rows.append({"flux": d["flux"], "met": d["met"], "z": round(z, 2),
                         "note": note_for(d, z, w), "live": True})
        else:
            gone = [l for l in (d["a"], d["b"]) if l not in daily]
            rows.append({"flux": d["flux"], "met": d["met"], "z": 0.0,
                         "note": f"jambe absente du lake : {', '.join(gone)}",
                         "live": False})
    df = pd.DataFrame(rows).sort_values("z", key=lambda s: s.abs(),
                                        ascending=False).reset_index(drop=True)
    diag = {"legs": len(legs), "legs_missing": missing}
    return df, diag


class TdiBuilder(BaseCollector):
    name = "tdi"
    dataset = "tdi"

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_tdi_map()

    def collect(self) -> pd.DataFrame:
        daily = load_daily()
        df, diag = build_tdi(daily, self.cfg)
        asof = max(d["session"].max() for d in daily.values())
        df["asof_session"] = pd.Timestamp(asof)
        self.log.info("couverture jambes", extra={"ctx": diag})
        return df

    def run(self) -> bool:
        try:
            df = self.collect()
            asof = pd.Timestamp(df["asof_session"].max()).date()
            self.write_parquet(df, partition_date=str(asof))
            self.log.info("run ok", extra={"ctx": {
                "asof": str(asof), "live": int(df["live"].sum())}})
            return True
        except Exception:
            self.log.error("run failed — donnée précédente conservée (stale)", exc_info=True)
            return False


if __name__ == "__main__":
    raise SystemExit(0 if TdiBuilder().run() else 1)
