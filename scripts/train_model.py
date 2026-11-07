"""Stretch goal: train a real classifier and swap it into the app.

For each grid cell and each sample date D in the chosen fire seasons:
  features = the same raw measurements the live model uses (risk.raw_features), as of D
  label    = a NEW fire started in that cell within D..D+6 (detections, none in the 5 days before)

Train on earlier years, test on the last year (a time split, so no peeking at the future), and
compare against the hand-weighted index on the same test set. Saves data/model.joblib; the app
picks it up automatically and shows "ML: xx% chance of a new fire within 7 days".

  python scripts/build_fire_history.py --start 2015 --end 2024     # labels + history first
  python scripts/train_model.py --years 2020 2021 2022 2023 --step 7

Weather comes from the Open-Meteo historical API (ERA5). Free tier is ~10k calls/day; one
fire season for ~190 cells is roughly 3k weighted calls, so 3-4 years fits in a day and is
cached on disk after the first run.  --demo runs the whole pipeline on synthetic data.
"""
import argparse
import json
import os
import pickle
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--years", type=int, nargs="+", default=[2020, 2021, 2022, 2023])
ap.add_argument("--season", default="5-12", help="months to sample, e.g. 5-12 for May-Dec")
ap.add_argument("--step", type=int, default=7, help="days between sample dates")
ap.add_argument("--demo", action="store_true", help="synthetic data end to end (pipeline test, not a real result)")
args = ap.parse_args()
if args.demo:
    os.environ["PYROWATCH_DATA_MODE"] = "demo"

import joblib  # noqa: E402
import numpy as np  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import average_precision_score, roc_auc_score  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

from backend import demo, risk, sources  # noqa: E402
from backend.config import CACHE_DIR, DATA_DIR  # noqa: E402
from backend.geo import build_grid, cell_for_point  # noqa: E402

cells = build_grid()
m0, m1 = (int(x) for x in args.season.split("-"))


def season_dates(y):
    d, end = date(y, m0, 1), min(date(y, m1, 28) + timedelta(days=3), sources.today_local() - timedelta(days=10))
    while d <= end:
        yield d
        d += timedelta(days=args.step)


def weather_for_year(y):
    """One archive request per year for all cells, cached to disk."""
    start, end = date(y, m0, 1) - timedelta(days=31), date(y, m1, 28) + timedelta(days=4)
    path = CACHE_DIR / f"train_wx_{y}_{m0}-{m1}{'_demo' if args.demo else ''}.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    print(f"  fetching {y} weather for {len(cells)} cells ({start} → {end})…")
    wx, status = sources.get_weather([(c["lat"], c["lon"]) for c in cells], start, end)
    if status["mode"] != "live" and not args.demo:
        raise SystemExit(f"Weather fetch failed: {status.get('note')}")
    path.write_bytes(pickle.dumps(wx))
    return wx


# --- labels -------------------------------------------------------------------------------
if args.demo:
    fire_days = None
else:
    fd_path = DATA_DIR / "fire_days.json"
    if not fd_path.exists():
        raise SystemExit("data/fire_days.json missing: run scripts/build_fire_history.py first.")
    fire_days = {k: set(v) for k, v in json.loads(fd_path.read_text())["days"].items()}

hist, _ = sources.get_fire_history()
elev, _ = sources.get_elevations([p for c in cells for p in c["terrain_points"]])
relief, i = {}, 0
for c in cells:
    vals = elev[i : i + len(c["terrain_points"])]
    i += len(c["terrain_points"])
    relief[c["id"]] = float(np.std(vals))


def label(cid, d, demo_fires):
    if demo_fires is not None:
        return int(cid in demo_fires)
    days = fire_days.get(cid, ())
    future = any((d + timedelta(days=k)).isoformat() in days for k in range(7))
    recent = any((d - timedelta(days=k)).isoformat() in days for k in range(1, 6))
    return int(future and not recent)


