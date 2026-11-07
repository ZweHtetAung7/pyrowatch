"""Live data sources: NASA FIRMS, Open-Meteo (forecast, archive, elevation), CelesTrak.

Every getter returns (data, status). `status` says whether the data is live or synthetic and
why, and is passed through to the API response so the UI can badge it.
"""
import csv
import hashlib
import io
import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from . import demo
from .config import CACHE_DIR, DATA_DIR, DATA_MODE, FIRMS_MAP_KEY, GRID_STEP_DEG, HTTP_TIMEOUT, TIMEZONE
from .geo import CA_BBOX, build_grid, in_california

log = logging.getLogger("pyrowatch.sources")
# httpx logs every request URL at INFO, and FIRMS URLs contain the MAP_KEY: keep those quiet.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
_client = httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": "PyroWatch/0.1 (hackathon project)"}, follow_redirects=True)
_mem: dict = {}
_lock = threading.Lock()

DAILY_VARS = [
    "temperature_2m_max",
    "relative_humidity_2m_min",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "precipitation_sum",
    "vapour_pressure_deficit_max",
    "et0_fao_evapotranspiration",
]

FIRMS_NRT = [("VIIRS_NOAA20_NRT", "NOAA-20"), ("VIIRS_NOAA21_NRT", "NOAA-21"), ("VIIRS_SNPP_NRT", "Suomi NPP"), ("MODIS_NRT", None)]
FIRMS_SP = [("VIIRS_SNPP_SP", "Suomi NPP"), ("VIIRS_NOAA20_SP", "NOAA-20"), ("MODIS_SP", None)]

# name, NORAD id, instrument, swath width (km)
SATELLITES = [
    ("NOAA-20", 43013, "VIIRS", 3040),
    ("NOAA-21", 54234, "VIIRS", 3040),
    ("Suomi NPP", 37849, "VIIRS", 3040),
    ("Aqua", 27424, "MODIS", 2330),
    ("Terra", 25994, "MODIS", 2330),
]


def today_local() -> date:
    return datetime.now(ZoneInfo(TIMEZONE)).date()


_key_locks: dict = {}


def _cached(key, ttl, fn):
    """In-memory TTL cache. A per-key lock stops parallel requests from fetching the same thing twice."""
    with _lock:
        key_lock = _key_locks.setdefault(key, threading.Lock())
    with key_lock:
        return _cached_locked(key, ttl, fn)


