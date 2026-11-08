"""The PyroWatch risk model.

Transparent weighted index (0-100) per grid cell:

    points_i = 100 * weight_i * factor_i * fuel_gate * veg_gate        total = sum(points_i)

Each factor_i is a 0-1 score from a real measurement (see FACTORS / score_factors).
fuel_gate = 0.35 + 0.65 * fuel_dryness, so wet vegetation damps the weather factors: a windy
day over green hills is not a fire day. veg_gate = 0.2 + 0.8 * vegetation, from spring NDVI, so
an area with almost nothing to burn (desert) can't rank high however hot and dry it is.
The breakdown shown in the UI is exactly these points.

It is a *risk index*, not a calibrated probability. If scripts/train_model.py has been run,
each cell also gets `ml_prob`: a trained classifier's probability of a new ignition within 7 days.
"""
import json
import logging
import math
import statistics
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import sources, store
from .config import DATA_DIR, LEDGER_DIR, RECORD_LEDGER
from .geo import build_grid, cell_for_point

log = logging.getLogger("pyrowatch.risk")

LIVE_FRESH_MINUTES = 60
RETENTION_DAYS = 14
_refresh_lock = threading.RLock()
# The live map is kept in memory too, so it is computed once per server run (and refreshed every few
# hours) whether or not a database is configured. A 48 h forecast barely changes within 3 hours.
LIVE_MEMORY_S = 3 * 3600
_live_memory: dict = {}  # date -> (computed_at, result)


def _live_hit(d):
    hit = _live_memory.get(d)
    if not hit:
        return None
    modes = [s.get("mode") for s in hit[1].get("sources", {}).values() if isinstance(s, dict)]
    ttl = 120 if "demo" in modes else LIVE_MEMORY_S  # a failed live source is retried soon
    return hit[1] if time.time() - hit[0] < ttl else None

FACTORS = [
    {"key": "fuel", "label": "Fuel dryness", "weight": 0.30},
    {"key": "wind", "label": "Wind", "weight": 0.20},
    {"key": "humidity", "label": "Low humidity", "weight": 0.18},
    {"key": "history", "label": "Fire history", "weight": 0.14},
    {"key": "heat", "label": "Heat", "weight": 0.10},
    {"key": "terrain", "label": "Terrain", "weight": 0.08},
]
HAZARDS = {
    "wildfire": {"label": "Wildfire", "unit": "risk index", "description": "Relative wildfire risk; not a probability."},
    "thunderstorm": {"label": "Thunderstorm", "unit": "convective potential index", "description": "CAPE indicates atmospheric instability; it is not a forecast of lightning or storm occurrence."},
    "blizzard": {"label": "Blizzard", "unit": "winter weather index", "description": "Combines forecast snowfall, wind and cold; it is not an official warning."},
}
CATEGORIES = [(75, "Extreme"), (60, "Very high"), (40, "High"), (20, "Moderate"), (0, "Low")]
HISTORY_YEARS = 5
# A fire counts as "significant" (likely a wildfire) when it burned this hot, seen in at least this many
# detections. Intensity, not count: winter pile burns and farm burns produce many detections at 5-30 MW,
# while wildfires reach hundreds to thousands of MW. Two detections filters single hot pixels.
# (Tightened from ">= 5 detections OR >= 50 MW" after the first real-data run; see README.)
SIGNIFICANT_MIN_DETECTIONS = 2
SIGNIFICANT_MIN_FRP_MW = 50.0
RAIN_DAY_MM = 2.0
ML_FEATURES = [
    "tmax_c", "rh_min_pct", "gust_kmh", "wind_kmh", "days_since_rain", "precip_30d_mm",
    "vpd_kpa", "relief_m", "fire_days_5y", "ndvi", "ffwi", "month_sin", "month_cos",
]


def clamp01(x):
    return 0.0 if x is None else max(0.0, min(1.0, x))


