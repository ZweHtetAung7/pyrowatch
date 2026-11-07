"""Ground stations: PyroWatch Beacons (ESP32 + temperature/humidity sensor) report readings here.

Satellites see fires from space; stations measure conditions on the ground. Readings show up as
a map layer, in the panel of the area they sit in, and in the Gemini agent's tools. They're kept
in memory and, with DATABASE_URL set, stored as a Tiger Data time series.
"""
import re
import threading
from collections import deque
from datetime import datetime, timedelta, timezone

from . import store
from .geo import cell_for_point, describe_location, in_california

_lock = threading.Lock()
_stations: dict = {}
STATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _now():
    return datetime.now(timezone.utc)


def _num(payload, key, lo, hi):
    v = payload.get(key)
    if v is None:
        return None
    v = float(v)
    if not lo <= v <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return round(v, 2)


def record(station_id: str, payload: dict):
    if not STATION_ID.match(station_id or ""):
        raise ValueError("station id: 1-40 letters, digits, '-' or '_'")
    lat, lon = _num(payload, "lat", -90, 90), _num(payload, "lon", -180, 180)
    if lat is None or lon is None:
        raise ValueError("lat and lon are required")
    reading = {
        "time_utc": _now().isoformat(timespec="seconds").replace("+00:00", "Z"),
        "temp_c": _num(payload, "temp_c", -50, 70),
        "rh_pct": _num(payload, "rh_pct", 0, 100),
        "smoke_raw": _num(payload, "smoke_raw", 0, 100000),
    }
    in_ca = in_california(lat, lon)
    with _lock:
        st = _stations.setdefault(station_id, {"id": station_id, "history": deque(maxlen=1440)})
        st.update(
            lat=lat, lon=lon, label=str(payload.get("label") or station_id)[:60],
            cell_id=cell_for_point(lat, lon) if in_ca else None,
            place=describe_location(lat, lon) if in_ca else f"{lat:.3f}, {lon:.3f} (outside California grid)",
            latest=reading,
        )
        st["history"].append(reading)
        summary = _summary(st)
    store.in_background(store.record_ground_reading, station_id, lat, lon, reading)
    return summary


def _summary(st):
    return {k: st[k] for k in ("id", "label", "lat", "lon", "cell_id", "place", "latest")}


def list_stations(max_age_hours=24, cell_id=None):
    cutoff = (_now() - timedelta(hours=max_age_hours)).isoformat(timespec="seconds").replace("+00:00", "Z")
    with _lock:
        out = [_summary(s) for s in _stations.values() if s["latest"]["time_utc"] >= cutoff]
    if cell_id:
        out = [s for s in out if s["cell_id"] == cell_id]
    return sorted(out, key=lambda s: s["latest"]["time_utc"], reverse=True)


def history(station_id, limit=240):
    with _lock:
        st = _stations.get(station_id)
        return list(st["history"])[-limit:] if st else None
