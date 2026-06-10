"""Tests quotes→daily (convention 17:00 NY) et constructeur TREND.
Données synthétiques uniquement — le lake réel est couvert par
test_quotes_schema.py."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from collectors.quotes_collector import m5_to_daily, sessionize
from collectors.trend_builder import (
    build_rows, mom_series, pos_score, risk_series,
)

UTC = "UTC"


# ------------------------------------------------- agrégation M5 → daily

def _m5(start: str, end: str, tz: str = "Europe/Prague") -> pd.Series:
    idx = pd.date_range(start, end, freq="5min", tz=tz)
    return pd.Series(np.linspace(100, 110, len(idx)), index=idx)


def test_sessionize_borne_17h_new_york() -> None:
    # mercredi 4 juin 2026, heure NY (EDT) explicite
    idx = pd.DatetimeIndex([
        "2026-06-03 16:55", "2026-06-03 17:00", "2026-06-03 23:00",
        "2026-06-04 09:30", "2026-06-04 16:55", "2026-06-04 17:00",
    ]).tz_localize("America/New_York")
    sessions = sessionize(idx)
    # barre démarrant à 16:55 → session du jour ; à 17:00 → lendemain
    assert [str(s.date()) for s in sessions] == [
        "2026-06-03", "2026-06-04", "2026-06-04",
        "2026-06-04", "2026-06-04", "2026-06-05",
    ]


def test_m5_to_daily_close_est_la_derniere_barre_avant_17h_ny() -> None:
    s = _m5("2026-06-01 00:00", "2026-06-05 23:00")  # lun → ven, tz Prague
    daily = m5_to_daily(s)
    assert daily.index.dayofweek.max() < 5
    # close de la session du 2026-06-03 = barre de 22:55 Prague (16:55 NY)
    expected = s[s.index.tz_convert("America/New_York")
                 < pd.Timestamp("2026-06-03 17:00", tz="America/New_York")].iloc[-1]
    assert daily.loc[pd.Timestamp("2026-06-03"), "close"] == expected
    assert "ret" in daily.columns


def test_m5_to_daily_refuse_index_naif() -> None:
    s = pd.Series([1.0], index=pd.DatetimeIndex(["2026-06-03 12:00"]))
    with pytest.raises(ValueError, match="tz-aware"):
        m5_to_daily(s)


# ------------------------------------------------------------ composantes

def _daily_close(values: np.ndarray) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=idx)


def test_mom_series_signe_et_bornes() -> None:
    n = 600
    up = _daily_close(100 * np.exp(np.linspace(0, 0.5, n) + np.random.default_rng(7).normal(0, 0.002, n).cumsum()))
    m = mom_series(up).dropna()
    assert m.iloc[-1] > 10
    assert m.abs().max() <= 50
    down = _daily_close(100 * np.exp(np.linspace(0, -0.5, n) + np.random.default_rng(7).normal(0, 0.002, n).cumsum()))
    assert mom_series(down).dropna().iloc[-1] < -10


def test_risk_series_stress_negatif() -> None:
    rng = np.random.default_rng(11)
    calm = rng.normal(0, 0.002, 500)
    stressed = rng.normal(0, 0.02, 60)
    close = _daily_close(100 * np.exp(np.concatenate([calm, stressed]).cumsum()))
    r = risk_series(close).dropna()
    # vol dans le haut de sa fenêtre 1 an (les 60 jours stressés se
    # classent entre eux : rang ~0.85, pas 1.0) → nettement négatif
    assert r.iloc[-1] < -25
    assert r.abs().max() <= 50


def test_pos_score_direct_base_quote_croisee() -> None:
    pctl = {"GOLD": 80.0, "EUR FX": 70.0, "JPY FX": 10.0, "CAD FX": 30.0}
    links = {
        "GOLD": {"direct": "GOLD"},
        "EURUSD": {"base": "EUR FX"},
        "USDCAD": {"quote": "CAD FX"},
        "EURJPY": {"base": "EUR FX", "quote": "JPY FX"},
    }
    assert pos_score("GOLD", pctl, links) == (30.0, True)
    assert pos_score("EURUSD", pctl, links) == (20.0, True)
    assert pos_score("USDCAD", pctl, links) == (20.0, True)   # spec short CAD → long USDCAD
    assert pos_score("EURJPY", pctl, links) == (30.0, True)   # (20 − (−40)) / 2
    assert pos_score("NZDUSD", pctl, links) == (0.0, False)   # pas de lien
    assert pos_score("SPX", pctl, {"SPX": {"direct": "E-MINI SPX"}}) == (0.0, False)  # contrat absent


# ------------------------------------------------------------- build_rows

def _mini_cfg() -> dict:
    return {
        "instruments": [
            {"sym": "GOLD", "source": "m5", "file": "XAUUSD"},
            {"sym": "NKY", "excluded": "absent du lake"},
        ],
        "cot_links": {"GOLD": {"direct": "GOLD"}},
    }


def _statics() -> dict:
    return {
        "GOLD": {"sym": "GOLD", "cat": "MET", "name": "Or (spot)", "f1": None, "f2": None},
        "NKY": {"sym": "NKY", "cat": "IND", "name": "Nikkei 225", "f1": "JP", "f2": None},
    }


def test_build_rows_live_et_exclu() -> None:
    rng = np.random.default_rng(3)
    n = 600
    close = _daily_close(100 * np.exp(np.linspace(0, 0.3, n) + rng.normal(0, 0.006, n).cumsum()))
    daily = {"GOLD": close.rename("close").reset_index().rename(columns={"index": "session"})}
    df = build_rows(daily, {"GOLD": 80.0}, _mini_cfg(), _statics())

    assert len(df) == 2 and list(df["g"]) == sorted(df["g"], reverse=True)
    gold = df[df["sym"] == "GOLD"].iloc[0]
    assert gold["live"] and gold["pos_available"]
    assert gold["pos"] == 30.0 and gold["mac"] == 0.0 and gold["flow"] == 0.0
    # g respecte la formule du front sur composantes arrondies (±0.15)
    expected = 0.35 * gold["mom"] + 0.20 * gold["mac"] + 0.15 * (gold["pos"] + gold["risk"] + gold["flow"])
    assert abs(gold["g"] - expected) < 0.15
    assert 0 <= gold["d30"] <= 100

    nky = df[df["sym"] == "NKY"].iloc[0]
    assert not nky["live"]
    assert nky[["g", "mom", "mac", "pos", "risk", "flow", "chg"]].eq(0).all()
    assert nky["d30"] == 50


def test_m5_to_daily_droppe_la_session_finale_incomplete() -> None:
    # semaine pleine puis quelques barres du dimanche soir (19:00-20:00 NY)
    # rattachées à la session du lundi → cette session doit être droppée
    week = _m5("2026-06-01 00:00", "2026-06-05 23:00")
    sunday = pd.Series(
        [110.0] * 13,
        index=pd.date_range("2026-06-07 19:00", "2026-06-07 20:00",
                            freq="5min", tz="America/New_York"),
    ).tz_convert("Europe/Prague")
    daily = m5_to_daily(pd.concat([week, sunday]))
    assert str(daily.index.max().date()) == "2026-06-05"
