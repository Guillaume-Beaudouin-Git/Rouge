"""Tests de contrat P1 : enveloppe uniforme + forme champ par champ de
chaque endpoint (schémas relevés dans frontend/rouge.html, section données
démo), + présence du fallback démo par module dans le front."""

from __future__ import annotations

import re
from datetime import datetime
from numbers import Number
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONT = (REPO_ROOT / "frontend" / "rouge.html").read_text(encoding="utf-8")

client = TestClient(app)

ENDPOINTS = [
    "/api/monitor/layers",
    "/api/intel/cot",
    "/api/intel/macro",
    "/api/intel/trend",
    "/api/intel/fx",
    "/api/intel/markets",
    "/api/intel/pm",
    "/api/intel/season",
    "/api/intel/tdi",
    "/api/intel/micro",
]


def get_data(endpoint: str):
    r = client.get(endpoint)
    assert r.status_code == 200
    return r.json()["data"]


def is_num(x) -> bool:
    return isinstance(x, Number) and not isinstance(x, bool)


# ---------------------------------------------------------------- enveloppe

@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_envelope(endpoint: str) -> None:
    r = client.get(endpoint)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"data", "meta"}
    # enveloppe minimale garantie ; les endpoints live peuvent enrichir
    # meta (components, excluded…)
    assert {"source", "asof", "stale"} <= set(body["meta"])
    assert isinstance(body["meta"]["source"], str)
    assert isinstance(body["meta"]["stale"], bool)
    datetime.fromisoformat(body["meta"]["asof"])  # iso8601 valide


# ------------------------------------------------------------ monitor/layers

def test_layers_shape() -> None:
    data = get_data("/api/monitor/layers")
    assert set(data) == {"news", "pm", "ais", "mil", "choke", "zones"}
    for z in data["zones"]:
        assert set(z) == {"name", "lon", "lat", "r", "lvl"}
        assert z["lvl"] in {"CRITIQUE", "ÉLEVÉ", "MOYEN"}
    for n in data["news"]:  # [lon, lat, intensité, titre, (source, ts si live)]
        assert len(n) >= 4 and is_num(n[0]) and is_num(n[1]) and is_num(n[2]) and isinstance(n[3], str)
    for s in data["ais"]:
        assert set(s) == {"lon", "lat", "type"} and s["type"] in {"TANKER", "CARGO"}
    for m in data["mil"]:  # [lon, lat, libellé]
        assert len(m) == 3 and is_num(m[0]) and is_num(m[1]) and isinstance(m[2], str)
    for k in data["choke"]:  # [nom, lon, lat]
        assert len(k) == 3 and isinstance(k[0], str) and is_num(k[1]) and is_num(k[2])
    for p in data["pm"]:
        assert set(p) == {"q", "p", "d", "vol", "lon", "lat"}


# ------------------------------------------------------------------ intel/cot

def test_cot_shape() -> None:
    rows = get_data("/api/intel/cot")
    assert len(rows) == 16
    for c in rows:
        assert set(c) == {"name", "iso", "pctl", "z", "dwk", "crowd"}
        assert isinstance(c["name"], str)
        assert c["iso"] is None or (isinstance(c["iso"], str) and len(c["iso"]) == 2)
        assert is_num(c["pctl"]) and 0 <= c["pctl"] <= 100
        assert is_num(c["z"]) and is_num(c["dwk"])
        assert isinstance(c["crowd"], bool)
        assert c["crowd"] == (c["pctl"] >= 90 or c["pctl"] <= 10)


# ---------------------------------------------------------------- intel/macro

def test_macro_shape() -> None:
    data = get_data("/api/intel/macro")
    assert set(data) == {"events", "scores"}
    assert data["events"], "calendrier vide"
    for e in data["events"]:
        assert set(e) == {"d", "dt", "ccy", "iso", "name", "tier", "time",
                          "prev", "cons", "beatZ", "missZ", "hit", "n"}
        datetime.fromisoformat(e["dt"].replace("Z", "+00:00"))
        assert e["tier"] in {1, 2, 3}
        assert re.fullmatch(r"\d{2}:\d{2}", e["time"])
        assert e["cons"] is None or is_num(e["cons"])
    assert len(data["scores"]) == 8
    for s in data["scores"]:
        assert set(s) == {"ccy", "iso", "next", "beatZ", "missZ", "beatPct",
                          "n", "mom", "streak", "top"}


# ---------------------------------------------------------------- intel/trend

def test_trend_shape() -> None:
    rows = get_data("/api/intel/trend")
    assert len(rows) == 26
    for t in rows:
        assert set(t) == {"cat", "sym", "name", "f1", "f2", "g", "mom", "mac",
                          "pos", "risk", "flow", "d30", "chg"}
        assert t["cat"] in {"FX", "IND", "MET", "ENE", "BND", "CRY"}
        # score composite : 0.35·mom + 0.20·mac + 0.15·(pos+risque+flux)
        expected = 0.35 * t["mom"] + 0.20 * t["mac"] + 0.15 * (t["pos"] + t["risk"] + t["flow"])
        assert abs(t["g"] - expected) < 0.15, f"{t['sym']}: g={t['g']} vs {expected:.2f}"
    gs = [t["g"] for t in rows]
    assert gs == sorted(gs, reverse=True), "TREND doit être trié par score décroissant"