X, y, yrs, baseline = [], [], [], []
for yr in args.years:
    wx = weather_for_year(yr)
    hist_years = [str(h) for h in range(yr - risk.HISTORY_YEARS, yr)]
    for d in season_dates(yr):
        feats = {}
        for c, series in zip(cells, wx):
            fd = sum(hist.get(c["id"], {}).get(h, 0) for h in hist_years)
            feats[c["id"]] = risk.raw_features(series, d, relief[c["id"]], fd)
        scores = {c["id"]: risk.score_cell(feats[c["id"]], c["lat"])[0] for c in cells}
        demo_fires = None
        if args.demo:  # synthetic ignitions, more likely where the index is high (pipeline test only)
            demo_fires = {cell_for_point(x["lat"], x["lon"]) for x in demo.fire_detections(cells, scores, d, 7, salt=f"train{d}")}
        for c in cells:
            X.append(risk.ml_vector(feats[c["id"]]))
            y.append(label(c["id"], d, demo_fires))
            yrs.append(yr)
            baseline.append(scores[c["id"]])
    print(f"  {yr}: {sum(1 for a, b in zip(yrs, y) if a == yr and b)} positives so far")

X, y, yrs, baseline = np.array(X, float), np.array(y), np.array(yrs), np.array(baseline)
test_year = max(args.years)
tr, te = yrs < test_year, yrs == test_year
print(f"\n{len(y)} samples, {y.sum()} positives ({y.mean():.2%}); train {tr.sum()} / test {te.sum()} (test year {test_year})")
if y[tr].sum() < 5 or y[te].sum() < 5:
    raise SystemExit("Too few positive examples to train/test. Use more years or a smaller --step.")

models = {
    "logistic_regression": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                         LogisticRegression(max_iter=2000, class_weight="balanced")),
    "gradient_boosting": HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, class_weight="balanced"),
}
results = {"weighted_index (baseline)": {"roc_auc": roc_auc_score(y[te], baseline[te]), "pr_auc": average_precision_score(y[te], baseline[te])}}
fitted = {}
for name, model in models.items():
    model.fit(X[tr], y[tr])
    p = model.predict_proba(X[te])[:, 1]
    results[name] = {"roc_auc": roc_auc_score(y[te], p), "pr_auc": average_precision_score(y[te], p)}
    fitted[name] = model

print(f"\n{'model':28s} {'ROC AUC':>8s} {'PR AUC':>8s}   (PR AUC of random = {y[te].mean():.3f})")
for name, r in results.items():
    print(f"{name:28s} {r['roc_auc']:8.3f} {r['pr_auc']:8.3f}")

best = max(fitted, key=lambda n: results[n]["roc_auc"])
lr = fitted["logistic_regression"]
coefs = dict(zip(risk.ML_FEATURES, lr[-1].coef_[0]))
print("\nLogistic-regression coefficients (standardized; + raises risk):")
for k, v in sorted(coefs.items(), key=lambda kv: -abs(kv[1])):
    print(f"  {k:18s} {v:+.3f}")

metrics = {
    "model": best, "test_year": test_year, "train_years": sorted(set(int(v) for v in yrs[tr])),
    "label": "new fire detection in the cell within 7 days", "positives": int(y.sum()), "samples": int(len(y)),
    "roc_auc": round(results[best]["roc_auc"], 3), "pr_auc": round(results[best]["pr_auc"], 3),
    "baseline_roc_auc": round(results["weighted_index (baseline)"]["roc_auc"], 3),
    "base_rate": round(float(y[te].mean()), 4), "data": "synthetic (--demo)" if args.demo else "FIRMS + ERA5",
}
joblib.dump({"model": fitted[best], "features": risk.ML_FEATURES, "metrics": metrics}, DATA_DIR / "model.joblib")
(DATA_DIR / "model_metrics.json").write_text(json.dumps({"results": results, "chosen": metrics, "lr_coefficients": coefs}, indent=2))
print(f"\nSaved data/model.joblib ({best}). Restart the server; each area now shows an ML probability.")
if args.demo:
    print("NOTE: --demo used synthetic data. These numbers mean nothing; rerun without --demo for real results.")