def fosberg_ffwi(temp_c, rh_pct, wind_kmh):
    """Fosberg Fire Weather Index (Fosberg 1978, US Forest Service), 0-100: how readily fire could
    spread given temperature, humidity and wind alone (no fuel, vegetation or terrain). It is widely
    used in National Weather Service fire-weather products. We feed it daily extremes (max
    temperature, min humidity, max sustained wind), so it reads as that day's worst case."""
    if None in (temp_c, rh_pct, wind_kmh):
        return None
    t = temp_c * 9 / 5 + 32  # deg F
    h = max(0.0, min(100.0, rh_pct))
    u = wind_kmh / 1.609344  # mph
    if h < 10:
        m = 0.03229 + 0.281073 * h - 0.000578 * h * t
    elif h < 50:
        m = 2.22749 + 0.160107 * h - 0.01478 * t
    else:
        m = 21.0606 + 0.005565 * h * h - 0.00035 * h * t - 0.483199 * h
    x = max(0.0, m) / 30  # equilibrium moisture content, as a fraction of 30%
    eta = max(0.0, 1 - 2 * x + 1.5 * x * x - 0.5 * x ** 3)
    return round(min(100.0, eta * math.sqrt(1 + u * u) / 0.3002), 1)


def category(score):
    return next(name for floor, name in CATEGORIES if score >= floor)


def _agg(rows, var, fn):
    vals = [r[var] for r in rows if r and r.get(var) is not None]
    return fn(vals) if vals else None


def raw_features(series, as_of: date, relief_m, fire_days_5y, ndvi=None):
    """Measurements for one cell. `series` is {iso_date: {var: value}} from sources.get_weather.
    The "next 48 h" window is days as_of and as_of+1; antecedent dryness uses the 30 days before."""
    window = [series.get((as_of + timedelta(days=k)).isoformat()) for k in (0, 1)]
    past = [series.get((as_of - timedelta(days=k)).isoformat()) for k in range(1, 31)]  # newest first

    days_since_rain, seen = None, 0
    for row in past:
        if not row or row.get("precipitation_sum") is None:
            continue
        seen += 1
        if row["precipitation_sum"] >= RAIN_DAY_MM:
            days_since_rain = seen
            break
    if days_since_rain is None and seen:
        days_since_rain = 30  # no rain day in the whole lookback ("30+")

    ffwi_days = [fosberg_ffwi(r.get("temperature_2m_max"), r.get("relative_humidity_2m_min"), r.get("wind_speed_10m_max"))
                 for r in window if r]
    peak = max((r for r in window if r and r.get("wind_gusts_10m_max") is not None),
               key=lambda r: r["wind_gusts_10m_max"], default=None)
    return {
        "tmax_c": _agg(window, "temperature_2m_max", max),
        "rh_min_pct": _agg(window, "relative_humidity_2m_min", min),
        "gust_kmh": _agg(window, "wind_gusts_10m_max", max),
        "wind_kmh": _agg(window, "wind_speed_10m_max", max),
        "wind_dir_deg": peak.get("wind_direction_10m_dominant") if peak else None,
        "days_since_rain": days_since_rain,
        "precip_30d_mm": _agg(past, "precipitation_sum", sum),
        "vpd_kpa": _agg(past[:7] + window, "vapour_pressure_deficit_max", statistics.fmean),
        "et0_mm": _agg(past[:7], "et0_fao_evapotranspiration", statistics.fmean),
        "relief_m": relief_m,
        "fire_days_5y": fire_days_5y,
        "ndvi": ndvi,
        "ffwi": max((v for v in ffwi_days if v is not None), default=None),
        "cape_max_jkg": _agg(window, "cape_max_jkg", max),
        "snowfall_48h_cm": _agg(window, "snowfall_sum_cm", sum),
        "month_sin": math.sin(2 * math.pi * as_of.month / 12),
        "month_cos": math.cos(2 * math.pi * as_of.month / 12),
    }


def _fmt(v, unit="", nd=0):
    return "n/a" if v is None else f"{v:.{nd}f}{unit}"


