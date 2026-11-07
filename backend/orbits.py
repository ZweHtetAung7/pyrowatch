"""Server-side satellite geometry (SGP4), so the Gemini agent can answer "when is the next pass?".

The browser does the same math with satellite.js for the animated layer; this mirrors it.
"""
import math
from datetime import datetime, timedelta, timezone

from sgp4.api import Satrec, jday

from . import sources
from .geo import haversine_km

WGS84_A = 6378.137
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)


def _gmst(jd_ut1):
    """Greenwich mean sidereal time in radians (IAU 1982)."""
    t = (jd_ut1 - 2451545.0) / 36525.0
    sec = 67310.54841 + (876600 * 3600 + 8640184.812866) * t + 0.093104 * t * t - 6.2e-6 * t**3
    return math.radians((sec % 86400) / 240.0)


def subpoint(sat: Satrec, when: datetime):
    """(lat, lon, altitude_km) of the point directly below the satellite."""
    jd, fr = jday(when.year, when.month, when.day, when.hour, when.minute, when.second + when.microsecond / 1e6)
    err, r, _ = sat.sgp4(jd, fr)
    if err:
        return None
    x, y, z = r  # TEME frame, km
    th = _gmst(jd + fr)
    xe, ye = x * math.cos(th) + y * math.sin(th), -x * math.sin(th) + y * math.cos(th)
    p = math.hypot(xe, ye)
    lat = math.atan2(z, p * (1 - WGS84_E2))
    h = 0.0
    for _ in range(5):
        n = WGS84_A / math.sqrt(1 - WGS84_E2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - WGS84_E2 * n / (n + h)))
    lon = (math.degrees(math.atan2(ye, xe)) + 540) % 360 - 180
    return math.degrees(lat), lon, h


def _satellites():
    tles, status = sources.get_satellite_tles()
    return [(t, Satrec.twoline2rv(t["tle1"], t["tle2"])) for t in tles], status


def positions(when=None):
    when = when or datetime.now(timezone.utc)
    sats, status = _satellites()
    out = []
    for t, sat in sats:
        sp = subpoint(sat, when)
        if sp:
            out.append({"name": t["name"], "instrument": t["instrument"], "lat": round(sp[0], 2), "lon": round(sp[1], 2), "altitude_km": round(sp[2])})
    return out, status


def next_passes(lat, lon, hours=12, step_s=60):
    """First pass of each fire-watch satellite within ~70% of its half-swath of (lat, lon)."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sats, status = _satellites()
    out = []
    for t, sat in sats:
        reach = t["swath_km"] / 2 * 0.7
        best = None
        for k in range(int(hours * 3600 / step_s) + 1):
            when = now + timedelta(seconds=k * step_s)
            sp = subpoint(sat, when)
            if not sp:
                continue
            d = haversine_km(lat, lon, sp[0], sp[1])
            if d <= reach and (best is None or d < best[0]):
                best = (d, when)
            elif best is not None and d > reach:
                break
        if best:
            out.append({
                "satellite": t["name"], "instrument": t["instrument"],
                "time_utc": best[1].isoformat().replace("+00:00", "Z"),
                "minutes_from_now": round((best[1] - now).total_seconds() / 60),
                "closest_km": round(best[0]),
            })
    out.sort(key=lambda p: p["minutes_from_now"])
    return out, status
