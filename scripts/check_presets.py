"""Validate the model on every replay preset with real data, and print a table for the pitch.

  python scripts/check_presets.py                 # all presets
  python scripts/check_presets.py --dates 2021-08-14 2024-07-24   # any other dates too

For each date: the prediction as of that day, the new fires seen by satellite over the next 3 days,
hit rate in our top 20%, lift over random, ROC AUC, how the area where the famous fire started
ranked the day before, and the top 5 areas. Results are also saved to data/preset_results.md
(paste-ready) and data/preset_results.json.

Needs FIRMS_MAP_KEY in .env (historical fire detections). Any synthetic input is flagged loudly:
numbers from demo data mean nothing.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import risk  # noqa: E402
from backend.config import DATA_DIR  # noqa: E402
from backend.geo import cell_for_point, haversine_km  # noqa: E402
from backend.main import REPLAY_PRESETS  # noqa: E402

# Approximate ignition points (good to a few km; a grid cell is ~50 km across).
ORIGINS = {
    "2025-01-06": [("Palisades Fire", 34.07, -118.54), ("Eaton Fire", 34.19, -118.10)],
    "2018-11-07": [("Camp Fire", 39.81, -121.43)],
    "2017-10-08": [("Tubbs Fire", 38.60, -122.62)],
    "2020-08-16": [("SCU Lightning Complex", 37.40, -121.40), ("LNU Lightning Complex", 38.50, -122.30)],
}


def pct(x):
    return "–" if x is None else f"{x * 100:.0f}%"


def check(d: date, label: str):
    v = risk.validate(d)
    pred = risk.compute_risk(d)
    cells = {c["id"]: c for c in pred["cells"]}
    modes = {k: s["mode"] for k, s in v["sources"]["prediction"].items()}
    modes["fires"] = v["sources"]["fires_window"]["mode"]
    origins = []
    for name, lat, lon in ORIGINS.get(d.isoformat(), []):
        cid = cell_for_point(lat, lon) or min(cells, key=lambda k: haversine_km(lat, lon, cells[k]["lat"], cells[k]["lon"]))
        c = cells[cid]
        origins.append({"fire": name, "area": c["name"], "rank": c["rank"], "risk": c["risk"], "category": c["category"]})
    return {
        "date": d.isoformat(), "label": label, "n_cells": v["n_cells"],
        "new_fire_areas": v["new_fire_cells"], "hits": v["hits"], "hit_rate": v["hit_rate"],
        "lift": v["lift"], "auc": v["auc"], "method": v["method"],
        "synthetic_inputs": [k for k, m in modes.items() if m != "live"],
        "origins": origins,
        "top5": [{"area": c["name"], "risk": c["risk"], "category": c["category"]}
                 for c in sorted(pred["cells"], key=lambda c: -c["risk"])[:5]],
        "new_fire_list": [{"area": c["name"], "rank": c["rank"], "risk": c["risk"]} for c in v["cells"]],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dates", nargs="*", default=[], help="extra dates (YYYY-MM-DD) to check")
    args = ap.parse_args()
    todo = [(p["date"], p["label"]) for p in REPLAY_PRESETS] + [(d, f"Custom date {d}") for d in args.dates]

    results = []
    for ds, label in todo:
        print(f"Checking {label} ...", flush=True)
        try:
            results.append(check(date.fromisoformat(ds), label))
        except Exception as exc:  # noqa: BLE001 - keep going, report at the end
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            results.append({"date": ds, "label": label, "error": f"{type(exc).__name__}: {exc}"})

    lines = ["| Event (prediction date) | New fire areas | In our top 20% | Hit rate | Lift vs random | ROC AUC | Data |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['label']} ({r['date']}) | error: {r['error'][:60]} | | | | | |")
            continue
        data = "all live" if not r["synthetic_inputs"] else "SYNTHETIC: " + ", ".join(r["synthetic_inputs"])
        lift = "–" if r["lift"] is None else f"{r['lift']}×"
        lines.append(f"| {r['label'].split(' (')[0]} ({r['date']}) | {r['new_fire_areas']} | {r['hits']} | "
                     f"{pct(r['hit_rate'])} | {lift} | {r['auc'] if r['auc'] is not None else '–'} | {data} |")
    lines.append("")
    lines.append("Where the named fires started, and how PyroWatch ranked that area the day before:")
    lines.append("")
    for r in results:
        for o in r.get("origins", []):
            lines.append(f"- {o['fire']}: {o['area']} ranked #{o['rank']} of {r['n_cells']} "
                         f"(risk {o['risk']:.0f}, {o['category']}) on {r['date']}")
    table = "\n".join(lines)

    print("\n" + table + "\n")
    for r in results:
        if r.get("top5"):
            print(f"Top 5 on {r['date']}: " + "; ".join(f"{t['area']} {t['risk']:.0f}" for t in r["top5"]))
    if any(r.get("synthetic_inputs") for r in results):
        print("\nWARNING: some inputs were synthetic (see the Data column). Those rows are NOT real results.\n"
              "  fires   -> set FIRMS_MAP_KEY in .env\n"
              "  history -> run scripts/build_fire_history.py (Min)\n"
              "  weather/terrain -> check your network; Open-Meteo needs no key")

    (DATA_DIR / "preset_results.md").write_text(table + "\n")
    (DATA_DIR / "preset_results.json").write_text(json.dumps(results, indent=2))
    print("\nSaved data/preset_results.md and data/preset_results.json")


if __name__ == "__main__":
    main()