def score_factors(f, lat):
    """0-1 score per factor plus a human-readable explanation of the raw numbers."""
    parts = [
        (0.4, clamp01(f["days_since_rain"] / 30) if f["days_since_rain"] is not None else None),
        (0.3, 1 - clamp01(f["precip_30d_mm"] / 60) if f["precip_30d_mm"] is not None else None),
        (0.3, clamp01(f["vpd_kpa"] / 4.0) if f["vpd_kpa"] is not None else None),
    ]
    parts = [(w, v) for w, v in parts if v is not None]
    fuel = sum(w * v for w, v in parts) / sum(w for w, _ in parts) if parts else None
    dsr = f["days_since_rain"]
    dsr_txt = "no rain day in 30+ days" if dsr == 30 else f"{dsr} days since rain" if dsr is not None else "rain n/a"

    offshore = f["wind_dir_deg"] is not None and (f["wind_dir_deg"] <= 110 or f["wind_dir_deg"] >= 340)
    wind_txt = f"gusts to {_fmt(f['gust_kmh'], ' km/h')}"
    if offshore and lat < 35.8 and (f["gust_kmh"] or 0) >= 40:
        wind_txt += " · offshore (Santa Ana-type)"
    elif offshore and lat >= 37 and (f["gust_kmh"] or 0) >= 40:
        wind_txt += " · offshore (Diablo/north wind)"

    return {
        "fuel": (fuel, f"{dsr_txt} · {_fmt(f['precip_30d_mm'], ' mm', 1)} in 30 days · VPD {_fmt(f['vpd_kpa'], ' kPa', 1)}"),
        "wind": (clamp01((f["gust_kmh"] - 15) / 55) if f["gust_kmh"] is not None else None, wind_txt),
        "humidity": (clamp01((50 - f["rh_min_pct"]) / 45) if f["rh_min_pct"] is not None else None,
                     f"minimum {_fmt(f['rh_min_pct'], '%')} relative humidity"),
        "history": (clamp01(math.log1p(f["fire_days_5y"]) / math.log1p(60)) if f["fire_days_5y"] is not None else None,
                    f"{_fmt(f['fire_days_5y'])} satellite fire-days in the past {HISTORY_YEARS} years"),
        "heat": (clamp01((f["tmax_c"] - 15) / 25) if f["tmax_c"] is not None else None, f"high of {_fmt(f['tmax_c'], '°C')}"),
        "terrain": (clamp01(f["relief_m"] / 450) if f["relief_m"] is not None else None,
                    f"elevation varies ±{_fmt(f['relief_m'], ' m')} across the area"),
    }


def vegetation_gate(ndvi):
    """Multiplier from spring NDVI: ~0.2 for bare desert, 1.0 from NDVI 0.4 (shrub, grass, forest) up."""
    if ndvi is None:
        return 1.0
    return 0.2 + 0.8 * clamp01((ndvi - 0.10) / 0.30)


def vegetation_text(ndvi):
    if ndvi is None:
        return None
    kind = ("sparse desert vegetation, little to burn" if ndvi < 0.15 else "thin vegetation" if ndvi < 0.25
            else "shrub and grass cover" if ndvi < 0.45 else "dense vegetation")
    return f"spring greenness (NDVI) {ndvi:.2f}: {kind}"


def score_cell(f, lat):
    scored = score_factors(f, lat)
    avail = [fx for fx in FACTORS if scored[fx["key"]][0] is not None]
    wsum = sum(fx["weight"] for fx in avail) or 1.0
    fuel = scored["fuel"][0]
    gate = 0.35 + 0.65 * (fuel if fuel is not None else 0.5)
    veg = vegetation_gate(f.get("ndvi"))
    factors = []
    for fx in FACTORS:
        value, explain = scored[fx["key"]]
        pts = 100 * fx["weight"] / wsum * value * gate * veg if value is not None else 0.0
        factors.append({**fx, "value": None if value is None else round(value, 3), "points": round(pts, 1), "explain": explain})
    total = round(sum(x["points"] for x in factors), 1)
    return total, round(gate, 3), factors, round(veg, 3)


