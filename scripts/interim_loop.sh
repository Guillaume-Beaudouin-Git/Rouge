#!/usr/bin/env bash
# Boucle de collecte intérimaire avant le scheduler P3 (APScheduler/systemd).
# GDELT toutes les 15 min, Polymarket toutes les 5 min — la profondeur news
# (fenêtre 24 h) et l'historique Fed (analyse RV) s'accumulent dès maintenant.
#
# Usage :
#   ./scripts/interim_loop.sh start    # démarre détaché (nohup-safe)
#   ./scripts/interim_loop.sh stop     # arrêt propre via PID file
#   ./scripts/interim_loop.sh status
set -euo pipefail
cd "$(dirname "$0")/.."

PIDFILE=_logs/interim_loop.pid
LOG=_logs/interim_loop.log
PY=venv/bin/python

loop() {
  echo "[interim] boucle démarrée pid=$$" >> "$LOG"
  local last_news=0 last_macro=0
  while true; do
    now=$(date +%s)
    "$PY" -m collectors.pm_collector >> "$LOG" 2>&1 || true
    if (( now - last_news >= 900 )); then
      "$PY" -m collectors.news_collector >> "$LOG" 2>&1 || true
      last_news=$now
    fi
    if (( now - last_macro >= 21600 )); then   # 6 h ⊇ 1 pull/j + re-pull J+1
      "$PY" -m collectors.macro_collector >> "$LOG" 2>&1 || true
      last_macro=$now
    fi
    sleep 300
  done
}

case "${1:-}" in
  start)
    if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "déjà en cours (pid $(cat "$PIDFILE"))"; exit 0
    fi
    mkdir -p _logs
    nohup "$0" _run >> "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    disown
    echo "démarré (pid $(cat "$PIDFILE")) — log: $LOG"
    ;;
  _run) loop ;;
  stop)
    if [[ -f "$PIDFILE" ]]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null && echo "arrêté (pid $(cat "$PIDFILE"))" || echo "process absent"
      rm -f "$PIDFILE"
    else
      echo "pas de PID file"
    fi
    ;;
  status)
    if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "en cours (pid $(cat "$PIDFILE"))"
    else
      echo "arrêté"
    fi
    ;;
  *) echo "usage: $0 start|stop|status"; exit 1 ;;
esac
