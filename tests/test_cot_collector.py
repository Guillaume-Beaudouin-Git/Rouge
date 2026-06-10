"""Tests du collecteur COT : mapping, parsing sur fixtures Socrata
enregistrées (aucun appel réseau), et calculs de la vue v_cot."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import yaml

from api.db import apply_views
from collectors.cot_collector import MAP_PATH, load_cot_map, normalize

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------- mapping

def test_mapping_couvre_les_16_contrats_du_front() -> None:
    cfg = load_cot_map()  # lève si un contrat du front manque
    assert len(cfg["contracts"]) == 16
    front = {r["name"] for r in json.loads((REPO_ROOT / "api/demo/cot.json").read_text())}
    assert {c["sym"] for c in cfg["contracts"]} == front


def test_mapping_incomplet_echoue_explicitement(tmp_path: Path) -> None:
    cfg = yaml.safe_load(MAP_PATH.read_text(encoding="utf-8"))
    cfg["contracts"] = cfg["contracts"][:-1]  # retire VIX FUT
    partial = tmp_path / "cot_map.yaml"
    partial.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(RuntimeError, match="VIX FUT"):
        load_cot_map(partial)


# ---------------------------------------------------------------- parsing

def test_normalize_disaggregated_fixture() -> None:
    cfg = load_cot_map()
    rows = json.loads((FIXTURES / "socrata_disaggregated.json").read_text())
    df = normalize(rows, cfg, "disaggregated")
    assert not df.empty
    assert set(df.columns) == {"sym", "iso", "ord", "dataset", "code", "category",
                               "report_date", "long", "short", "net", "open_interest"}
    assert set(df["sym"]) <= {"GOLD", "WTI"}
    assert (df["net"] == df["long"] - df["short"]).all()
    assert df["report_date"].map(lambda d: isinstance(d, date)).all()


def test_normalize_tff_fixture() -> None:
    cfg = load_cot_map()
    rows = json.loads((FIXTURES / "socrata_tff.json").read_text())
    df = normalize(rows, cfg, "tff")
    assert set(df["sym"]) <= {"EUR FX", "VIX FUT"}
    eur = df[df["sym"] == "EUR FX"].iloc[0]
    assert eur["iso"] == "EU" and eur["net"] == eur["long"] - eur["short"]


def test_normalize_ignore_les_contrats_non_mappes() -> None:
    cfg = load_cot_map()
    rows = [{"cftc_contract_market_code": "999999",
             "report_date_as_yyyy_mm_dd": "2026-06-02T00:00:00.000",
             "m_money_positions_long_all": "1", "m_money_positions_short_all": "2",
             "open_interest_all": "3"}]
    assert normalize(rows, cfg, "disaggregated").empty


# ------------------------------------------------------------------- vue

def _build_lake(tmp_path: Path, series: dict[str, list[int]]) -> Path:
    """Écrit un mini lake COT : une partition par semaine (mardis)."""
    weeks = max(len(v) for v in series.values())
    dates = pd.date_range(end="2026-06-02", periods=weeks, freq="7D")
    for i, d in enumerate(dates):
        rows = [
            {"sym": sym, "iso": None, "ord": j, "category": "test",
             "report_date": d.date(), "net": nets[i],
             "long": max(nets[i], 0), "short": max(-nets[i], 0), "open_interest": 0}
            for j, (sym, nets) in enumerate(series.items()) if i < len(nets)
        ]
        part = tmp_path / "data" / "cot" / f"date={d.date()}"
        part.mkdir(parents=True)
        pd.DataFrame(rows).to_parquet(part / "part.parquet", index=False)
    return tmp_path


def _query_v_cot(lake_root: Path) -> list[dict]:
    con = duckdb.connect()
    apply_views(con, lake_root)
    cur = con.execute("SELECT * FROM v_cot ORDER BY ord")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def test_v_cot_pctl_z_dwk_crowd(tmp_path: Path) -> None:
    # 160 semaines : net croissant par pas de 1000 → dernier point = max
    rising = [i * 1000 for i in range(160)]
    # série plate à 100 puis chute à 0 → dernier point = min, z négatif
    crashing = [100] * 159 + [0]
    rows = _query_v_cot(_build_lake(tmp_path, {"RISING": rising, "CRASHING": crashing}))
    by = {r["name"]: r for r in rows}

    r = by["RISING"]
    assert r["pctl"] == 100 and r["crowd"] is True
    assert r["dwk"] == pytest.approx(1.0)  # +1000 contrats = +1.0 k
    assert r["z"] > 1.5

    c = by["CRASHING"]
    # fenêtre 3 ans = 156 dernières semaines (1 seul zéro sur 156) → P1
    assert c["pctl"] <= 1 and c["crowd"] is True
    assert c["dwk"] == pytest.approx(-0.1)
    assert c["z"] < -5

    assert all(r["report_date"] == date(2026, 6, 2) for r in rows)


def test_v_cot_fenetre_3_ans_exclut_le_passe_lointain(tmp_path: Path) -> None:
    # 200 semaines : 44 anciennes très hautes (10000), 156 récentes à 0
    # sauf le dernier point à 1 → percentile ~100 SI la fenêtre est bien
    # bornée à 3 ans (sinon les 10000 anciens l'écraseraient vers ~78)
    series = [10_000] * 44 + [0] * 155 + [1]
    rows = _query_v_cot(_build_lake(tmp_path, {"WINDOWED": series}))
    assert rows[0]["pctl"] == 100
    assert rows[0]["n_weeks"] == 156
