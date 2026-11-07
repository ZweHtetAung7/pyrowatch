"""Build data/fire_history.json (fire-days per grid cell per year) and data/fire_days.json
(the dates each cell had detections; used as labels by train_model.py).

Source: NASA FIRMS MODIS archive, consistent since 2001. Three ways to get it:

  python scripts/build_fire_history.py --start 2015 --end 2025              # FIRMS yearly country CSVs (no key)
  python scripts/build_fire_history.py --start 2015 --end 2025 --api        # FIRMS area API, needs FIRMS_MAP_KEY
  python scripts/build_fire_history.py --csv modis_2023_United_States.csv ...  # files you downloaded by hand

Yearly CSVs: https://firms.modaps.eosdis.nasa.gov/country/ (MODIS, United States).
"""
import argparse
import csv
import io
import json
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from backend.config import DATA_DIR, FIRMS_MAP_KEY, GRID_STEP_DEG  # noqa: E402
from backend.geo import CA_BBOX, cell_for_point, in_california  # noqa: E402

COUNTRY_URL = "https://firms.modaps.eosdis.nasa.gov/data/country/modis/{y}/modis_{y}_United_States.csv"


def keep(row):
    """Vegetation fires with reasonable confidence inside California."""
    try:
        lat, lon = float(row["latitude"]), float(row["longitude"])
        conf = float(row.get("confidence") or 0)
    except (KeyError, ValueError):
        return None
    if row.get("type") not in (None, "", "0"):  # 0 = presumed vegetation fire; drop volcanoes/industrial/offshore
        return None
    w, s, e, n = CA_BBOX
    if conf < 30 or not (s <= lat <= n and w <= lon <= e) or not in_california(lat, lon):
        return None
    return lat, lon, row["acq_date"]


def ingest(rows, days_by_cell):
    n = 0
    for row in rows:
        k = keep(row)
        if not k:
            continue
        cid = cell_for_point(k[0], k[1])
        if cid:
            days_by_cell[cid].add(k[2])
            n += 1
    return n


def from_country_csv(year, days_by_cell, client):
    url = COUNTRY_URL.format(y=year)
    print(f"  {year}: downloading {url}")
    with client.stream("GET", url) as r:
        r.raise_for_status()
        lines = (ln for ln in r.iter_lines())
        return ingest(csv.DictReader(lines), days_by_cell)


def from_api(year, days_by_cell, client):
    if not FIRMS_MAP_KEY:
        raise SystemExit("--api needs FIRMS_MAP_KEY in .env")
    w, s, e, n = CA_BBOX
    d, end, total = date(year, 1, 1), min(date(year, 12, 31), date.today() - timedelta(days=1)), 0
    while d <= end:
        span = min(5, (end - d).days + 1)
        for product in ("MODIS_SP", "MODIS_NRT"):
            r = client.get(f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_MAP_KEY}/{product}/{w},{s},{e},{n}/{span}/{d.isoformat()}")
            if r.status_code == 200 and r.text.startswith("latitude"):
                got = ingest(csv.DictReader(io.StringIO(r.text)), days_by_cell)
                total += got
                if got or product == "MODIS_NRT":
                    break
        print(f"  {d} +{span}d  total={total}", end="\r")
        d += timedelta(days=span)
        time.sleep(0.2)
    print()
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=2015)
    ap.add_argument("--end", type=int, default=datetime.now(timezone.utc).year - 1)
    ap.add_argument("--api", action="store_true", help="use the FIRMS area API instead of yearly country CSVs")
    ap.add_argument("--csv", nargs="*", help="local FIRMS CSV files to ingest instead of downloading")
    args = ap.parse_args()

    days_by_cell = defaultdict(set)
    client = httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": "PyroWatch/0.1"})
    if args.csv:
        for path in args.csv:
            with open(path, newline="") as f:
                print(f"  {path}: {ingest(csv.DictReader(f), days_by_cell)} detections kept")
    else:
        for y in range(args.start, args.end + 1):
            try:
                n = from_api(y, days_by_cell, client) if args.api else from_country_csv(y, days_by_cell, client)
                print(f"  {y}: {n} detections kept")
            except httpx.HTTPError as exc:
                print(f"  {y}: FAILED ({exc}). Try --api, or download the CSV by hand and use --csv.")

    years = sorted({d[:4] for s in days_by_cell.values() for d in s})
    if not years:
        raise SystemExit("No detections ingested; nothing written.")
    cells = {cid: {y: sum(1 for d in s if d.startswith(y)) for y in years} for cid, s in days_by_cell.items()}
    meta = {"grid_step": GRID_STEP_DEG, "source": "NASA FIRMS MODIS archive", "years": f"{years[0]}–{years[-1]}",
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (DATA_DIR / "fire_history.json").write_text(json.dumps({"meta": meta, "cells": cells}))
    (DATA_DIR / "fire_days.json").write_text(json.dumps({"meta": meta, "days": {k: sorted(v) for k, v in days_by_cell.items()}}))
    print(f"Wrote fire_history.json and fire_days.json: {len(cells)} cells, years {meta['years']}")


if __name__ == "__main__":
    main()
