#!/usr/bin/env bash
# One-shot setup on a fresh Vultr Ubuntu 24.04 instance (Cloud Compute, 1 vCPU / 1-2 GB is plenty).
#   git clone <this repo> /opt/pyrowatch && cd /opt/pyrowatch
#   nano .env                                  # paste your keys (copy from .env.example)
#   sudo bash deploy/setup_vultr.sh pyrowatch.example   # your GoDaddy Registry domain, A record -> this server's IP
# Later updates: sudo bash deploy/update.sh
set -euo pipefail
DOMAIN="${1:?usage: sudo bash deploy/setup_vultr.sh your-domain}"
cd /opt/pyrowatch
[ -f .env ] || { cp .env.example .env; echo "Created .env from the template; add your keys and rerun."; exit 1; }

apt-get update
apt-get install -y python3-venv caddy git curl cron
id pyrowatch >/dev/null 2>&1 || useradd --system --home /opt/pyrowatch --shell /usr/sbin/nologin pyrowatch
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
chown -R pyrowatch: /opt/pyrowatch
git config --global --get-all safe.directory 2>/dev/null | grep -qx /opt/pyrowatch \
  || git config --global --add safe.directory /opt/pyrowatch   # so root can git pull later

cp deploy/pyrowatch.service /etc/systemd/system/pyrowatch.service
systemctl daemon-reload
systemctl enable --now pyrowatch
sed "s/YOUR_DOMAIN/${DOMAIN}/" deploy/Caddyfile > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy
if command -v ufw >/dev/null; then ufw allow OpenSSH; ufw allow 80/tcp; ufw allow 443/tcp; fi

# Refresh the live prediction every 30 minutes even with no visitors, so the Tiger Data history
# (if DATABASE_URL is set) keeps growing and nobody waits for a cold forecast.
echo '*/30 * * * * root curl -fsS -m 300 -o /dev/null http://127.0.0.1:8000/api/risk' > /etc/cron.d/pyrowatch-refresh

for _ in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:8000/api/health && break; sleep 1; done
curl -fsS http://127.0.0.1:8000/api/health || { echo "App did not start. See: journalctl -u pyrowatch -n 50"; exit 1; }
echo " <- app is up. Warming caches (a few minutes)..."
bash deploy/warm.sh
echo "Done. Open https://${DOMAIN} (HTTPS starts working a minute or two after the domain points here)."
