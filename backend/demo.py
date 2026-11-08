"""Synthetic, deterministic stand-ins for every live source.

Used when PYROWATCH_DATA_MODE=demo, or per-source when a live call fails in "auto" mode.
Every API response says which sources were synthetic, and the UI shows a DEMO badge, so this
data can never be passed off as real. It exists so the app runs on bad wifi and in CI.
"""
import hashlib
import math
import random
from datetime import date, datetime, timedelta, timezone


def _rng(*parts):
    seed = int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)
    return random.Random(seed)


def _gauss2(lat, lon, clat, clon, sigma_deg):
    return math.exp(-((lat - clat) ** 2 + (lon - clon) ** 2) / (2 * sigma_deg**2))


def _coastal(lat, lon):
    """~1 at the coast, ~0 well inland (crude: distance from a line along the coast)."""
    coast_lon = -124.2 + (42 - lat) * 0.62  # coast drifts east as you go south
    return max(0.0, 1.0 - (lon - coast_lon) / 2.0)


def _event_strength(day: date):
    """A synthetic offshore-wind event that peaks every ~9 days."""
    phase = (day.toordinal() % 9) / 9
    return max(0.0, math.sin(phase * math.pi)) ** 2


def daily_weather(lat, lon, day: date):
    south = (42 - lat) / 9.5  # 0 north .. 1 south
    coast = _coastal(lat, lon)
    doy = day.timetuple().tm_yday
    season = math.cos((doy - 200) / 365 * 2 * math.pi)  # +1 late July, -1 mid January
    rng = _rng("wx", round(lat, 2), round(lon, 2), day.isoformat())
    ev = _event_strength(day)
    socal_hot = _gauss2(lat, lon, 34.3, -118.6, 0.9) + 0.6 * _gauss2(lat, lon, 33.2, -116.8, 0.8)
    norcal_hot = 0.8 * _gauss2(lat, lon, 39.6, -121.7, 0.8)
    wind_spot = ev * (socal_hot + norcal_hot)

    tmax = 14 + 12 * season + 7 * south + 9 * (1 - coast) * (season + 1) / 2 + 5 * wind_spot + rng.gauss(0, 2)
    rh_min = 62 - 22 * south - 18 * (1 - coast) - 38 * wind_spot + 8 * (1 - season) / 2 + rng.gauss(0, 5)
    rh_min = min(95, max(4, rh_min))
    wind = 12 + 10 * coast + 45 * wind_spot + rng.gauss(0, 4)
    gusts = wind * 1.55 + rng.gauss(0, 4)
    wet_season = (1 - season) / 2  # 0 summer .. 1 winter
    p_rain = 0.03 + 0.45 * wet_season * (1 - 0.7 * south) * (0.6 + 0.4 * coast)
    precip = rng.expovariate(1 / (8 * (1 - 0.6 * south))) if rng.random() < p_rain else 0.0
    vpd = max(0.1, 0.6 + 2.4 * (tmax - 10) / 30 * (1 - rh_min / 100) * 1.6 + 1.5 * wind_spot)
    return {
        "temperature_2m_max": round(tmax, 1),
        "relative_humidity_2m_min": round(rh_min, 0),
        "wind_speed_10m_max": round(max(2, wind), 1),
        "wind_gusts_10m_max": round(max(4, gusts), 1),
        "wind_direction_10m_dominant": round((45 + rng.gauss(0, 20)) % 360 if wind_spot > 0.3 else (290 + rng.gauss(0, 40)) % 360),
        "precipitation_sum": round(precip, 1),
        "vapour_pressure_deficit_max": round(vpd, 2),
        "et0_fao_evapotranspiration": round(max(0.3, 1 + 5 * (season + 1) / 2 * (1 - coast * 0.4)), 2),
    }


def hourly_weather(lat, lon, day: date):
    """Deterministic synthetic hourly CAPE (J/kg) and snowfall (cm)."""
    season = math.cos((day.timetuple().tm_yday - 200) / 365 * 2 * math.pi)
    wind_event = _event_strength(day)
    sierra = _gauss2(lat, lon, 37.5, -119.3, 1.15)
    winter = max(0.0, -season)
    rng = _rng("hourly-wx", round(lat, 2), round(lon, 2), day.isoformat())
    hourly = []
    for hour in range(24):
        daylight = max(0.0, math.sin(math.pi * (hour - 6) / 14))
        cape = max(0.0, 100 + 1800 * max(0.0, season) * daylight
                   + 550 * wind_event * daylight + rng.gauss(0, 90))
        snow_chance = min(0.8, winter * sierra * 0.45)
        snowfall = rng.expovariate(1 / max(0.05, 0.35 * sierra)) if rng.random() < snow_chance else 0.0
        hourly.append({
            "time": f"{day.isoformat()}T{hour:02d}:00",
            "cape": round(cape, 1),
            "snowfall": round(snowfall, 2),
        })
    return hourly


def weather_series(points, start: date, end: date):
    out = []
    for lat, lon in points:
        days, d = {}, start
        while d <= end:
            hourly = hourly_weather(lat, lon, d)
            days[d.isoformat()] = {
                **daily_weather(lat, lon, d),
                "cape_max_jkg": max((h["cape"] for h in hourly), default=None),
                "snowfall_sum_cm": sum(h["snowfall"] for h in hourly),
            }
            d += timedelta(days=1)
        out.append(days)
    return out


