#!/usr/bin/env bash
# ROUGE — bootstrap VPS phase 2 (sudo, APRÈS clone + venv + .env dans /tmp).
# Usage : ssh -t … 'sudo bash -s' < ~/rouge/deploy/vps_bootstrap_2.sh
# Fait : .env en place, services systemd, Caddy (demande le mot de passe
# Basic Auth en interactif).
set -euo pipefail

echo "== .env =="
if [[ -f /tmp/rouge.env ]]; then
  mv /tmp/rouge.env /home/rouge/rouge/.env
  chown rouge:rouge /home/rouge/rouge/.env && chmod 600 /home/rouge/rouge/.env
fi
grep -q "ROUGE_ENV=prod" /home/rouge/rouge/.env || echo "ATTENTION: ROUGE_ENV != prod"

echo "== services systemd =="
cp /home/rouge/rouge/deploy/rouge-{api,scheduler,ais}.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now rouge-api rouge-scheduler rouge-ais
sleep 3
systemctl --no-pager --no-legend status rouge-api rouge-scheduler rouge-ais | grep -E "rouge-|Active:"

echo "== caddy : Basic Auth (saisir le mot de passe d'accès) =="
HASH=$(caddy hash-password)
echo "guillaume $HASH" > /etc/caddy/rouge_users
chown root:caddy /etc/caddy/rouge_users && chmod 640 /etc/caddy/rouge_users
cp /home/rouge/rouge/deploy/Caddyfile /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy
echo "== fait — tester : curl -u guillaume http://178.104.200.63/api/health =="