def score_severe_weather(f):
    """Return explainable 0–100 thunderstorm and blizzard indices from forecast metrics."""
    cape = f.get("cape_max_jkg")
    storm_factors = []
    if cape is not None:
        storm_factors.append({
            "key": "cape", "label": "Peak CAPE", "weight": 1.0,
            "value": clamp01(cape / 2500), "points": round(100 * clamp01(cape / 2500), 1),
            "explain": f"peak hourly CAPE {cape:.0f} J/kg over 48 h",
        })
    storm_score = round(sum(item["points"] for item in storm_factors), 1) if storm_factors else None

    definitions = [
        ("snowfall", "Snowfall", 0.60, f.get("snowfall_48h_cm"),
         lambda value: clamp01(value / 25),
         lambda value: f"{value:.1f} cm forecast over 48 h"),
        ("wind", "Wind", 0.25, f.get("gust_kmh"),
         lambda value: clamp01((value - 25) / 55),
         lambda value: f"peak gusts {value:.0f} km/h"),
        ("cold", "Cold", 0.15, f.get("tmax_c"),
         lambda value: clamp01((5 - value) / 20),
         lambda value: f"high temperature {value:.1f} °C"),
    ]
    snowfall = f.get("snowfall_48h_cm")
    available = [(key, label, weight, value, score, explain)
                 for key, label, weight, value, score, explain in definitions
                 if value is not None and (key != "snowfall" or snowfall is not None)]
    weight_sum = sum(item[2] for item in available)
    blizzard_factors = []
    if weight_sum:
        snowfall_gate = clamp01(snowfall / 15)
        for key, label, weight, value, score_fn, explain_fn in available:
            value_score = score_fn(value)
            explain = f"{explain_fn(value)} · snowfall gate ×{snowfall_gate:.2f}"
            blizzard_factors.append({
                "key": key, "label": label, "weight": round(weight / weight_sum, 3),
                "value": round(value_score, 3),
                "points": round(100 * weight / weight_sum * value_score * snowfall_gate, 1),
                "explain": explain,
            })
    blizzard_score = round(sum(item["points"] for item in blizzard_factors), 1) if blizzard_factors else None

    return {
        "thunderstorm": {
            "score": storm_score, "category": category(storm_score) if storm_score is not None else None,
            "factors": storm_factors,
        },
        "blizzard": {
            "score": blizzard_score, "category": category(blizzard_score) if blizzard_score is not None else None,
            "factors": blizzard_factors,
        },
    }


# ---------------------------------------------------------------- optional trained model

_model_cache = {"mtime": None, "bundle": None}


def load_model():
    path = DATA_DIR / "model.joblib"
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    if _model_cache["mtime"] != mtime:
        try:
            import joblib

            _model_cache.update(mtime=mtime, bundle=joblib.load(path))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not load model.joblib: %s", exc)
            _model_cache.update(mtime=mtime, bundle=None)
    return _model_cache["bundle"]


def ml_vector(f, features=None):
    """Feature vector in the order the model was trained with (older models keep working)."""
    return [f[k] if f.get(k) is not None else float("nan") for k in (features or ML_FEATURES)]


# ---------------------------------------------------------------- the main computation

def _cell_inputs(as_of: date):
    """Raw features for every cell as of `as_of`, plus source statuses."""
    cells = build_grid()
    wx, wx_status = sources.get_weather([(c["lat"], c["lon"]) for c in cells], as_of - timedelta(days=31), as_of + timedelta(days=1))

    pts = [p for c in cells for p in c["terrain_points"]]
    elev, elev_status = sources.get_elevations(pts)
    relief, i = {}, 0
    for c in cells:
        vals = elev[i : i + len(c["terrain_points"])]
        i += len(c["terrain_points"])
        relief[c["id"]] = statistics.pstdev(vals) if len(vals) > 1 else 0.0

    hist, hist_status = sources.get_fire_history()
    veg, veg_status = sources.get_vegetation()
    years = [str(y) for y in range(as_of.year - HISTORY_YEARS, as_of.year)]  # strictly before as_of: no leakage
    feats = {}
    for c, series in zip(cells, wx):
        fd = sum(hist.get(c["id"], {}).get(y, 0) for y in years)  # no detections on record = 0 fire-days
        feats[c["id"]] = raw_features(series, as_of, relief[c["id"]], fd, veg.get(c["id"]))
    return cells, feats, {"weather": wx_status, "terrain": elev_status,
                          "history": {**hist_status, "years_used": f"{years[0]}–{years[-1]}"}, "vegetation": veg_status}


def compute_risk(as_of: date | None = None):
    today = sources.today_local()
    d = as_of or today
    if d > today:
        raise ValueError("as_of cannot be in the future")
    # Past replay dates: serve full snapshot from DB (no external API calls).
    if as_of is not None and d < today:
        snap = store.get_risk_snapshot(d)
        if snap:
            return snap
    # Live/today: serve a fresh DB snapshot if within LIVE_FRESH_MINUTES (hit once, then reuse).
    if d == today:
        hit = _live_hit(d)
        if hit:
            return hit
        snap = store.get_risk_snapshot(today, max_age_minutes=LIVE_FRESH_MINUTES)
        if snap:
            return snap
        with _refresh_lock:
            hit = _live_hit(d)
            if hit:
                return hit
            snap = store.get_risk_snapshot(today, max_age_minutes=LIVE_FRESH_MINUTES)
            if snap:
                _live_memory.clear()
                _live_memory[d] = (time.time(), snap)
                return snap
            result = _compute_risk(d, today)  # also prunes old history in the background
            _live_memory.clear()
            _live_memory[d] = (time.time(), result)
            return result
    ttl = 86400
    result = sources._cached(("risk", d), ttl, lambda: _compute_risk(d, today))
    return result


