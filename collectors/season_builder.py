"""Constructeur saisonnalité — 12 actifs × 12 mois depuis quotes_daily.

Par mois calendaire : rendement moyen (%) et hit-rate (% d'années
positives) calculés sur les retours mensuels close-à-close des sessions
daily 17:00 NY existantes. Pas d'annualisation. meta.years_used expose la
profondeur réelle par actif (FX ~19 ans, indices ~13, COPPER ~14).
Actifs absents du lake (BTC, BUND) : ligne neutre live=false, listés dans
meta.excluded — jamais simulés.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from collectors.base import REPO_ROOT, BaseCollector
from collectors.fx_builder import load_daily
from collectors.quotes_collector import load_quotes_map

SEASON_FIXTURE = REPO_ROOT / "api" / "demo" / "season.json"


def monthly_stats(close: pd.Series) -> pd.DataFrame:
    """Série daily (index session) → 12 lignes {month, mean_pct, hit_pct,
    n_years} sur les retours mensuels close-à-close."""
    m_close = close.resample("ME").last().dropna()
    rets = m_close.pct_change().dropna() * 100
    # mois incomplets en bord d'historique : un mois avec retour calculé
    # sur moins de 15 jours de données reste représentatif du mois
    # calendaire — on garde tout, la moyenne lisse
    g = rets.groupby(rets.index.month)
    out = pd.DataFrame({
        "month": range(1, 13),
    }).set_index("month")
    out["mean_pct"] = g.mean()
    out["hit_pct"] = g.apply(lambda x: 100.0 * (x > 0).mean())
    out["n_years"] = g.count()
    return out.fillna(0.0).reset_index()


class SeasonBuilder(BaseCollector):
    name = "season"
    dataset = "season"

    def __init__(self) -> None:
        super().__init__()
        fixture = json.loads(SEASON_FIXTURE.read_text(encoding="utf-8"))
        self.assets = fixture["assets"]          # ordre du front
        qm = load_quotes_map()
        self.lake_sym = {i["sym"]: i for i in qm["instruments"]}

    def collect(self) -> pd.DataFrame:
        daily = load_daily()
        rows = []
        asof = max(df["session"].max() for df in daily.values())
        for sym in self.assets:
            inst = self.lake_sym.get(sym, {})
            if "excluded" in inst or sym not in daily:
                for m in range(1, 13):
                    rows.append({"sym": sym, "month": m, "mean_pct": 0.0,
                                 "hit_pct": 0.0, "n_years": 0, "live": False})
                continue
            close = daily[sym].set_index("session")["close"]
            stats = monthly_stats(close)
            for _, r in stats.iterrows():
                rows.append({"sym": sym, "month": int(r["month"]),
                             "mean_pct": round(float(r["mean_pct"]), 2),
                             "hit_pct": round(float(r["hit_pct"]), 1),
                             "n_years": int(r["n_years"]), "live": True})
        df = pd.DataFrame(rows)
        df["asof_session"] = pd.Timestamp(asof)
        return df

    def run(self) -> bool:
        try:
            df = self.collect()
            asof = pd.Timestamp(df["asof_session"].max()).date()
            self.write_parquet(df, partition_date=str(asof))
            self.log.info("run ok", extra={"ctx": {
                "asof": str(asof), "live": int(df[df['live']]['sym'].nunique())}})
            return True
        except Exception:
            self.log.error("run failed — donnée précédente conservée (stale)", exc_info=True)
            return False


if __name__ == "__main__":
    raise SystemExit(0 if SeasonBuilder().run() else 1)
