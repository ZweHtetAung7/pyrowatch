#!/usr/bin/env bash
# Pull the latest code from GitHub and restart:  sudo bash deploy/update.sh
set -euo pipefail
cd /opt/pyrowatch
# The code belongs to the pyrowatch user; let root's git work here too.
git config --global --get-all safe.directory 2>/dev/null | grep -qx /opt/pyrowatch \
  || git config --global --add safe.directory /opt/pyrowatch
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
chown -R pyrowatch: /opt/pyrowatch
systemctl restart pyrowatch
for _ in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:8000/api/health && break; sleep 1; done
curl -fsS -o /dev/null http://127.0.0.1:8000/api/health && echo "Updated to $(git log -1 --format='%h %s') and running."
bash deploy/warm.sh
