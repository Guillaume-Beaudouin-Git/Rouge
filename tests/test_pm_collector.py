"""Tests du collecteur Polymarket : harvest/placement sur fixture Gamma
enregistrée (aucun appel réseau), historisation, et vue v_pm."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from api.db import apply_views
from collectors.pm_collector import harvest, load_pm_map, place

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

CFG = load_pm_map()
FED_EVENTS = json.loads((FIXTURES / "gamma_events_fed.json").read_text(encoding="utf-8"))


def _displayable(events: list[dict]) -> int:
    """Événements ayant au moins un livre non décidé (1–99 %)."""
    n = 0
    for e in events:
        prices = []
        for m in e["markets"]:
            if m["active"] and not m["closed"]:
                outcomes = json.loads(m["outcomes"])
                idx = outcomes.index("Yes") if "Yes" in outcomes else 0
                prices.append(float(json.loads(m["outcomePrices"])[idx]) * 100)
        if any(1 <= p <= 99 for p in prices):
            n += 1
    return n


N_DISPLAYABLE = min(CFG["categories"]["fed"]["quota"], _displayable(FED_EVENTS))
#: « maintenant » figé à la date d'enregistrement de la fixture (déterminisme)
NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


def test_harvest_fed_collect_all_et_quota() -> None:
    rows = harvest(FED_EVENTS, "fed", CFG["categories"]["fed"], now=NOW)
    # collect_all : tous les marchés actifs des événements fed sont historisés
    n_active = sum(
        1 for e in FED_EVENTS for m in e["markets"]
        if m["active"] and not m["closed"] and m.get("acceptingOrders", True)
    )
    assert len(rows) == n_active
    displayed = [r for r in rows if r["display"]]
    assert len(displayed) == N_DISPLAYABLE
    assert displayed, "fixture sans événement affichable"
    # un seul marché affiché par événement
    assert len({r["event_slug"] for r in displayed}) == len(displayed)
    for r in rows:
        assert 0 <= r["p"] <= 100
        assert isinstance(r["d"], int)
        assert r["vol_num"] >= 0


def test_harvest_prefere_les_marches_non_decides() -> None:
    rows = harvest(FED_EVENTS, "fed", CFG["categories"]["fed"], now=NOW)
    for r in rows:
        if r["display"]:
            assert 1 <= r["p"] <= 99, f"marché décidé affiché : {r['q']} (p={r['p']})"


def test_harvest_sans_collect_all_ne_garde_que_le_display() -> None:
    cat = dict(CFG["categories"]["fed"], collect_all=False, quota=2)
    rows = harvest(FED_EVENTS, "fed", cat, now=NOW)
    assert len(rows) == 2
    assert all(r["display"] for r in rows)


def test_place_ancre_et_decale() -> None:
    cat = CFG["categories"]["fed"]
    rows = place(harvest(FED_EVENTS, "fed", cat, now=NOW), CFG)
    displayed = [r for r in rows if r["display"]]
    assert displayed[0]["lon"] == cat["anchor"]["lon"]
    assert displayed[1]["lon"] == cat["anchor"]["lon"] + 4.0  # décalage rang 1
    assert [r["ord"] for r in displayed] == list(range(len(displayed)))
    assert all(r["lon"] is None for r in rows if not r["display"])


# ------------------------------------------------------------------- vue

def _snapshot_df(ts: datetime, p_offset: int = 0) -> pd.DataFrame:
    rows = place(
        harvest(FED_EVENTS, "fed", CFG["categories"]["fed"], now=NOW), CFG
    )
    df = pd.DataFrame(rows)
    df["p"] = (df["p"] + p_offset).clip(0, 100)
    df["snapshot_ts"] = ts
    return df


def test_v_pm_sert_le_dernier_snapshot(tmp_path: Path) -> None:
    d = tmp_path / "data" / "pm" / "date=2026-06-10"
    d.mkdir(parents=True)
    old = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)
    new = datetime(2026, 6, 10, 8, 5, tzinfo=timezone.utc)
    _snapshot_df(old).to_parquet(d / "part-080000.parquet", index=False)
    _snapshot_df(new, p_offset=1).to_parquet(d / "part-080500.parquet", index=False)

    con = duckdb.connect()
    apply_views(con, tmp_path)
    rows = con.execute("SELECT q, p, snapshot_ts FROM v_pm ORDER BY ord").fetchall()
    assert len(rows) == N_DISPLAYABLE
    assert all(r[2].astimezone(timezone.utc) == new for r in rows)
    # l'historique reste interrogeable : deux points par marché display
    n_hist = con.execute(
        "SELECT count(*) FROM v_pm_raw WHERE display"
    ).fetchone()[0]
    assert n_hist == 2 * N_DISPLAYABLE


def test_harvest_ecarte_les_marches_expires() -> None:
    far_future = datetime(2030, 1, 1, tzinfo=timezone.utc)
    rows = harvest(FED_EVENTS, "fed", CFG["categories"]["fed"], now=far_future)
    # tous les endDate de la fixture sont passés → rien d'affichable,
    # mais collect_all continue d'historiser
    assert not any(r["display"] for r in rows)
    assert rows
