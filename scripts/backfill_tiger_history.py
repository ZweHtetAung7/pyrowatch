"""Backfill the previous N days of risk + fire snapshots into Tiger Data/PostgreSQL.

This computes each day (using cached historical weather/FIRMS where available) and persists:
  - full risk snapshot (so /api/risk?as_of=... is served from DB, no external API calls)
  - full fire snapshot for the replay window (default days=3, matches the UI)
  - per-cell hazard history rows (wildfire/thunderstorm/blizzard) for /api/history

Prereqs:
  - Set DATABASE_URL in .env and ensure /api/config reports database.ready
  - Run:  python scripts/backfill_tiger_history.py --days 14 --fire-days 3

First run may be slow (archive weather/FIRMS) and can hit rate limits; safe to re-run
(upserts). Applies 14-day rolling retention at the end.
"""
import argparse
import logging
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import risk, sources, store  # noqa: E402
from backend.config import DATABASE_URL  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("backfill_tiger")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=14, help="How many past days to backfill (default 14)")
    ap.add_argument("--fire-days", type=int, default=3, help="Replay fire window days (default 3, matches UI)")
    args = ap.parse_args()

    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is not set. Put your Tiger/Postgres URL in .env (see .env.example).")
    st = store.status()
    log.info("database status: %s", st)
    if not st.get("ready"):
        log.warning("Database not ready (%s). Data may not persist until connection is fixed.", st.get("error"))

    today = sources.today_local()
    total = max(1, min(int(args.days), 90))
    done = 0
    for i in range(total, 0, -1):
        d = today - timedelta(days=i)
        if store.get_risk_snapshot(d) and store.get_fire_snapshot(d, args.fire_days):
            log.info("%s: snapshots already present, skipping", d)
            continue
        log.info("%s: computing risk + fires(%dd)", d, args.fire_days)
        t0 = time.time()
        try:
            risk.compute_risk(d)
            risk.fires(d, days=args.fire_days)
            time.sleep(0.5)  # let background writes flush
            done += 1
            log.info("%s: ok in %.1fs (db %s)", d, time.time() - t0, "ready" if store.status().get("ready") else "not ready")
        except Exception as exc:  # noqa: BLE001
            log.exception("%s: failed: %s", d, exc)
    store.prune_history(14)
    log.info("backfill complete: %d/%d days processed (14-day retention applied). database=%s", done, total, store.status())
    log.info("Verify: GET /api/risk?as_of=YYYY-MM-DD and /api/fires?as_of=YYYY-MM-DD&days=%d", args.fire_days)


if __name__ == "__main__":
    main()
