"""Tests OpenSky : filtre militaire sur fixture réelle enregistrée
(aucun appel réseau), plages hex, libellés, cap."""

from __future__ import annotations

import json
from pathlib import Path

from collectors.flights_collector import filter_states, is_military, load_mil_filter

FIXTURE = json.loads((Path(__file__).resolve().parent / "fixtures" /
                      "opensky_states.json").read_text(encoding="utf-8"))
CFG = load_mil_filter()


def test_fixture_filtree() -> None:
    df = filter_states(FIXTURE["states"], CFG)
    assert 1 <= len(df) <= CFG["serve_max"]
    assert {"lon", "lat", "label"} <= set(df.columns)
    assert df["label"].str.contains(" · ").all()
    assert df["icao24"].is_unique
    assert df["lon"].between(-180, 180).all() and df["lat"].between(-90, 90).all()


def test_is_military_par_prefixe_et_hex() -> None:
    assert is_military("3c0000", "GAF444  ", CFG)        # préfixe callsign
    assert is_military("ae620b", "CARD20", CFG)          # hex US DoD
    assert is_military("ae0001", None, CFG)              # hex sans callsign
    assert not is_military("39de4f", "TVF57JG", CFG)     # civil (fixture réelle)
    assert not is_military(None, None, CFG)


def test_civils_de_la_fixture_exclus() -> None:
    df = filter_states(FIXTURE["states"], CFG)
    assert not df["label"].str.startswith("TVF").any()
    # tous les points retenus re-passent le filtre individuellement
    for _, r in df.iterrows():
        assert is_military(r["icao24"], r["label"].split(" · ")[0], CFG)


def test_cap_et_dedup() -> None:
    base = FIXTURE["states"][0]
    many = []
    for i in range(30):
        s = list(base)
        s[0] = f"ae{i:04x}"
        s[1] = f"DUKE{i:02d}"
        s[4] = 1_781_000_000 + i
        many.append(s)
    many += [list(many[5])]  # doublon icao24
    df = filter_states(many, CFG)
    assert len(df) == CFG["serve_max"]
    assert df["icao24"].is_unique
    # les plus récents d'abord
    assert df["last_contact"].is_monotonic_decreasing