def _compute_risk(d: date, today: date):
    cells, feats, statuses = _cell_inputs(d)
    bundle = load_model()
    out = []
    for c in cells:
        f = feats[c["id"]]
        total, gate, factors, veg_gate = score_cell(f, c["lat"])
        severe = score_severe_weather(f)
        severe["wildfire"] = {"score": total, "category": category(round(total)), "factors": factors}
        row = {
            "id": c["id"], "name": c["name"], "lat": c["lat"], "lon": c["lon"], "shape": c["shape"],
            "risk": total, "category": category(round(total)), "fuel_gate": gate, "veg_gate": veg_gate,
            "vegetation": vegetation_text(f.get("ndvi")), "factors": factors,
            "hazards": severe,
            "raw": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in f.items() if not k.startswith("month_")},
        }
        if bundle:
            try:
                row["ml_prob"] = round(float(bundle["model"].predict_proba([ml_vector(f, bundle.get("features"))])[0][1]), 3)
            except Exception as exc:  # noqa: BLE001
                log.warning("model prediction failed: %s", exc)
        out.append(row)
    ranked = sorted(out, key=lambda r: -r["risk"])
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    for hazard in HAZARDS:
        hazard_ranked = sorted(
            (r for r in out if r["hazards"][hazard]["score"] is not None),
            key=lambda r: -r["hazards"][hazard]["score"],
        )
        for i, row in enumerate(hazard_ranked):
            row["hazards"][hazard]["rank"] = i + 1
    counts = {name: 0 for _, name in CATEGORIES}
    for r in out:
        counts[r["category"]] += 1
    counts_by_hazard = {}
    top_by_hazard = {}
    for hazard in HAZARDS:
        scored = [r for r in out if r["hazards"][hazard]["score"] is not None]
        counts_by_hazard[hazard] = {name: 0 for _, name in CATEGORIES}
        for row in scored:
            counts_by_hazard[hazard][row["hazards"][hazard]["category"]] += 1
        top_by_hazard[hazard] = [
            {"id": r["id"], "name": r["name"], "score": r["hazards"][hazard]["score"],
             "category": r["hazards"][hazard]["category"]}
            for r in sorted(scored, key=lambda row: -row["hazards"][hazard]["score"])[:8]
        ]
    result = {
        "as_of": d.isoformat(),
        "window": f"{d.isoformat()} → {(d + timedelta(days=1)).isoformat()} (48 h)",
        "is_live": d == today,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "model": {"type": "weighted index", "factors": [{k: fx[k] for k in ("key", "label", "weight")} for fx in FACTORS],
                  "ml": (bundle or {}).get("metrics") if bundle else None},
        "hazards": HAZARDS,
        "counts_by_hazard": counts_by_hazard,
        "top_by_hazard": top_by_hazard,
        "sources": statuses,
        "counts": counts,
        "top": [{"id": r["id"], "name": r["name"], "risk": r["risk"]} for r in ranked[:8]],
        "cells": out,
    }
    if d == today and statuses["weather"]["mode"] == "live":
        if RECORD_LEDGER:
            _record_prediction(result)
        store.in_background(store.record_prediction, result)  # every refresh -> a time series in Tiger Data
    # Persist full snapshots for fast lookup (live cache + replays/backfill) and enforce retention.
    store.in_background(store.save_risk_snapshot, d, result)
    if d < today:
        store.in_background(store.record_prediction, result)
    store.in_background(store.prune_history, RETENTION_DAYS)
    return result


# ---------------------------------------------------------------- prediction ledger

def _record_prediction(result):
    """Save the first live prediction of each day, so it can later be validated as a *recorded*
    forecast instead of a hindcast. Only called when the weather was live."""
    path = LEDGER_DIR / f"{result['as_of']}.json"
    if path.exists():
        return
    snap = {
        "as_of": result["as_of"],
        "recorded_at": result["generated_at"],
        "sources": result["sources"],
        "cells": {c["id"]: {"risk": c["risk"], "category": c["category"], "ml_prob": c.get("ml_prob"),
                            "hazards": c.get("hazards", {})} for c in result["cells"]},
    }
    path.write_text(json.dumps(snap))


