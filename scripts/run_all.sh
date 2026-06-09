#!/usr/bin/env bash
# Orchestration ROUGE — squelette P0, complété en P3 (APScheduler + systemd).
# Usage : ./scripts/run_all.sh
# Tout process long doit être lancé détaché (nohup … & disown), jamais en
# foreground attaché au terminal.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=venv/bin/python

echo "[rouge] init duckdb"
"$PY" scripts/init_db.py

# P3 : scheduler APScheduler (COT hebdo, macro 1h, GDELT 15 min, PM 5 min,
# OpenSky/AIS streaming) + API uvicorn, chacun via nohup … & disown.
echo "[rouge] (P3) scheduler et API non encore branchés"
