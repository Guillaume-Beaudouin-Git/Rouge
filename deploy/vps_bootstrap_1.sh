#!/usr/bin/env bash
# ROUGE — bootstrap VPS phase 1 (UNE exécution sudo).
# Usage (depuis le Mac) :
#   ssh -t -i ~/.ssh/hetzner_algo_claude algo@178.104.200.63 \
#     'sudo bash -s -- <IP_MAC>' < ~/rouge/deploy/vps_bootstrap_1.sh
# Fait : user rouge, paquets, clé ssh Mac→rouge, deploy key (AFFICHÉE EN
# FIN DE SCRIPT — à ajouter sur GitHub), ufw. Ne touche à rien d'algo/HL.
set -euo pipefail
MAC_IP="${1:?usage: bootstrap_1.sh <IP_MAC_pour_allowlist>}"

echo "== user rouge =="
id rouge &>/dev/null || adduser --disabled-password --gecos "" rouge

echo "== paquets =="
apt-get update -qq
apt-get install -y -qq python3.12-venv python3-pip rsync ufw \
  debian-keyring debian-archive-keyring apt-transport-https curl gnupg
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy
fi

echo "== ssh : la clé du Mac (authorized_keys d'algo) ouvre rouge =="
install -d -m 700 -o rouge -g rouge /home/rouge/.ssh
cp /home/algo/.ssh/authorized_keys /home/rouge/.ssh/authorized_keys
chown rouge:rouge /home/rouge/.ssh/authorized_keys
chmod 600 /home/rouge/.ssh/authorized_keys

echo "== deploy key GitHub (read-only) =="
if [[ ! -f /home/rouge/.ssh/rouge_deploy ]]; then
  sudo -u rouge ssh-keygen -t ed25519 -N "" -q \
    -f /home/rouge/.ssh/rouge_deploy -C rouge-vps-deploy
fi
sudo -u rouge tee /home/rouge/.ssh/config >/dev/null <<'SSHCFG'
Host github.com
  IdentityFile ~/.ssh/rouge_deploy
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
SSHCFG
chmod 600 /home/rouge/.ssh/config

echo "== répertoires lake =="
sudo -u rouge mkdir -p /home/rouge/lake/ftmo_cache /home/rouge/lake/duka_m1

echo "== ufw : SSH ouvert, HTTP allowlist Mac =="
ufw allow OpenSSH >/dev/null
ufw allow from "$MAC_IP" to any port 80 proto tcp >/dev/null
ufw --force enable >/dev/null
ufw status numbered

echo
echo "================ DEPLOY KEY À AJOUTER SUR GITHUB ================"
cat /home/rouge/.ssh/rouge_deploy.pub
echo "================================================================="