def load_recorded_prediction(d: date):
    """Earliest saved prediction for day d: from Tiger Data if configured, else the ledger file."""
    from_db = store.recorded_prediction(d)
    if from_db:
        return {**from_db, "where": "Tiger Data"}
    path = LEDGER_DIR / f"{d.isoformat()}.json"
    return {**json.loads(path.read_text()), "where": "ledger file"} if path.exists() else None


# ---------------------------------------------------------------- fires & validation

def fires(as_of: date | None = None, days=2):
    """Fire detections for the map: the last `days` days (live) or as_of..as_of+days-1 (replay)."""
    today = sources.today_local()
    d = as_of or today
    start = None if (as_of is None or as_of == today) else as_of
    # Replay windows: serve from DB snapshot when available.
    if start is not None:
        snap = store.get_fire_snapshot(d, days)
        if snap:
            return snap
    # Live window: serve a fresh DB snapshot if within LIVE_FRESH_MINUTES.
    if start is None:
        snap = store.get_fire_snapshot(today, days, max_age_minutes=LIVE_FRESH_MINUTES)
        if snap:
            return snap
        with _refresh_lock:
            snap = store.get_fire_snapshot(today, days, max_age_minutes=LIVE_FRESH_MINUTES)
            if snap:
                return snap
            risk = compute_risk(today)
            demo_risk = {c["id"]: c["risk"] for c in risk["cells"]}
            dets, status = sources.get_fires(None, days, demo_risk=demo_risk)
            for det in dets:
                det["cell_id"] = cell_for_point(det["lat"], det["lon"])
            result = {"as_of": today.isoformat(), "days": days, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                      "source": status, "count": len(dets), "detections": dets}
            if status["mode"] == "live":
                store.in_background(store.record_detections, dets)
            store.in_background(store.save_fire_snapshot, today, days, result)
            store.in_background(store.prune_history, RETENTION_DAYS)
            return result
    risk = compute_risk(as_of)
    demo_risk = {c["id"]: c["risk"] for c in risk["cells"]}
    dets, status = sources.get_fires(start, days, demo_risk=demo_risk)
    for det in dets:
        det["cell_id"] = cell_for_point(det["lat"], det["lon"])
    result = {"as_of": d.isoformat(), "days": days, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
              "source": status, "count": len(dets), "detections": dets}
    if status["mode"] == "live":
        store.in_background(store.record_detections, dets)
    store.in_background(store.save_fire_snapshot, d, days, result)
    store.in_background(store.prune_history, RETENTION_DAYS)
    return result


