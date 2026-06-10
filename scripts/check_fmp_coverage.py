"""Test empirique GO/STOP de la couverture macro FMP — AVANT construction.

Protocole (décision du 2026-06-10) : fenêtre [J-30, J+14], pour chacune des
8 devises du front (via leur pays), compter les événements avec consensus
ET actual ET previous non nuls. GO si >= 6/8 devises exploitables ; sinon
STOP + rapport chiffré et bascule sur l'option 2 (FairEconomy).

Usage : ./venv/bin/python scripts/check_fmp_coverage.py
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

#: devise front → codes pays FMP ; EUR agrège les grandes économies zone euro
CCY_COUNTRIES = {
    "USD": {"US"},
    "EUR": {"EA", "EU", "DE", "FR", "IT", "ES"},
    "GBP": {"GB", "UK"},
    "JPY": {"JP"},
    "AUD": {"AU"},
    "CAD": {"CA"},
    "CHF": {"CH"},
    "CNY": {"CN"},
}
GO_THRESHOLD = 6
#: devise exploitable si au moins N événements complets sur la fenêtre
MIN_EVENTS = 3


def main() -> int:
    key = os.getenv("FMP_API_KEY", "").strip()
    if not key:
        print("STOP — FMP_API_KEY absente de .env : pose la clé puis relance.")
        return 2

    frm = (date.today() - timedelta(days=30)).isoformat()
    to = (date.today() + timedelta(days=14)).isoformat()
    candidates = [
        ("stable", f"https://financialmodelingprep.com/stable/economic-calendar"),
        ("v3", f"https://financialmodelingprep.com/api/v3/economic_calendar"),
    ]
    rows, used = None, None
    for label, url in candidates:
        r = httpx.get(url, params={"from": frm, "to": to, "apikey": key}, timeout=30)
        if r.status_code == 200 and isinstance(r.json(), list) and r.json():
            rows, used = r.json(), label
            break
        print(f"  [{label}] HTTP {r.status_code} : {r.text[:120]}")
    if rows is None:
        print("STOP — aucun endpoint calendrier accessible avec cette clé "
              "(calendrier passé premium ?) → bascule option 2 (FairEconomy).")
        return 1

    print(f"endpoint retenu : {used} | fenêtre {frm} → {to} | {len(rows)} événements bruts")
    sample = rows[0]
    print(f"champs : {sorted(sample.keys())}\n")

    def grab(r: dict, *names: str):
        for n in names:
            if r.get(n) is not None:
                return r[n]
        return None

    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "complets": 0})
    for r in rows:
        country = (r.get("country") or "").upper()
        for ccy, countries in CCY_COUNTRIES.items():
            if country in countries:
                s = stats[ccy]
                s["total"] += 1
                cons = grab(r, "estimate", "consensus", "forecast")
                act = grab(r, "actual")
                prev = grab(r, "previous", "prev")
                if cons is not None and act is not None and prev is not None:
                    s["complets"] += 1

    ok = 0
    print(f"{'DEVISE':<7} {'ÉVÉNEMENTS':>11} {'COMPLETS':>9}  (consensus+actual+previous)")
    for ccy in CCY_COUNTRIES:
        s = stats[ccy]
        usable = s["complets"] >= MIN_EVENTS
        ok += usable
        print(f"{ccy:<7} {s['total']:>11} {s['complets']:>9}  {'✓' if usable else '✗'}")

    print()
    if ok >= GO_THRESHOLD:
        print(f"GO — {ok}/8 devises exploitables (seuil {GO_THRESHOLD}).")
        return 0
    print(f"STOP — {ok}/8 devises exploitables (< {GO_THRESHOLD}) "
          "→ bascule option 2 (FairEconomy), cf. protocole.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
