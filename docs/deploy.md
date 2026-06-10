# Déploiement VPS (Hetzner CX22) — pas à pas

Cible : le terminal tourne sur le VPS sans dépendre du Mac. Utilisateur
unix dédié `rouge`, tout sous `/home/rouge/`, **zéro interaction** avec le
pipeline Hyperliquid existant (user `algo`, ses services et fichiers ne
sont ni lus ni modifiés).

## 0. Pré-vol (depuis le Mac)

```bash
ssh -i ~/.ssh/hetzner_algo_claude algo@178.104.200.63 'df -h /; free -h; systemctl list-units --type=service --state=running | head -20'
# STOP si marge disque/RAM douteuse — rapport avant toute install.
```

## 1. Utilisateur dédié + paquets (sudo via algo)

```bash
sudo adduser --disabled-password --gecos "" rouge
sudo apt-get update && sudo apt-get install -y python3.12-venv caddy ufw
# accès rsync depuis le Mac : la clé du Mac est autorisée pour rouge
sudo mkdir -p /home/rouge/.ssh && sudo cp ~/.ssh/authorized_keys /home/rouge/.ssh/
sudo chown -R rouge:rouge /home/rouge/.ssh && sudo chmod 700 /home/rouge/.ssh
```

## 2. Code via git (deploy key read-only)

```bash
sudo -u rouge ssh-keygen -t ed25519 -N "" -f /home/rouge/.ssh/rouge_deploy -C rouge-vps-deploy
sudo -u rouge cat /home/rouge/.ssh/rouge_deploy.pub
# → ajouter cette clé en DEPLOY KEY (read-only) sur le repo GitHub
sudo -u rouge tee /home/rouge/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/rouge_deploy
  IdentitiesOnly yes
EOF
sudo -u rouge git clone git@github.com:Guillaume-Beaudouin-Git/Rouge.git /home/rouge/rouge
```

## 3. .env (scp UNE fois — jamais via git) + venv

```bash
# depuis le Mac :
scp -i ~/.ssh/hetzner_algo_claude ~/rouge/.env algo@178.104.200.63:/tmp/rouge.env
# sur le VPS :
sudo mv /tmp/rouge.env /home/rouge/rouge/.env && sudo chown rouge:rouge /home/rouge/rouge/.env && sudo chmod 600 /home/rouge/rouge/.env
# ÉDITER : DUKASCOPY_LAKE_DIR=/home/rouge/lake/ftmo_cache
#          DUKASCOPY_M1_DIR=/home/rouge/lake/duka_m1
#          ROUGE_ENV=prod
sudo -u rouge python3.12 -m venv /home/rouge/rouge/venv
sudo -u rouge /home/rouge/rouge/venv/bin/pip install -r /home/rouge/rouge/requirements.txt
```

## 4. Data initiale (rsync depuis le Mac)

```bash
sudo -u rouge mkdir -p /home/rouge/lake/ftmo_cache /home/rouge/lake/duka_m1
# depuis le Mac (273 Mo M5 + ~60 Mo COPPER M1 + 12 Mo data) :
~/rouge/scripts/sync_quotes_to_vps.sh
rsync -az -e "ssh -i ~/.ssh/hetzner_algo_claude" ~/rouge/data/ rouge@178.104.200.63:/home/rouge/rouge/data/
```

Quotes au quotidien : **repli intérimaire documenté** — le refresh
Dukascopy reste sur le Mac (dukascopy-node + dataio.py, non porté : voir
BACKLOG), un cron Mac à 05:45 Paris pousse le cache M5 post-refresh
(`scripts/sync_quotes_to_vps.sh`). Mac éteint → quotes stale (toléré,
flaggé par /api/health et les badges STALE).

## 5. Services systemd

```bash
sudo cp /home/rouge/rouge/deploy/rouge-{api,scheduler,ais}.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rouge-api rouge-scheduler rouge-ais
```

## 6. Exposition : Caddy + Basic Auth + ufw

Choix documenté : pas de sous-domaine → **HTTP :80 + Basic Auth + ufw**.
(Un sous-domaine plus tard = 1 ligne du Caddyfile, TLS automatique.)

```bash
caddy hash-password   # saisir le mot de passe → hash bcrypt
echo "guillaume <hash>" | sudo tee /etc/caddy/rouge_users   # HORS repo
sudo chmod 640 /etc/caddy/rouge_users && sudo chown root:caddy /etc/caddy/rouge_users
sudo cp /home/rouge/rouge/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo ufw allow OpenSSH && sudo ufw allow from <IP_MAC> to any port 80 && sudo ufw enable
```

CORS prod : `ROUGE_CORS_ORIGINS=http://178.104.200.63` dans le .env VPS
(front servi same-origin — CORS n'est qu'une ceinture).

## 7. Bascule

1. `curl -u guillaume http://178.104.200.63/api/health` → tous les
   datasets non-stale.
2. Ouvrir `http://178.104.200.63` → badges identiques à localhost.
3. Sur le Mac : `./scripts/interim_loop.sh stop` (le cron rsync quotes,
   lui, reste).
