"""API ROUGE — sert le front (same-origin) et les endpoints /api/*.

P1 : chaque endpoint sert la fixture démo api/demo/<dataset>.json dans
l'enveloppe uniforme {"data": …, "meta": {"source", "asof", "stale"}}.
P2 branchera dataset par dataset les vues DuckDB, avec stale=true quand
on sert la dernière partition valide après échec de collecte.

Dev : ./venv/bin/uvicorn api.main:app --reload  puis  http://localhost:8000
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api import db

log = logging.getLogger("rouge.api")

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

DEMO_DIR = Path(__file__).resolve().parent / "demo"
FRONT_DIR = REPO_ROOT / "frontend"
ROUGE_ENV = os.getenv("ROUGE_ENV", "dev")

app = FastAPI(title="ROUGE API", version="0.1", docs_url="/api/docs" if ROUGE_ENV == "dev" else None)

_origins = [
    o.strip()
    for o in os.getenv("ROUGE_CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware, allow_origins=_origins, allow_methods=["GET"], allow_headers=["*"]
)


def envelope(dataset: str) -> dict:
    """Enveloppe uniforme. P1 : source démo, jamais stale."""
    path = DEMO_DIR / f"{dataset}.json"
    if not path.exists():
        raise HTTPException(503, detail=f"fixture {dataset} absente — lancer scripts/extract_demo.py")
    asof = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    return {
        "data": json.loads(path.read_text(encoding="utf-8")),
        "meta": {"source": "demo", "asof": asof, "stale": False},
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "env": ROUGE_ENV}


NEWS_STALE_MINUTES = 60  # cf. news_map.yaml stale_after_minutes


@app.get("/api/monitor/layers")
def monitor_layers() -> dict:
    """Calques carte. Blocs branchés au fil de P2 : news est live (GDELT),
    le reste sert encore la fixture démo — l'état par bloc est exposé dans
    meta.blocks pour les badges par calque du front."""
    body = envelope("layers")
    blocks = {k: "demo" for k in ("news", "pm", "ais", "mil", "choke", "zones")}
    try:
        rows = db.query(
            "SELECT lon, lat, w, title, source, ts, snapshot_ts FROM v_news"
        )
        if not rows:
            raise LookupError("v_news vide")
        snap = rows[0]["snapshot_ts"]
        snap = snap.replace(tzinfo=timezone.utc) if snap.tzinfo is None else snap.astimezone(timezone.utc)
        age_min = (datetime.now(timezone.utc) - snap).total_seconds() / 60
        stale = age_min > NEWS_STALE_MINUTES
        body["data"]["news"] = [
            [r["lon"], r["lat"], r["w"], r["title"], r["source"],
             r["ts"].astimezone(timezone.utc).isoformat(timespec="seconds")]
            for r in rows
        ]
        blocks["news"] = "stale" if stale else "live"
        body["meta"] = {"source": "gdelt+demo",
                        "asof": snap.isoformat(timespec="seconds"),
                        "stale": stale}
    except Exception as err:
        log.warning("v_news indisponible (%s) — bloc news en fixture démo", err)
    body["meta"]["blocks"] = blocks
    return body


#: seuil de péremption par dataset (jours depuis la date de référence)
COT_STALE_DAYS = 10


@app.get("/api/intel/cot")
def intel_cot() -> dict:
    """COT réel via v_cot (asof = date du rapport, le mardi de référence) ;
    repli silencieux sur la fixture démo si le data lake est indisponible."""
    try:
        rows = db.query(
            "SELECT name, iso, pctl, z, dwk, crowd, report_date FROM v_cot ORDER BY ord"
        )
        expected = len(json.loads((DEMO_DIR / "cot.json").read_text(encoding="utf-8")))
        if len(rows) != expected:
            raise LookupError(f"v_cot : {len(rows)} contrats au lieu de {expected}")
        asof = max(r["report_date"] for r in rows)
        data = [
            {"name": r["name"], "iso": r["iso"], "pctl": r["pctl"],
             "z": r["z"], "dwk": r["dwk"], "crowd": r["crowd"]}
            for r in rows
        ]
        return {
            "data": data,
            "meta": {
                "source": "cftc-socrata",
                "asof": asof.isoformat(),
                "stale": (date.today() - asof).days > COT_STALE_DAYS,
            },
        }
    except Exception as err:
        log.warning("v_cot indisponible (%s) — repli fixture démo", err)
        return envelope("cot")


@app.get("/api/intel/macro")
def intel_macro() -> dict:
    return envelope("macro")


TREND_STALE_DAYS = 7  # cf. quotes_map.yaml stale_after_days


@app.get("/api/intel/trend")
def intel_trend() -> dict:
    """TREND réel (quotes Dukascopy + positionnement COT). Composantes mac
    et flow neutres tant que leurs collecteurs ne sont pas branchés —
    flaggées dans meta, jamais simulées. Actifs hors lake : ligne neutre
    live=false, listés dans meta.excluded."""
    try:
        rows = db.query(
            "SELECT cat, sym, name, f1, f2, g, mom, mac, pos, risk, flow, "
            "d30, chg, live, pos_available, asof_session FROM v_trend"
        )
        expected = len(json.loads((DEMO_DIR / "trend.json").read_text(encoding="utf-8")))
        if len(rows) != expected:
            raise LookupError(f"v_trend : {len(rows)} actifs au lieu de {expected}")
        asof = max(r["asof_session"] for r in rows).date()
        data = [
            {"cat": r["cat"], "sym": r["sym"], "name": r["name"],
             "f1": r["f1"], "f2": r["f2"], "g": r["g"], "mom": r["mom"],
             "mac": r["mac"], "pos": r["pos"], "risk": r["risk"],
             "flow": r["flow"], "d30": r["d30"], "chg": r["chg"]}
            for r in rows
        ]
        return {
            "data": data,
            "meta": {
                "source": "dukascopy+cftc",
                "asof": asof.isoformat(),
                "stale": (date.today() - asof).days > TREND_STALE_DAYS,
                "components": {"mom": True, "mac": False, "pos": True,
                               "risk": True, "flow": False},
                # part du poids total du score portée par des composantes
                # live (pas de renormalisation : verdicts conservateurs
                # tant que mac/flow ne sont pas branchés)
                "effective_weight": 0.65,
                "excluded": sorted(r["sym"] for r in rows if not r["live"]),
                "pos_missing": sorted(
                    r["sym"] for r in rows if r["live"] and not r["pos_available"]),
            },
        }
    except Exception as err:
        log.warning("v_trend indisponible (%s) — repli fixture démo", err)
        return envelope("trend")


@app.get("/api/intel/fx")
def intel_fx() -> dict:
    """Force G8 + 28 paires depuis le lake quotes (croisées synthétiques
    via jambes USD). Conflit = sens de la paire vs signe du différentiel
    de force ; repli démo si le data lake est indisponible."""
    try:
        strength = db.query("SELECT c, iso, now, s, asof_session FROM v_fx_strength")
        pairs = db.query("SELECT p, b, q, diff, trend, conflict FROM v_fx_pairs")
        if len(strength) != 8 or len(pairs) != 28:
            raise LookupError(f"v_fx : {len(strength)} devises / {len(pairs)} paires")
        asof = max(r["asof_session"] for r in strength).date()
        return {
            "data": {
                "strength": [{"c": r["c"], "iso": r["iso"], "now": r["now"],
                              "s": list(r["s"])} for r in strength],
                "pairs": pairs,
            },
            "meta": {
                "source": "dukascopy",
                "asof": asof.isoformat(),
                "stale": (date.today() - asof).days > TREND_STALE_DAYS,
                "conflict_rate": round(sum(p["conflict"] for p in pairs) / 28, 2),
            },
        }
    except Exception as err:
        log.warning("v_fx indisponible (%s) — repli fixture démo", err)
        return envelope("fx")


@app.get("/api/intel/markets")
def intel_markets() -> dict:
    return envelope("markets")


PM_STALE_MINUTES = 30


def _fmt_vol(v: float) -> str:
    return f"{v / 1e6:.1f} M$" if v >= 1e6 else f"{v / 1e3:.0f} k$"


@app.get("/api/intel/pm")
def intel_pm() -> dict:
    """Marchés de prédiction réels via v_pm (asof = timestamp de collecte) ;
    repli silencieux sur la fixture démo si le data lake est indisponible."""
    try:
        rows = db.query("SELECT q, p, d, vol_num, lon, lat, snapshot_ts FROM v_pm ORDER BY ord")
        expected = len(json.loads((DEMO_DIR / "pm.json").read_text(encoding="utf-8")))
        if len(rows) != expected:
            raise LookupError(f"v_pm : {len(rows)} livres au lieu de {expected}")
        asof = max(r["snapshot_ts"] for r in rows)
        asof = asof.replace(tzinfo=timezone.utc) if asof.tzinfo is None else asof.astimezone(timezone.utc)
        age_min = (datetime.now(timezone.utc) - asof).total_seconds() / 60
        data = [
            {"q": r["q"], "p": r["p"], "d": r["d"], "vol": _fmt_vol(r["vol_num"]),
             "lon": r["lon"], "lat": r["lat"]}
            for r in rows
        ]
        return {
            "data": data,
            "meta": {
                "source": "polymarket-gamma",
                "asof": asof.isoformat(timespec="seconds"),
                "stale": age_min > PM_STALE_MINUTES,
            },
        }
    except Exception as err:
        log.warning("v_pm indisponible (%s) — repli fixture démo", err)
        return envelope("pm")


@app.get("/api/intel/season")
def intel_season() -> dict:
    """Saisonnalité réelle (retours mensuels moyens + hit-rate, pas
    d'annualisation) depuis quotes_daily ; repli démo si indisponible."""
    try:
        rows = db.query("SELECT sym, month, mean_pct, hit_pct, n_years, live, "
                        "asof_session FROM v_season")
        fixture = json.loads((DEMO_DIR / "season.json").read_text(encoding="utf-8"))
        assets = fixture["assets"]
        if len(rows) != 12 * len(assets):
            raise LookupError(f"v_season : {len(rows)} lignes")
        by_sym: dict[str, list] = {}
        hit: dict[str, list] = {}
        years: dict[str, int] = {}
        excluded = []
        for sym in assets:
            sr = sorted((r for r in rows if r["sym"] == sym), key=lambda r: r["month"])
            by_sym[sym] = [r["mean_pct"] for r in sr]
            hit[sym] = [r["hit_pct"] for r in sr]
            years[sym] = max(r["n_years"] for r in sr)
            if not sr[0]["live"]:
                excluded.append(sym)
        asof = max(r["asof_session"] for r in rows).date()
        return {
            "data": {"assets": assets, "bias": by_sym, "hit": hit},
            "meta": {
                "source": "dukascopy",
                "asof": asof.isoformat(),
                "stale": (date.today() - asof).days > TREND_STALE_DAYS,
                "years_used": years,
                "excluded": excluded,
            },
        }
    except Exception as err:
        log.warning("v_season indisponible (%s) — repli fixture démo", err)
        return envelope("season")


@app.get("/api/intel/tdi")
def intel_tdi() -> dict:
    """12 divergences pair-based (log-spread, z vs fenêtre) depuis le lake
    quotes ; lignes excluded explicites si jambe absente ; repli démo."""
    try:
        rows = db.query("SELECT flux, met, z, note, live, asof_session FROM v_tdi")
        if len(rows) != 12:
            raise LookupError(f"v_tdi : {len(rows)} lignes")
        asof = max(r["asof_session"] for r in rows).date()
        return {
            "data": [{"flux": r["flux"], "met": r["met"], "z": r["z"],
                      "note": r["note"]} for r in rows],
            "meta": {
                "source": "dukascopy",
                "asof": asof.isoformat(),
                "stale": (date.today() - asof).days > TREND_STALE_DAYS,
                "excluded": [r["met"] for r in rows if not r["live"]],
            },
        }
    except Exception as err:
        log.warning("v_tdi indisponible (%s) — repli fixture démo", err)
        return envelope("tdi")


@app.get("/api/intel/micro")
def intel_micro() -> dict:
    return envelope("micro")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONT_DIR / "rouge.html", media_type="text/html")


# Monté en dernier : les routes /api/* déclarées au-dessus restent prioritaires.
app.mount("/", StaticFiles(directory=FRONT_DIR), name="frontend")
