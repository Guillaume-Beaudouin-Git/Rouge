"""Tests microstructure : percentile horaire sur vol construite, lead-lag à
décalage connu, intersection stricte sans ffill."""

from __future__ import annotations

import numpy as np
import pandas as pd

from collectors.micro_builder import hourly_pctls, leadlag


def _m5_days(n_days: int, hot_hour: int | None = None, seed: int = 0,
             hot_last_days: int = 10) -> pd.Series:
    """M5 continu sur n_days ; si hot_hour, cette heure devient 10× plus
    volatile sur les hot_last_days derniers jours."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n_days * 288, freq="5min", tz="UTC")
    sigma = np.full(len(idx), 1e-4)
    if hot_hour is not None:
        last_cut = idx[-1] - pd.Timedelta(days=hot_last_days)
        hot = (idx.hour == hot_hour) & (idx > last_cut)
        sigma[hot] = 1e-3
    rets = rng.normal(0, sigma)
    return pd.Series(100 * np.exp(np.cumsum(rets)), index=idx)


def test_hourly_pctl_detecte_l_heure_chaude() -> None:
    s = _m5_days(300, hot_hour=14)
    pctls = hourly_pctls(s, recent=10, rank_days=252)
    assert len(pctls) == 24
    assert pctls[14] >= 95, f"heure chaude P{pctls[14]}"
    others = [p for h, p in enumerate(pctls) if h != 14]
    assert max(others) <= 90  # le reste n'a pas changé de régime


def test_leadlag_detecte_un_decalage_connu() -> None:
    rng = np.random.default_rng(1)
    idx = pd.date_range("2026-05-01", periods=5000, freq="5min", tz="UTC")
    a = pd.Series(rng.normal(0, 1e-3, len(idx)), index=idx)
    b = a.shift(2) * 0.9 + rng.normal(0, 2e-4, len(idx))  # A mène B de 2 barres
    res = leadlag(a, b.dropna(), max_lag=12, min_overlap=2000)
    assert res is not None
    assert res["lag_bars"] == 2 and res["corr"] > 0.9


def test_leadlag_intersection_stricte_sans_ffill() -> None:
    """B a un trou de session : les barres de A pendant le trou ne doivent
    pas être appariées (pas de ffill) — l'intersection les exclut."""
    rng = np.random.default_rng(2)
    idx = pd.date_range("2026-05-01", periods=6000, freq="5min", tz="UTC")
    a = pd.Series(rng.normal(0, 1e-3, len(idx)), index=idx)
    b = a * 0.8 + rng.normal(0, 3e-4, len(idx))
    hole = (idx >= "2026-05-08") & (idx < "2026-05-11")   # week-end de B
    b = b[~hole]
    res = leadlag(a, b, max_lag=12, min_overlap=2000)
    assert res is not None
    assert res["n"] == len(b)  # intersection = barres où B cote, rien de plus
    assert res["lag_bars"] == 0 and res["corr"] > 0.9


def test_leadlag_chevauchement_insuffisant_ecarte() -> None:
    idx_a = pd.date_range("2026-05-01", periods=3000, freq="5min", tz="UTC")
    idx_b = pd.date_range("2026-06-01", periods=3000, freq="5min", tz="UTC")
    a = pd.Series(np.random.default_rng(3).normal(0, 1e-3, 3000), index=idx_a)
    b = pd.Series(np.random.default_rng(4).normal(0, 1e-3, 3000), index=idx_b)
    assert leadlag(a, b, max_lag=12, min_overlap=2000) is None
