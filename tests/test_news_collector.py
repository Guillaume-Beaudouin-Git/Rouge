"""Tests du collecteur GDELT : parsing sur fixture GKG réelle enregistrée
(aucun appel réseau), dédup URL canonique, clustering 50 km, cap et
sélection, vue v_news."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import yaml

from api.db import apply_views
from collectors.news_collector import (
    MAP_PATH, best_location, canonical_url, load_news_map, parse_gkg,
    select_points, slug_title,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "gkg_sample.csv").read_text(encoding="latin-1")

CFG = load_news_map()
#: horodatage du fichier fixture (20260610033000)
NOW = datetime(2026, 6, 10, 4, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- mapping

def test_mapping_incomplet_echoue_explicitement(tmp_path: Path) -> None:
    cfg = yaml.safe_load(MAP_PATH.read_text(encoding="utf-8"))
    cfg["categories"]["energie"]["themes"] = []
    bad = tmp_path / "news_map.yaml"
    bad.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(RuntimeError, match="energie"):
        load_news_map(bad)


# ---------------------------------------------------------------- parsing

def test_parse_gkg_fixture() -> None:
    df = parse_gkg(FIXTURE, CFG)
    # la fixture contient 45 lignes matchantes localisées, 5 matchantes
    # sans localisation (ignorées), 3 hors thèmes (ignorées)
    assert len(df) == 45
    assert set(df.columns) >= {"ts", "source", "url", "url_canon", "title",
                               "lat", "lon", "category"}
    assert df["category"].isin(CFG["categories"]).all()
    assert df["title"].str.len().gt(0).all()
    assert df["lat"].between(-90, 90).all() and df["lon"].between(-180, 180).all()
    assert (df["ts"] == datetime(2026, 6, 10, 3, 30, tzinfo=timezone.utc)).all()


def test_best_location_prefere_la_ville_au_pays() -> None:
    v1 = "1#Iran#IR#IR#32#53#IR;4#Strait Of Hormuz#IR##26.57#56.25#-3093745"
    assert best_location(v1) == (26.57, 56.25)
    assert best_location("") is None
    assert best_location("1#Bad#XX#XX#abc#def#XX") is None


def test_canonical_url_et_slug() -> None:
    a = "https://www.Example.com/news/oil-tanker-hit/?utm_source=x#top"
    b = "http://example.com/news/oil-tanker-hit"
    assert canonical_url(a) == canonical_url(b) == "example.com/news/oil-tanker-hit"
    assert slug_title("https://x.com/markets/fed-holds-rates-steady.html") == "Fed holds rates steady"


# ---------------------------------------------------------- sélection

def _pt(ts: datetime, lat: float, lon: float, cat: str = "energie",
        url: str | None = None, title: str = "t") -> dict:
    url = url or f"https://s.com/{cat}/{lat}-{lon}-{ts.timestamp()}"
    return {"ts": ts, "source": "s.com", "url": url,
            "url_canon": canonical_url(url), "title": title,
            "lat": lat, "lon": lon, "category": cat, "n_themes": 1}


def test_dedup_url_canonique() -> None:
    """GDELT duplique massivement : même article vu 3 fois (www, querystring,
    trailing slash) → UN seul point."""
    t = NOW - timedelta(hours=1)
    df = pd.DataFrame([
        _pt(t, 26.5, 56.2, url="https://www.s.com/a/b/?utm=1"),
        _pt(t + timedelta(minutes=15), 26.5, 56.2, url="https://s.com/a/b"),
        _pt(t + timedelta(minutes=30), 26.5, 56.2, url="http://s.com/a/b/"),
    ])
    sel = select_points(df, CFG, now=NOW)
    assert len(sel) == 1 and sel.iloc[0]["n"] == 1


def test_clustering_50km_meme_sujet() -> None:
    t = NOW - timedelta(hours=1)
    df = pd.DataFrame([
        _pt(t, 26.50, 56.20),                       # Ormuz
        _pt(t, 26.60, 56.40, title="proche"),       # ~23 km → fusionne
        _pt(t, 25.20, 55.30, title="Dubaï"),        # ~170 km → point séparé
        _pt(t, 26.50, 56.20, cat="conflits"),       # même lieu, autre sujet → séparé
    ])
    sel = select_points(df, CFG, now=NOW)
    assert len(sel) == 3
    merged = sel[sel["n"] == 2]
    assert len(merged) == 1 and merged.iloc[0]["category"] == "energie"


def test_cap_et_score_recence_volume() -> None:
    cats = list(CFG["categories"])
    rows = []
    # 30 clusters distincts (>50 km d'écart) répartis sur les 5 catégories
    for i in range(30):
        rows.append(_pt(NOW - timedelta(hours=i % 23), -60 + i * 4,
                        10 + (i * 7) % 160, cat=cats[i % len(cats)]))
    df = pd.DataFrame(rows)
    sel = select_points(df, CFG, now=NOW)
    assert len(sel) == CFG["serve_max"]
    # gros cluster récent → w=3 et tête de liste
    big = [_pt(NOW - timedelta(minutes=30), 50.0, 0.0,
               url=f"https://b.com/{i}") for i in range(6)]
    sel2 = select_points(pd.DataFrame(rows + big), CFG, now=NOW)
    assert sel2.iloc[0]["n"] == 6 and sel2.iloc[0]["w"] == 3


def test_fenetre_24h() -> None:
    df = pd.DataFrame([
        _pt(NOW - timedelta(hours=30), 10, 10),   # hors fenêtre
        _pt(NOW - timedelta(hours=2), 60, 60),
    ])
    sel = select_points(df, CFG, now=NOW)
    assert len(sel) == 1 and sel.iloc[0]["lat"] == 60


# ------------------------------------------------------------------- vue

def test_v_news_sert_le_dernier_snapshot(tmp_path: Path) -> None:
    d = tmp_path / "data" / "news_sel" / "date=2026-06-10"
    d.mkdir(parents=True)
    for hh, off in (("030000", 0.0), ("034500", 1.0)):
        sel = select_points(pd.DataFrame([_pt(NOW - timedelta(hours=2), 26.5 + off, 56.2)]),
                            CFG, now=NOW)
        sel["snapshot_ts"] = datetime(2026, 6, 10, int(hh[:2]), int(hh[2:4]), tzinfo=timezone.utc)
        sel.to_parquet(d / f"part-{hh}.parquet", index=False)
    con = duckdb.connect()
    apply_views(con, tmp_path)
    rows = con.execute("SELECT lat, snapshot_ts FROM v_news").fetchall()
    assert len(rows) == 1 and rows[0][0] == 27.5  # dernier snapshot uniquement


def test_cap_par_categorie() -> None:
    """Une catégorie à très gros volume ne doit pas écraser les autres."""
    t = NOW - timedelta(hours=1)
    rows = [_pt(t, -60 + i * 5, (i * 11) % 170, cat="marches") for i in range(20)]
    rows += [_pt(t, 26.5, 56.2, cat="energie"), _pt(t, 12.6, 43.3, cat="shipping")]
    sel = select_points(pd.DataFrame(rows), CFG, now=NOW)
    counts = sel["category"].value_counts()
    assert counts["marches"] == CFG["category_max"]
    assert counts.get("energie") == 1 and counts.get("shipping") == 1