def _auc(pos, neg):
    """ROC AUC via Mann-Whitney U (ties count half)."""
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def validate(as_of: date | None = None, top_fraction=0.2):
    """Did new fires start where we said risk was high?

    Prediction as of D (recorded in the ledger if we have it, otherwise a hindcast), fires from
    D to D+2, and "new" means the cell had no detections in the 5 days before D.
    """
    today = sources.today_local()
    d = as_of or (today - timedelta(days=2))
    if d > today:
        raise ValueError("as_of cannot be in the future")
    pred = compute_risk(d)
    recorded = load_recorded_prediction(d)
    if recorded:
        scores = {cid: v["risk"] for cid, v in recorded["cells"].items()}
        method = f"recorded prediction (saved {recorded['recorded_at']} in the {recorded['where']}, before these fires were observed)"
    else:
        scores = {c["id"]: c["risk"] for c in pred["cells"]}
        method = ("hindcast: risk recomputed from data up to the start date, using observed weather "
                  "in place of the forecast")

    window_days = min(3, (today - d).days + 1)
    window, w_status = sources.get_fires(d, window_days, demo_risk=scores)
    prior, p_status = sources.get_fires(d - timedelta(days=5), 5, demo_risk=scores)
    prior_stats = {}
    for det in prior:
        cid = cell_for_point(det["lat"], det["lon"])
        if cid is not None:
            st = prior_stats.setdefault(cid, {"n": 0, "max_frp": 0.0})
            st["n"] += 1
            st["max_frp"] = max(st["max_frp"], det["frp"])
    is_big = lambda st: st["n"] >= SIGNIFICANT_MIN_DETECTIONS and st["max_frp"] >= SIGNIFICANT_MIN_FRP_MW  # noqa: E731
    prior_cells = set(prior_stats)  # any detection before: excluded from the all-hotspot count
    prior_big = {cid for cid, st in prior_stats.items() if is_big(st)}  # a real fire before: excluded from significant

    by_cell = {}
    for det in window:
        cid = cell_for_point(det["lat"], det["lon"])
        if cid is None:
            continue
        e = by_cell.setdefault(cid, {"n": 0, "first": det["time_utc"], "max_frp": 0.0, "satellite": det["satellite"]})
        e["n"] += 1
        e["max_frp"] = max(e["max_frp"], det["frp"])
        if det["time_utc"] < e["first"]:
            e["first"], e["satellite"] = det["time_utc"], det["satellite"]
    new_cells = {cid: v for cid, v in by_cell.items() if cid not in prior_cells}
    # A 50 km area often holds a small hotspot or two (a flare, a farm burn) every few days; that
    # shouldn't disqualify a major fire there. Significant = a big fire now, and none in the 5 days before.
    significant = {cid: v for cid, v in by_cell.items() if is_big(v) and cid not in prior_big}

    ranked = sorted(scores, key=lambda cid: -scores[cid])
    rank = {cid: i + 1 for i, cid in enumerate(ranked)}
    k = max(1, round(len(ranked) * top_fraction))
    top = set(ranked[:k])

    def metrics(fires, score_map=scores):
        order = sorted(score_map, key=lambda cid: -score_map[cid])
        top_k = set(order[:k])
        negs = [score_map[cid] for cid in score_map if cid not in by_cell and cid not in prior_cells]
        hits = [cid for cid in fires if cid in top_k]
        rate = len(hits) / len(fires) if fires else None
        auc = _auc([score_map[cid] for cid in fires if cid in score_map], negs)
        return {"new_fire_cells": len(fires), "hits": len(hits),
                "hit_rate": None if rate is None else round(rate, 3),
                "lift": None if rate is None else round(rate / top_fraction, 2),
                "auc": None if auc is None else round(auc, 3)}

    names = {c["id"]: c for c in pred["cells"]}
    ffwi_scores = {c["id"]: (c["raw"].get("ffwi") or 0.0) for c in pred["cells"]}
    return {
        "as_of": d.isoformat(),
        "fire_window": f"{d.isoformat()} → {(d + timedelta(days=window_days - 1)).isoformat()}",
        "method": method,
        "sources": {"prediction": pred["sources"], "fires_window": w_status, "fires_prior": p_status},
        "n_cells": len(ranked),
        "top_fraction": top_fraction,
        "top_threshold": scores[ranked[k - 1]],
        # Headline numbers: significant new fires. Single small hotspots are mostly farm,
        # prescribed or industrial burns; the all-hotspot numbers are reported alongside.
        "significant_rule": (f"a fire intensity peak above {SIGNIFICANT_MIN_FRP_MW:.0f} MW, seen in at least "
                             f"{SIGNIFICANT_MIN_DETECTIONS} satellite detections"),
        **metrics(significant),
        "all_hotspots": metrics(new_cells),
        # Baseline: the same test, ranking areas by the standard Fosberg fire weather index alone.
        "baseline_ffwi": {"significant": metrics(significant, ffwi_scores), "all_hotspots": metrics(new_cells, ffwi_scores)},
        "already_burning_cells": len(set(by_cell) & prior_big),
        "new_rule": ("a significant fire counts as new if that area had no significant fire in the 5 days before; "
                     "for all hotspots, no detection at all in the 5 days before"),
        "excluded_already_burning": [names[cid]["name"] for cid in by_cell if cid in prior_big and is_big(by_cell[cid]) and cid in names],
        "cells": sorted(
            [
                {"id": cid, "name": names[cid]["name"], "lat": names[cid]["lat"], "lon": names[cid]["lon"],
                 "risk": scores[cid], "rank": rank[cid], "in_top": cid in top, "detections": v["n"],
                 "significant": cid in significant,
                 "first_detection_utc": v["first"], "first_satellite": v["satellite"], "max_frp": round(v["max_frp"], 1)}
                for cid, v in {**new_cells, **significant}.items() if cid in names
            ],
            key=lambda x: (not x["significant"], x["rank"]),
        ),
        "caveats": [
            "Satellite detections include some agricultural, prescribed and industrial burns, not only wildfires.",
            "A 0.5° cell is ~50 km across; this tests regional risk, not exact ignition points.",
            "The index ranks relative risk; it is not a calibrated probability.",
        ],
    }
