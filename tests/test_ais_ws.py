"""Tests daemon AIS : parsing sur messages réels enregistrés (aucun
réseau), affectation corridor, mapping type, downsampling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from collectors.ais_ws import (
    AisDaemon, corridor_bboxes, corridor_of, load_ais_map, select_vessels,
    ship_kind,
)

FIXTURE = (Path(__file__).resolve().parent / "fixtures" /
           "aisstream_messages.jsonl").read_text(encoding="utf-8").splitlines()
CFG = load_ais_map()


def _daemon(monkeypatch) -> AisDaemon:
    monkeypatch.setenv("AISSTREAM_API_KEY", "test-key")
    return AisDaemon()


def test_bboxes_couvrent_les_6_corridors() -> None:
    boxes = corridor_bboxes(CFG)
    assert len(boxes) == 6
    for (lat1, lon1), (lat2, lon2) in boxes:
        assert lat1 < lat2 and lon1 < lon2
    # Ormuz est dans la bbox du corridor 1
    assert corridor_of(56.25, 26.6, CFG) == "ORMUZ-SINGAPOUR"
    assert corridor_of(0.0, 0.0, CFG) is None  # golfe de Guinée : hors corridors


def test_ship_kind() -> None:
    assert ship_kind(70) == "CARGO" and ship_kind(79) == "CARGO"
    assert ship_kind(80) == "TANKER" and ship_kind(89) == "TANKER"
    assert ship_kind(30) is None and ship_kind(None) is None


def test_messages_reels_alimentent_le_buffer(monkeypatch) -> None:
    d = _daemon(monkeypatch)
    for raw in FIXTURE:
        d.handle(raw)
    assert len(d.state) > 10
    for v in d.state.values():
        assert v["corridor"] is not None
        assert -90 <= v["lat"] <= 90 and -180 <= v["lon"] <= 180
    # les ShipStaticData de la fixture ont typé au moins un navire
    assert len(d.types) >= 1


def test_static_data_type_retroactif(monkeypatch) -> None:
    import json
    d = _daemon(monkeypatch)
    pos = json.dumps({"MessageType": "PositionReport",
                      "MetaData": {"MMSI": 1, "latitude": 26.6, "longitude": 56.3}})
    static = json.dumps({"MessageType": "ShipStaticData",
                         "MetaData": {"MMSI": 1},
                         "Message": {"ShipStaticData": {"Type": 84}}})
    d.handle(pos)
    assert d.state[1]["type"] is None
    d.handle(static)
    assert d.state[1]["type"] == "TANKER"   # rétroactif sur le buffer


def test_select_vessels_downsample_et_repartition() -> None:
    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    state = {}
    mmsi = 0
    # 30 navires typés par corridor (180 au total) + 20 sans type
    for c in CFG["corridors"]:
        for i in range(30):
            mmsi += 1
            state[mmsi] = {"lon": c["a"][0], "lat": c["a"][1],
                           "ts": now - timedelta(minutes=i),
                           "corridor": c["name"], "type": "CARGO" if i % 2 else "TANKER"}
    for i in range(20):
        mmsi += 1
        state[mmsi] = {"lon": 0, "lat": 0, "ts": now, "corridor": "X", "type": None}
    sel = select_vessels(state, CFG, now=now)
    assert len(sel) <= CFG["serve_max"]
    per = sel["corridor"].value_counts()
    assert per.max() <= -(-CFG["serve_max"] // 6)   # cap par corridor (ceil)
    assert set(sel["type"]) <= {"TANKER", "CARGO"}  # jamais de type inconnu
    # récence : le plus récent de chaque corridor est servi
    assert (sel.groupby("corridor")["ts"].max() == now).all()
