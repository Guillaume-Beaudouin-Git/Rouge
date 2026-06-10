"""Constructeur microstructure — vol horaire + lead-lag depuis le M5 du
lake Dukascopy (lecture seule).

Vol horaire : somme des r² intra-heure (log-returns M5, heures UTC) ;
valeur courante = moyenne des N dernières sessions de la même heure ;
percentile vs distribution 1 an de la même heure du même actif.
Lead-lag : cross-corrélation des rendements M5 à ±k barres, calculée sur
l'intersection stricte des timestamps (jamais de ffill à travers les gaps).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from collectors.base import REPO_ROOT, BaseCollector
from collectors.quotes_collector import load_quotes_map

MAP_PATH = REPO_ROOT / "api" / "config" / "micro_map.yaml"
MICRO_FIXTURE = REPO_ROOT / "api" / "demo" / "micro.json"


def load_micro_map(path: Path = MAP_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    for sect, fields in (("hourly", ("rv_recent_sessions", "rv_rank_days")),
                         ("leadlag", ("window_sessions", "max_lag_bars",
                                      "min_overlap_bars", "pairs"))):
        for f in fields:
            if f not in cfg.get(sect, {}):
                raise RuntimeError(f"micro_map.yaml : champ '{sect}.{f}' manquant")
    return cfg


def hourly_pctls(m5_close: pd.Series, recent: int, rank_days: int) -> list[int]:
    """Série M5 (index tz-aware) → 24 percentiles de vol horaire UTC."""
    s = m5_close.tz_convert("UTC")
    r2 = np.log(s).diff().pow(2)
    day, hour = r2.index.date, r2.index.hour
    rv = r2.groupby([day, hour]).sum()
    rv.index.names = ["day", "hour"]
    out = []
    for h in range(24):
        try:
            hist = rv.xs(h, level="hour").sort_index().tail(rank_days)
        except KeyError:
            out.append(0)
            continue
        if len(hist) < recent + 5:
            out.append(0)
            continue
        cur = hist.tail(recent).mean()
        out.append(int(round(100 * (hist <= cur).mean())))
    return out


def leadlag(ra: pd.Series, rb: pd.Series, max_lag: int,
            min_overlap: int) -> dict | None:
    """Cross-corrélation aux lags ±max_lag sur l'intersection stricte des
    timestamps. → {lag_bars, corr, n} (lag>0 : A mène B), None si
    chevauchement insuffisant."""
    j = pd.concat([ra.rename("a"), rb.rename("b")], axis=1, join="inner",
                  sort=True).dropna()
    if len(j) < min_overlap:
        return None
    best = (0, j["a"].corr(j["b"]))
    for k in range(1, max_lag + 1):
        # corr(a_t, b_{t+k}) : A mène B de k barres — décalage en position
        # APRÈS intersection (les gaps de session ne sont pas traversés
        # par un ffill ; un shift positionnel sur la grille commune)
        ca = j["a"].iloc[:-k].to_numpy()
        cb = j["b"].iloc[k:].to_numpy()
        c1 = float(np.corrcoef(ca, cb)[0, 1])
        cb2 = j["b"].iloc[:-k].to_numpy()
        ca2 = j["a"].iloc[k:].to_numpy()
        c2 = float(np.corrcoef(cb2, ca2)[0, 1])
        for k_signed, c in ((k, c1), (-k, c2)):
            if abs(c) > abs(best[1]):
                best = (k_signed, c)
    return {"lag_bars": best[0], "corr": round(best[1], 2), "n": len(j)}


class MicroBuilder(BaseCollector):
    name = "micro"
    dataset = "micro_hours"

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_micro_map()
        self.assets = [a["a"] for a in
                       json.loads(MICRO_FIXTURE.read_text(encoding="utf-8"))["assets"]]
        qm = load_quotes_map()
        self.inst = {i["sym"]: i for i in qm["instruments"]}
        self.m5_dir = qm["m5_dir"]
        self.m1_dir = qm["m1_dir"]

    def _load_m5(self, sym: str, days: int) -> pd.Series | None:
        inst = self.inst.get(sym)
        if not inst or "excluded" in inst:
            return None
        cut = pd.Timestamp(date.today() - timedelta(days=days), tz="UTC")
        if inst["source"] == "m5":
            s = pd.read_parquet(self.m5_dir / f"{inst['file']}_m5_close.parquet")["close"]
            return s[s.index >= cut]
        # COPPER : M1 annuel → rééchantillonné M5 (close de la 5e minute)
        frames = []
        for year in range(cut.year, date.today().year + 1):
            p = self.m1_dir / f"{inst['file']}_{year}.parquet"
            if p.exists():
                frames.append(pd.read_parquet(p, columns=["ts_utc", "close"]))
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True).sort_values("ts_utc")
        s = pd.Series(df["close"].values,
                      index=pd.DatetimeIndex(df["ts_utc"]).tz_localize("UTC"))
        s = s[s.index >= cut]
        return s.resample("5min").last().dropna()

    def collect(self) -> pd.DataFrame:
        h = self.cfg["hourly"]
        ll = self.cfg["leadlag"]
        rank_horizon = int(h["rv_rank_days"] * 1.6)  # marge week-ends
        hours_rows, m5_cache = [], {}
        asof = None
        for sym in self.assets:
            s = self._load_m5(sym, rank_horizon)
            if s is None or s.empty:
                for hh in range(24):
                    hours_rows.append({"a": sym, "hour": hh, "pctl": 0, "live": False})
                continue
            m5_cache[sym] = s
            asof = max(asof or s.index.max(), s.index.max())
            for hh, p in enumerate(hourly_pctls(s, h["rv_recent_sessions"], h["rv_rank_days"])):
                hours_rows.append({"a": sym, "hour": hh, "pctl": p, "live": True})

        ll_rows = []
        cut_ll = (asof or pd.Timestamp.now(tz="UTC")) - pd.Timedelta(days=int(ll["window_sessions"] * 1.5))
        for a, b in ll["pairs"]:
            sa, sb = m5_cache.get(a), m5_cache.get(b)
            if sa is None or sb is None:
                ll_rows.append({"pair": f"{a} → {b}", "lag": None, "corr": None,
                                "n": 0, "live": False})
                continue
            ra = np.log(sa[sa.index >= cut_ll].tz_convert("UTC")).diff().dropna()
            rb = np.log(sb[sb.index >= cut_ll].tz_convert("UTC")).diff().dropna()
            res = leadlag(ra, rb, ll["max_lag_bars"], ll["min_overlap_bars"])
            if res is None:
                ll_rows.append({"pair": f"{a} → {b}", "lag": None, "corr": None,
                                "n": 0, "live": False})
                continue
            lead, lag_b = (a, b) if res["lag_bars"] >= 0 else (b, a)
            ll_rows.append({"pair": f"{lead} → {lag_b}",
                            "lag": f"{max(abs(res['lag_bars']), 1) * 5} min"
                                   if res["lag_bars"] != 0 else "0 min",
                            "corr": res["corr"], "n": res["n"], "live": True})

        hours = pd.DataFrame(hours_rows)
        lldf = pd.DataFrame(ll_rows)
        ts = pd.Timestamp(asof).tz_convert("UTC").tz_localize(None).normalize()
        # ancrage sur la session globale (même asof que TREND) : les ticks
        # du dimanche soir (COPPER M1) ne constituent pas une session
        while ts.dayofweek >= 5:
            ts -= pd.Timedelta(days=1)
        hours["asof_session"] = ts
        lldf["asof_session"] = ts
        self._leadlag = lldf
        return hours

    def run(self) -> bool:
        try:
            hours = self.collect()
            asof = pd.Timestamp(hours["asof_session"].max()).date()
            for df, ds in ((hours, "micro_hours"), (self._leadlag, "micro_leadlag")):
                part_dir = REPO_ROOT / "data" / ds / f"date={asof}"
                part_dir.mkdir(parents=True, exist_ok=True)
                df.to_parquet(part_dir / "part.parquet", index=False)
            self.log.info("run ok", extra={"ctx": {
                "asof": str(asof),
                "live_assets": int(hours[hours['live']]['a'].nunique()),
                "leadlag_ok": int(self._leadlag["live"].sum())}})
            return True
        except Exception:
            self.log.error("run failed — donnée précédente conservée (stale)", exc_info=True)
            return False


if __name__ == "__main__":
    raise SystemExit(0 if MicroBuilder().run() else 1)