# ------------------------------------------------------------------- intel/fx

def test_fx_shape() -> None:
    data = get_data("/api/intel/fx")
    assert set(data) == {"strength", "pairs"}
    assert len(data["strength"]) == 8
    for c in data["strength"]:
        assert set(c) == {"c", "iso", "now", "s"}
        assert len(c["s"]) == 14 and c["now"] == c["s"][13]
    assert len(data["pairs"]) == 28
    for p in data["pairs"]:
        assert set(p) == {"p", "b", "q", "diff", "trend", "conflict"}
        assert p["trend"] in {-1, 1}
        assert p["p"] == p["b"] + p["q"]
        assert isinstance(p["conflict"], bool)
    diffs = [abs(p["diff"]) for p in data["pairs"]]
    assert diffs == sorted(diffs, reverse=True), "PAIRS triées par |diff| décroissant"


# -------------------------------------------------------------- intel/markets

def test_markets_shape() -> None:
    rows = get_data("/api/intel/markets")
    assert len(rows) == 6
    for m in rows:
        assert set(m) == {"k", "v", "d", "s"}
        assert isinstance(m["k"], str) and isinstance(m["v"], str)
        assert is_num(m["d"]) and isinstance(m["s"], str)


# ------------------------------------------------------------------- intel/pm

def test_pm_shape() -> None:
    rows = get_data("/api/intel/pm")
    assert len(rows) == 8
    for m in rows:
        assert set(m) == {"q", "p", "d", "vol", "lon", "lat"}
        assert is_num(m["p"]) and 0 <= m["p"] <= 100
        assert isinstance(m["vol"], str)


# --------------------------------------------------------------- intel/season

def test_season_shape() -> None:
    data = get_data("/api/intel/season")
    assert set(data) >= {"assets", "bias"}
    assert len(data["assets"]) == 12
    assert set(data["bias"]) == set(data["assets"])
    for sym, months in data["bias"].items():
        assert len(months) == 12 and all(is_num(v) for v in months)


# ------------------------------------------------------------------ intel/tdi

def test_tdi_shape() -> None:
    rows = get_data("/api/intel/tdi")
    assert len(rows) == 12
    for t in rows:
        assert set(t) == {"flux", "met", "z", "note"}
        assert isinstance(t["flux"], str) and isinstance(t["met"], str)
        assert is_num(t["z"]) and isinstance(t["note"], str)
    zs = [abs(t["z"]) for t in rows]
    assert zs == sorted(zs, reverse=True), "TDI trié par |z| décroissant"


# ---------------------------------------------------------------- intel/micro

def test_micro_shape() -> None:
    data = get_data("/api/intel/micro")
    assert set(data) >= {"assets", "leadlag"}
    assert len(data["assets"]) == 20
    for a in data["assets"]:
        assert set(a) >= {"a", "row"}
        assert len(a["row"]) == 24
        assert all(is_num(v) and 0 <= v <= 100 for v in a["row"])
    for l in data["leadlag"]:
        assert set(l) >= {"pair", "lag", "corr"}
        assert is_num(l["corr"]) and -1 <= l["corr"] <= 1


# -------------------------------------------------------- front : same-origin

def test_front_served_at_root() -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "ROUGE" in r.text and "loadData" in r.text


# ------------------------------------------------- front : fallback par module

FALLBACKS = [
    r"loadData\('/api/monitor/layers',\{news:NEWSPTS,pm:PMARKETS,ais:AIS,mil:MILPTS,choke:CHOKE,zones:ZONES\}\)",
    r"loadData\('/api/intel/cot',COT\)",
    r"loadData\('/api/intel/macro',\{events:MACRO,scores:SCORE\}\)",
    r"loadData\('/api/intel/trend',TREND\)",
    r"loadData\('/api/intel/fx',\{strength:FXS,pairs:PAIRS\}\)",
    r"loadData\('/api/intel/markets',MKT\)",
    r"loadData\('/api/intel/pm',PMARKETS\)",
    r"loadData\('/api/intel/season',\{assets:SEAS_ASSETS,bias:SEAS_BIAS\}\)",
    r"loadData\('/api/intel/tdi',TDI\)",
    r"loadData\('/api/intel/micro',\{assets:MICRO,leadlag:LEADLAG\}\)",
]


@pytest.mark.parametrize("pattern", FALLBACKS)
def test_front_has_demo_fallback(pattern: str) -> None:
    assert re.search(pattern, FRONT), f"fallback manquant dans rouge.html : {pattern}"


def test_front_loaddata_wrapper() -> None:
    assert "AbortController" in FRONT
    assert "2500" in FRONT  # timeout du wrapper
    assert FRONT.count("function loadData") == 1