def _cached_locked(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _mem.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    stamp = now
    # A live call that failed and fell back to synthetic data is retried after ~2 minutes.
    if isinstance(val, tuple) and isinstance(val[1], dict) and "failed" in val[1].get("note", ""):
        stamp = now - ttl + 120
    with _lock:
        _mem[key] = (stamp, val)
    return val


def _with_fallback(label, live_fn, demo_fn, *, demo_reason=None):
    if DATA_MODE == "demo" or demo_reason:
        return demo_fn(), {"source": label, "mode": "demo", "note": demo_reason or "PYROWATCH_DATA_MODE=demo"}
    try:
        return live_fn(), {"source": label, "mode": "live"}
    except Exception as exc:  # noqa: BLE001 - any failure falls back in auto mode
        if DATA_MODE == "live":
            raise
        note = f"live call failed ({type(exc).__name__}: {str(exc)[:160]}); using synthetic data"
        log.warning("%s: %s", label, note)
        return demo_fn(), {"source": label, "mode": "demo", "note": note}


def _get(url, params=None, retries=0, wait=15.0):
    """GET that waits and retries on HTTP 429. Open-Meteo's free tier allows ~600 calls a minute
    per IP (a multi-location request counts once per location), and venue Wi-Fi shares one IP."""
    for attempt in range(retries + 1):
        r = _client.get(url, params=params)
        if r.status_code != 429 or attempt == retries:
            return r
        try:
            delay = float(r.headers.get("Retry-After") or wait)
        except ValueError:
            delay = wait
        log.warning("%s is rate-limiting us; waiting %.0f s (retry %d of %d)", httpx.URL(url).host, delay, attempt + 1, retries)
        time.sleep(min(delay, 65.0))
    return r


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


# ---------------------------------------------------------------- weather (Open-Meteo)

def get_weather(points, start: date, end: date):
    """Daily weather for each (lat, lon) in `points`, as a list of {iso_date: {var: value}}."""
    today = today_local()
    use_forecast_api = (today - start).days <= 90
    label = "Open-Meteo " + ("forecast" if use_forecast_api else "historical (ERA5)")
    key = ("wx", tuple(points), start, end)
    ttl = 1800 if end >= today - timedelta(days=1) else 7 * 86400
    # Reanalysis data more than ~5 days old never changes: keep it on disk so reruns and replay
    # demos cost no API quota.
    historical = end < today - timedelta(days=6)
    digest = hashlib.md5(json.dumps([[round(a, 4), round(b, 4)] for a, b in points] + [str(start), str(end), DAILY_VARS]).encode()).hexdigest()[:16]
    disk_path = CACHE_DIR / f"weather_{digest}.json"
    if historical and DATA_MODE != "demo" and disk_path.exists():
        return json.loads(disk_path.read_text()), {"source": label + " (cached)", "mode": "live"}

    def live():
        url = "https://api.open-meteo.com/v1/forecast" if use_forecast_api else "https://archive-api.open-meteo.com/v1/archive"
        variables = list(DAILY_VARS)
        out = []
        for batch in _chunks(list(points), 50):
            for _attempt in range(4):
                params = {
                    "latitude": ",".join(f"{p[0]:.4f}" for p in batch),
                    "longitude": ",".join(f"{p[1]:.4f}" for p in batch),
                    "daily": ",".join(variables),
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "timezone": TIMEZONE,
                    "wind_speed_unit": "kmh",
                }
                r = _get(url, params, retries=3, wait=15)
                if r.status_code == 400:
                    # An unsupported variable fails the whole call; drop it and retry.
                    reason = r.json().get("reason", "")
                    bad = [v for v in variables if v in reason]
                    if bad:
                        log.warning("Open-Meteo rejected %s; retrying without it", bad)
                        variables = [v for v in variables if v not in bad]
                        continue
                r.raise_for_status()
                break
            js = r.json()
            for loc in js if isinstance(js, list) else [js]:
                daily = loc.get("daily", {})
                times = daily.get("time", [])
                out.append(
                    {t: {v: (daily.get(v) or [None] * len(times))[i] for v in DAILY_VARS} for i, t in enumerate(times)}
                )
        if len(out) != len(points):
            raise RuntimeError(f"expected {len(points)} locations, got {len(out)}")
        if historical:
            disk_path.write_text(json.dumps(out))
        return out

    return _cached(key, ttl, lambda: _with_fallback(label, live, lambda: demo.weather_series(points, start, end)))


# ---------------------------------------------------------------- terrain (Open-Meteo elevation)

def get_elevations(points):
    path = CACHE_DIR / "elevation.json"
    disk = json.loads(path.read_text()) if path.exists() else {}
    keys = [f"{la:.3f},{lo:.3f}" for la, lo in points]
    missing = [p for p, k in zip(points, keys) if k not in disk]

    def live():
        # ~1,500 points on the first run is more than one minute's free quota, so wait out 429s and
        # save after every batch: a later run only fetches what is still missing.
        if len(missing) > 500:
            log.info("fetching elevation for %d points once (cached afterwards; may take ~3 min)", len(missing))
        for batch in _chunks(missing, 100):
            r = _get(
                "https://api.open-meteo.com/v1/elevation",
                {"latitude": ",".join(f"{p[0]:.3f}" for p in batch), "longitude": ",".join(f"{p[1]:.3f}" for p in batch)},
                retries=6, wait=15,
            )
            r.raise_for_status()
            for p, e in zip(batch, r.json()["elevation"]):
                disk[f"{p[0]:.3f},{p[1]:.3f}"] = e
            path.write_text(json.dumps(disk))
        return [disk[k] for k in keys]

    if not missing:
        return [disk[k] for k in keys], {"source": "Open-Meteo elevation (cached)", "mode": "live"}
    return _with_fallback("Open-Meteo elevation", live, lambda: demo.elevation(points))


# ---------------------------------------------------------------- vegetation (MODIS NDVI)

def get_vegetation():
    """{cell_id: spring NDVI} from data/vegetation.json, made once by scripts/fetch_vegetation.py.
    Missing or incomplete file -> {} and mode "off": the vegetation multiplier is simply not applied."""
    cells = build_grid()
    if DATA_MODE == "demo":
        return {c["id"]: demo.ndvi(c["lat"], c["lon"]) for c in cells}, {"source": "vegetation", "mode": "demo", "note": "PYROWATCH_DATA_MODE=demo"}
    path = DATA_DIR / "vegetation.json"
    if path.exists():
        js = json.loads(path.read_text())
        vals = js.get("cells", {})
        if js.get("meta", {}).get("grid_step") == GRID_STEP_DEG and all(c["id"] in vals for c in cells):
            return vals, {"source": js["meta"].get("source", "MODIS NDVI"), "mode": "live", "composite": js["meta"].get("composite")}
    return {}, {"source": "vegetation", "mode": "off", "note": "data/vegetation.json missing or incomplete — run scripts/fetch_vegetation.py"}


# ---------------------------------------------------------------- fire history

def get_fire_history():
    """{cell_id: {year: fire_days}} built by scripts/build_fire_history.py."""
    path = DATA_DIR / "fire_history.json"
    cells = build_grid()
    if path.exists() and DATA_MODE != "demo":
        js = json.loads(path.read_text())
        if js.get("meta", {}).get("grid_step") == GRID_STEP_DEG:
            return js["cells"], {"source": js["meta"].get("source", "FIRMS archive"), "mode": "live", "years": js["meta"].get("years")}
        log.warning("fire_history.json was built for a different grid step; ignoring it")
    years = list(range(2012, today_local().year + 1))
    note = "PYROWATCH_DATA_MODE=demo" if DATA_MODE == "demo" else "data/fire_history.json missing — run scripts/build_fire_history.py"
    return demo.fire_history(cells, years), {"source": "fire history", "mode": "demo", "note": note}


# ---------------------------------------------------------------- active fires (NASA FIRMS)

def _confidence(raw, instrument):
    raw = (raw or "").strip().lower()
    if instrument == "VIIRS":
        return {"l": "low", "n": "nominal", "h": "high"}.get(raw[:1], raw or "unknown")
    try:
        v = float(raw)
    except ValueError:
        return "unknown"
    return "low" if v < 30 else "nominal" if v < 80 else "high"


def _parse_firms_csv(text, product, sat_name):
    if "latitude" not in text[:200]:
        raise RuntimeError(f"FIRMS {product}: {text[:120].strip()}")
    instrument = "MODIS" if product.startswith("MODIS") else "VIIRS"
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            lat, lon = float(row["latitude"]), float(row["longitude"])
        except (KeyError, ValueError):
            continue
        if not in_california(lat, lon):
            continue
        hhmm = (row.get("acq_time") or "0").zfill(4)
        t = datetime.strptime(f"{row['acq_date']} {hhmm}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
        name = sat_name or {"T": "Terra", "A": "Aqua"}.get((row.get("satellite") or "?")[:1].upper(), row.get("satellite"))
        out.append(
            {
                "lat": lat,
                "lon": lon,
                "time_utc": t.isoformat().replace("+00:00", "Z"),
                "satellite": name,
                "instrument": instrument,
                "confidence": _confidence(row.get("confidence"), instrument),
                "frp": float(row.get("frp") or 0),
                "daynight": row.get("daynight", ""),
                "source": product,
            }
        )
    return out


def get_fires(start: date | None, days: int, demo_risk: dict | None = None, min_confidence="nominal"):
    """Active-fire detections inside California. start=None means "the most recent `days` days"."""
    days = max(1, min(int(days), 10))
    today = today_local()
    old = start is not None and (today - start).days >= 60
    key = ("fires", start, days, min_confidence)
    keep = {"low": {"low", "nominal", "high", "unknown"}, "nominal": {"nominal", "high", "unknown"}, "high": {"high"}}[min_confidence]

    def fetch(products):
        w, s, e, n = CA_BBOX
        dets, errors = [], []
        for product, sat_name in products:
            for off in range(0, days, 5):  # FIRMS caps the day range per call; stay conservative
                span = min(5, days - off)
                url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_MAP_KEY}/{product}/{w},{s},{e},{n}/{span}"
                if start is not None:
                    url += f"/{(start + timedelta(days=off)).isoformat()}"
                try:
                    r = _client.get(url)
                    r.raise_for_status()
                    dets += _parse_firms_csv(r.text, product, sat_name)
                except Exception as exc:  # noqa: BLE001 - one product failing shouldn't sink the rest
                    errors.append(f"{product}: {str(exc).replace(FIRMS_MAP_KEY, '***')[:100]}")
        if errors and len(errors) >= len(products) * ((days + 4) // 5):
            raise RuntimeError("; ".join(errors[:3]))
        return dets

    def live():
        dets = fetch(FIRMS_SP) if old else fetch(FIRMS_NRT)
        if old and not dets:
            dets = fetch(FIRMS_NRT)
        return [d for d in dets if d["confidence"] in keep]

    def synthetic():
        s = start or (today - timedelta(days=days - 1))
        return demo.fire_detections(build_grid(), demo_risk or {}, s, days, salt=f"{start}")

    reason = None if FIRMS_MAP_KEY else "no FIRMS_MAP_KEY set (free: firms.modaps.eosdis.nasa.gov/api/map_key)"
    if reason and DATA_MODE == "live":
        raise RuntimeError(reason)
    ttl = 900 if start is None or start >= today - timedelta(days=days) else 86400
    return _cached(key, ttl, lambda: _with_fallback("NASA FIRMS", live, synthetic, demo_reason=reason))


# ---------------------------------------------------------------- satellites (CelesTrak)

def get_satellite_tles():
    def live():
        out = []
        for name, norad, instrument, swath in SATELLITES:
            r = _client.get("https://celestrak.org/NORAD/elements/gp.php", params={"CATNR": norad, "FORMAT": "TLE"})
            r.raise_for_status()
            lines = [ln.rstrip() for ln in r.text.strip().splitlines()]
            l1 = next((ln for ln in lines if ln.startswith("1 ")), None)
            l2 = next((ln for ln in lines if ln.startswith("2 ")), None)
            if l1 and l2:
                out.append({"name": name, "norad": norad, "instrument": instrument, "swath_km": swath, "tle1": l1, "tle2": l2})
        if not out:
            raise RuntimeError("CelesTrak returned no element sets")
        return out

    def synthetic():
        out = []
        for i, (name, norad, instrument, swath) in enumerate(SATELLITES):
            _, l1, l2 = demo.synthetic_tle(name, norad, raan_deg=200 + i * 22, mean_anomaly_deg=i * 70)
            out.append({"name": name, "norad": norad, "instrument": instrument, "swath_km": swath, "tle1": l1, "tle2": l2, "simulated": True})
        return out

    # CelesTrak asks clients not to re-download element sets more than every couple of hours.
    return _cached(("tle",), 4 * 3600, lambda: _with_fallback("CelesTrak", live, synthetic))
