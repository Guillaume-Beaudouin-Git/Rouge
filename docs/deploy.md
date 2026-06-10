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

## Exécution des scripts sudo / interactifs — pattern obligatoire

`ssh -t … 'sudo bash -s' < script.sh` NE MARCHE PAS : la redirection stdin
empêche l'allocation du TTY et sudo ne peut pas prompter (idem pour
`caddy hash-password`). Pattern validé, dans un Terminal séparé :

```bash
scp -i ~/.ssh/hetzner_algo_claude deploy/vps_bootstrap_N.sh algo@178.104.200.63:/tmp/
ssh -t -i ~/.ssh/hetzner_algo_claude algo@178.104.200.63 'sudo bash /tmp/vps_bootstrap_N.sh <args>'
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
# depuis le Mac, DIRECTEMENT en rouge (la clé du Mac ouvre rouge après la
# phase 1) — destination finale, chmod 600, AUCUNE copie /tmp à nettoyer :
sed -e 's|^DUKASCOPY_LAKE_DIR=.*|DUKASCOPY_LAKE_DIR=/home/rouge/lake/ftmo_cache|' \
    -e 's|^DUKASCOPY_M1_DIR=.*|DUKASCOPY_M1_DIR=/home/rouge/lake/duka_m1|' \
    -e 's|^ROUGE_ENV=.*|ROUGE_ENV=prod|' \
    -e 's|^ROUGE_CORS_ORIGINS=.*|ROUGE_CORS_ORIGINS=http://178.104.200.63|' \
    ~/rouge/.env > /tmp/rouge.env.vps
scp -i ~/.ssh/hetzner_algo_claude /tmp/rouge.env.vps rouge@178.104.200.63:/home/rouge/rouge/.env
rm /tmp/rouge.env.vps
ssh -i ~/.ssh/hetzner_algo_claude rouge@178.104.200.63 'chmod 600 ~/rouge/.env'
# (si une copie /tmp/rouge.env a existé sur le VPS : la supprimer)
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

### Mon IP a changé (allowlist ufw)

L'IP résidentielle bouge. Symptôme : timeout sur http://178.104.200.63
alors que ssh fonctionne. Correctif (Terminal séparé, pattern sudo) :

```bash
curl -4 -s ifconfig.me                                  # nouvelle IP
ssh -t -i ~/.ssh/hetzner_algo_claude algo@178.104.200.63 \
  'sudo bash -c "ufw status numbered; ufw delete <n° de la vieille règle 80>; ufw allow from <NOUVELLE_IP> to any port 80 proto tcp"'
```

CORS prod : `ROUGE_CORS_ORIGINS=http://178.104.200.63` dans le .env VPS
(front servi same-origin — CORS n'est qu'une ceinture).

## 7. Bascule

1. `curl -u guillaume http://178.104.200.63/api/health` → tous les
   datasets non-stale.
2. Ouvrir `http://178.104.200.63` → badges identiques à localhost.
3. Sur le Mac : `./scripts/interim_loop.sh stop` (le cron rsync quotes,
   lui, reste).

### Ce qui dépend ENCORE du Mac après bascule

| Dépendance | Cadence | Si le Mac est éteint |
|---|---|---|
| Refresh Dukascopy (dukascopy-node + dataio.py d'Algo_claude) | quotidien ~05:02 Paris | le lake M5 du VPS gèle |
| `scripts/sync_quotes_to_vps.sh` (cron Mac 05:45) | quotidien | quotes/trend/fx/saison/tdi/micro passent STALE (badges + /api/health) — le reste (COT, PM, news, macro, MIL, AIS) vit en autonomie sur le VPS |

Ligne crontab Mac (`crontab -e`) :

```cron
45 5 * * * $HOME/rouge/scripts/sync_quotes_to_vps.sh
```

Sortie de la dépendance = portage dukascopy-node sur le VPS (BACKLOG).