def elevation(points):
    out = []
    for lat, lon in points:
        sierra = 2600 * _gauss2(lat, lon, 37.6 + (lon + 119.3) * -1.2, -119.3 + (lat - 37.6) * -0.9, 0.35)
        cascades = 2200 * _gauss2(lat, lon, 41.3, -122.2, 0.4)
        transverse = 1600 * _gauss2(lat, lon, 34.3, -117.8, 0.35) + 1200 * _gauss2(lat, lon, 34.5, -119.2, 0.3)
        coast_range = 700 * math.exp(-((lon - (-124.2 + (42 - lat) * 0.62) - 0.5) ** 2) / 0.2)
        noise = _rng("elev", round(lat, 3), round(lon, 3)).gauss(0, 120)
        out.append(max(0.0, 80 + sierra + cascades + transverse + coast_range + noise))
    return out


def ndvi(lat, lon):
    """Synthetic spring NDVI: low in the southeastern deserts, higher north and on the coast."""
    desert = min(1.0, _gauss2(lat, lon, 34.6, -116.0, 1.3) + 0.7 * _gauss2(lat, lon, 36.3, -116.8, 0.8)
                 + 0.6 * _gauss2(lat, lon, 33.0, -115.5, 0.6))
    v = 0.42 + 0.2 * max(0.0, (lat - 37.5) / 4.5) + 0.1 * _coastal(lat, lon) - 0.38 * desert
    return round(max(0.05, min(0.85, v + _rng("ndvi", round(lat, 2), round(lon, 2)).gauss(0, 0.04))), 3)


def fire_history(cells, years):
    """{cell_id: {year: fire_days}} with more fire in SoCal, foothills and NorCal."""
    hist = {}
    for c in cells:
        lat, lon = c["lat"], c["lon"]
        base = (
            30 * _gauss2(lat, lon, 34.4, -118.4, 1.0)
            + 25 * _gauss2(lat, lon, 39.8, -122.0, 1.0)
            + 18 * _gauss2(lat, lon, 37.3, -119.8, 0.9)
            + 12 * _gauss2(lat, lon, 33.0, -116.7, 0.7)
            + 2
        )
        hist[c["id"]] = {
            str(y): max(0, int(_rng("hist", c["id"], y).gauss(base, base * 0.5))) for y in years
        }
    return hist


def fire_detections(cells, risk_by_cell, start: date, days: int, salt="window"):
    """Synthetic detections, more likely in high-risk cells. Simulated: for UI testing only."""
    dets = []
    for c in cells:
        r = risk_by_cell.get(c["id"], 0) / 100
        for k in range(days):
            day = start + timedelta(days=k)
            rng = _rng("fire", salt, c["id"], day.isoformat())
            if rng.random() < 0.002 + 0.25 * r**3:
                n = rng.randint(2, 14)
                clat = c["lat"] + rng.uniform(-0.15, 0.15)
                clon = c["lon"] + rng.uniform(-0.15, 0.15)
                for _ in range(n):
                    t = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(
                        hours=rng.choice([9, 10, 20, 21]), minutes=rng.randint(0, 59)
                    )
                    sat = rng.choice([("NOAA-20", "VIIRS"), ("NOAA-21", "VIIRS"), ("Suomi NPP", "VIIRS"), ("Aqua", "MODIS")])
                    dets.append(
                        {
                            "lat": round(clat + rng.gauss(0, 0.012), 4),
                            "lon": round(clon + rng.gauss(0, 0.012), 4),
                            "time_utc": t.isoformat().replace("+00:00", "Z"),
                            "satellite": sat[0],
                            "instrument": sat[1],
                            "confidence": rng.choice(["nominal", "high", "high"]),
                            "frp": round(rng.expovariate(1 / 25), 1),
                            "daynight": "D" if t.hour >= 17 else "N",
                            "source": "demo",
                        }
                    )
    return dets


# --- Synthetic TLEs so the orbit layer works offline -------------------------------------

def _tle_checksum(line):
    return sum(int(ch) if ch.isdigit() else (1 if ch == "-" else 0) for ch in line[:68]) % 10


def synthetic_tle(name, norad, raan_deg, mean_anomaly_deg, mean_motion=14.195, incl=98.7):
    """A well-formed TLE for a sun-synchronous orbit with epoch = now. Not a real ephemeris."""
    now = datetime.now(timezone.utc)
    year = now.year % 100
    doy = (now - datetime(now.year, 1, 1, tzinfo=timezone.utc)).total_seconds() / 86400 + 1
    # Fixed-column layout (satellite.js parses by column position).
    l1 = (
        f"1 {norad:05d}U 00000A   {year:02d}{doy:012.8f}  .00000000  00000-0  00000-0 0  999"
    )
    l2 = (
        f"2 {norad:05d} {incl:8.4f} {raan_deg % 360:8.4f} 0001000 {90.0:8.4f} "
        f"{mean_anomaly_deg % 360:8.4f} {mean_motion:11.8f}{1:5d}"
    )
    assert len(l1) == 68 and len(l2) == 68, (len(l1), len(l2))
    return name, l1 + str(_tle_checksum(l1)), l2 + str(_tle_checksum(l2))
