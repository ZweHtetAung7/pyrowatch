#!/usr/bin/env bash
# Pre-load live risk and the replay events so the first visitor (a judge) never waits.
#   bash deploy/warm.sh                      # on the server
#   bash deploy/warm.sh https://your-domain  # from anywhere
# Historical weather is saved to disk, so after this even a restart loads replays quickly.
BASE="${1:-http://127.0.0.1:8000}"
get() { curl -fsS -m 600 -o /dev/null "$BASE$1"; }

get /api/risk && get "/api/fires?days=2" && echo "live map: ok" || echo "live map: FAILED (check: journalctl -u pyrowatch -n 50)"
dates=$(curl -fsS -m 30 "$BASE/api/config" | python3 -c 'import json, sys; print(" ".join(p["date"] for p in json.load(sys.stdin)["replay_presets"]))')
for d in $dates; do
  echo -n "replay $d: loading... "
  if get "/api/risk?as_of=$d" && get "/api/fires?as_of=$d&days=3" && get "/api/validate?as_of=$d"; then
    echo "ok"
  else
    echo "failed (it will load on the first visit instead)"
  fi
done
