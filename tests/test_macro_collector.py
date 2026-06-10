"""Tests macro FairEconomy : parsing fixture réelle, règles de vintage
(gel du premier print, révisions à part), stats de surprise, fenêtre."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from collectors.macro_collector import (
    CCY_ISO, build_serve, parse_feed, parse_num, series_stats, update_vintage,
)

FIXTURE = json.loads((Path(__file__).resolve().parent / "fixtures" /
                      "ff_thisweek.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 6, 10, 6, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- parsing

def test_parse_num() -> None:
    assert parse_num("5.6%") == 5.6
    assert parse_num("-0.1%") == -0.1
    assert parse_num("3.26T") == 3.26
    assert parse_num("62.4") == 62.4
    assert parse_num("") is None and parse_num(None) is None


def test_parse_feed_fixture() -> None:
    df = parse_feed(FIXTURE, NOW)
    assert len(df) > 40
    assert set(df["ccy"]) <= set(CCY_ISO)          # devises front uniquement
    assert df["tier"].isin([1, 2, 3]).all()        # Holiday/Non-Economic filtrés
    assert (df["dt"].dt.tz is not None) or str(df["dt"].dtype).endswith("UTC]")
    assert df["uid"].is_unique


# ---------------------------------------------------------------- vintage

def _ev(title: str, dt: datetime, cons, prev, ccy: str = "USD") -> dict:
    df = parse_feed([{"title": title, "country": ccy, "impact": "High",
                      "date": dt.isoformat(),
                      "forecast": cons, "previous": prev}], dt)
    return df.iloc[0].to_dict()


def test_vintage_gel_du_premier_print_et_revision() -> None:
    t_may = datetime(2026, 5, 12, 12, 30, tzinfo=timezone.utc)
    t_jun = datetime(2026, 6, 10, 12, 30, tzinfo=timezone.utc)
    # pull 1 (mai) : l'événement CPI de mai apparaît
    v = update_vintage(pd.DataFrame(), pd.DataFrame([_ev("CPI y/y", t_may, "2.0%", "1.9%")]),
                       now=t_may - timedelta(days=1))
    assert v.iloc[0]["actual_first_seen"] is None or pd.isna(v.iloc[0]["actual_first_seen"])
    # pull 2 (juin) : l'occurrence suivante porte previous=2.3 → actual de
    # mai dérivé et GELÉ
    v = update_vintage(v, pd.DataFrame([_ev("CPI y/y", t_jun, "2.1%", "2.3%")]),
                       now=t_jun - timedelta(days=2))
    may = v[v["dt"] == t_may].iloc[0]
    assert float(may["actual_first_seen"]) == 2.3
    assert pd.isna(may["actual_revised"]) or may["actual_revised"] is None
    # pull 3 : previous de juin révisé à 2.4 → actual_revised, premier print intact
    v = update_vintage(v, pd.DataFrame([_ev("CPI y/y", t_jun, "2.1%", "2.4%")]),
                       now=t_jun - timedelta(days=1))
    may = v[v["dt"] == t_may].iloc[0]
    assert float(may["actual_first_seen"]) == 2.3, "premier print écrasé !"
    assert float(may["actual_revised"]) == 2.4


def test_vintage_consensus_gele_apres_publication() -> None:
    t = datetime(2026, 6, 8, 12, 30, tzinfo=timezone.utc)
    v = update_vintage(pd.DataFrame(), pd.DataFrame([_ev("NFP", t, "180", "175")]),
                       now=t - timedelta(days=3))
    # re-pull AVANT publication : consensus mis à jour
    v = update_vintage(v, pd.DataFrame([_ev("NFP", t, "190", "175")]),
                       now=t - timedelta(hours=2))
    assert float(v.iloc[0]["cons"]) == 190
    # re-pull APRÈS publication : consensus gelé
    v = update_vintage(v, pd.DataFrame([_ev("NFP", t, "210", "175")]),
                       now=t + timedelta(hours=4))
    assert float(v.iloc[0]["cons"]) == 190


# ------------------------------------------------------------- surprises

def _vintage_series(surprises: list[float], ccy: str = "USD") -> pd.DataFrame:
    rows = []
    base = datetime(2025, 1, 10, 12, 30, tzinfo=timezone.utc)
    v = pd.DataFrame()
    for i, s in enumerate(surprises):
        dt = base + timedelta(days=30 * i)
        cons = 2.0
        rows.append(_ev("CPI y/y", dt, f"{cons}", "1.9", ccy=ccy))
        rows[-1]["uid"] = f"uid{i}"
    v = update_vintage(pd.DataFrame(), pd.DataFrame(rows), now=base)
    v["actual_first_seen"] = [2.0 + s for s in surprises]
    return v


def test_series_stats_z_et_hit() -> None:
    v = _vintage_series([0.4, -0.2, 0.4, -0.2, 0.4, -0.2])
    z = series_stats(v)
    assert len(z) == 6
    # surprises ±sym : z = s/σ ; 3 positives sur 6
    assert (z["z"] > 0).sum() == 3
    sd = pd.Series([0.4, -0.2, 0.4, -0.2, 0.4, -0.2]).std(ddof=1)
    assert z["z"].max() == pytest.approx(0.4 / sd, rel=1e-6)


def test_series_courte_ignoree() -> None:
    v = _vintage_series([0.4, -0.2])      # < MIN_OBS
    assert series_stats(v).empty


def test_build_serve_fenetre_et_formes() -> None:
    feed = parse_feed(FIXTURE, NOW)
    v = update_vintage(pd.DataFrame(), feed, now=NOW)
    events, scores = build_serve(v, NOW)
    assert len(scores) == 8 and set(scores["ccy"]) == set(CCY_ISO)
    if len(events):
        assert events["d"].between(0, 14).all()
        assert set(events.columns) >= {"d", "dt", "ccy", "iso", "name", "tier",
                                       "time", "prev", "cons", "beatZ", "missZ",
                                       "hit", "n"}
        assert events["dt"].map(lambda s: datetime.fromisoformat(s)).notna().all()
