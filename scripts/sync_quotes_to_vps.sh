#!/usr/bin/env bash
# Mac → VPS : pousse le cache M5 Dukascopy après le refresh local (~05:02
# Paris). Repli intérimaire documenté tant que le refresh Dukascopy n'est
# pas porté nativement sur le VPS (cf. BACKLOG) — stale toléré Mac éteint.
# Cron Mac suggéré : 45 5 * * *  ~/rouge/scripts/sync_quotes_to_vps.sh
set -euo pipefail
KEY=~/.ssh/hetzner_algo_claude
VPS=rouge@178.104.200.63
SRC_M5=~/Desktop/Algo_claude/data/ftmo_portfolio/cache/
SRC_M1=~/Desktop/Algo_claude/data/deep_history_m1/_cache_duka/
LOG=~/rouge/_logs/sync_vps.log

{
  echo "[$(date -u +%FT%TZ)] sync M5 → VPS"
  rsync -az -e "ssh -i $KEY" --include='*_m5_close.parquet' --exclude='*' \
        "$SRC_M5" "$VPS:/home/rouge/lake/ftmo_cache/"
  rsync -az -e "ssh -i $KEY" --include='COPPER_*.parquet' --exclude='*' \
        "$SRC_M1" "$VPS:/home/rouge/lake/duka_m1/"
  echo "[$(date -u +%FT%TZ)] sync ok"
} >> "$LOG" 2>&1
