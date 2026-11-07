"""Fetch how green each area is (MODIS NDVI) once, and save it to data/vegetation.json.

Why: fire needs something to burn. Deserts are always "dry", so a weather-only score ranks them
high; NDVI (how green the land is in spring) tells sparse desert from chaparral, grass and forest.

Source: NASA MODIS MOD13Q1 (250 m, 16-day composite) through the ORNL DAAC MODIS web service, which
needs no key. We use the spring 2016 composite (Apr 6-21, 2016): green-up season shows where fuel
grows, and it predates every replay preset, so no future information leaks into a replay.

  python scripts/fetch_vegetation.py          # ~186 requests, 5-15 minutes; resumes if stopped
  git add data/vegetation.json && git commit -m "Add vegetation data" && git push

Teammates then get it with `git pull`; the app never calls this service itself.
"""
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from backend.config import DATA_DIR, GRID_STEP_DEG  # noqa: E402
from backend.geo import build_grid  # noqa: E402

COMPOSITE = "A2016097"  # MOD13Q1 composite starting 2016-04-06
HALF_SIZE_KM = 12  # average a 24 x 24 km box around each area's anchor point
URL = "https://modis.ornl.gov/rst/api/v1/MOD13Q1/subset"
OUT = DATA_DIR / "vegetation.json"


def fetch_one(client, lat, lon):
    params = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "band": "250m_16_days_NDVI",
              "startDate": COMPOSITE, "endDate": COMPOSITE, "kmAboveBelow": HALF_SIZE_KM, "kmLeftRight": HALF_SIZE_KM}
    for attempt in range(5):
        try:
            r = client.get(URL, params=params)
            if r.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            js = r.json()
            values = [v for s in js.get("subset", []) for v in s.get("data", [])]
            scale = float(js.get("scale") or 0.0001)
            land = [v * scale for v in values if v is not None and v > 0]  # drop fill (-3000) and water (< 0)
            if not land:
                return None
            return round(statistics.fmean(land), 4)
        except (httpx.HTTPError, ValueError) as exc:
            wait = 5 * (attempt + 1)
            print(f"    retry in {wait}s ({type(exc).__name__}: {str(exc)[:80]})")
            time.sleep(wait)
    raise RuntimeError("MODIS service kept failing; run the script again later (it resumes)")


def main():
    cells = build_grid()
    data = json.loads(OUT.read_text()) if OUT.exists() else {}
    if data.get("meta", {}).get("composite") != COMPOSITE or data.get("meta", {}).get("grid_step") != GRID_STEP_DEG:
        data = {}
    values = data.get("cells", {})
    todo = [c for c in cells if c["id"] not in values]
    print(f"{len(cells) - len(todo)} of {len(cells)} areas already fetched; fetching {len(todo)} ...")
    client = httpx.Client(timeout=90, headers={"Accept": "json", "User-Agent": "PyroWatch/0.1 (hackathon project)"})
    meta = {"composite": COMPOSITE, "grid_step": GRID_STEP_DEG, "half_size_km": HALF_SIZE_KM,
            "source": "NASA MODIS MOD13Q1 NDVI via ORNL DAAC"}
    for i, c in enumerate(todo, 1):
        values[c["id"]] = fetch_one(client, c["lat"], c["lon"])
        meta["built_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        OUT.write_text(json.dumps({"meta": meta, "cells": values}))
        if i % 10 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}  {c['name']}: NDVI {values[c['id']]}")
        time.sleep(0.2)
    ok = sum(1 for v in values.values() if v is not None)
    print(f"Done: {ok} of {len(cells)} areas have NDVI. Saved {OUT.relative_to(ROOT)}. Now commit and push it.")


if __name__ == "__main__":
    main()
