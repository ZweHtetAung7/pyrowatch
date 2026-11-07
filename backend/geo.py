"""California outline, the analysis grid, and place names for grid cells."""
import math
from functools import lru_cache

from .config import GRID_STEP_DEG

# Simplified California outline as (lon, lat), clockwise from the NW corner.
# Good to a few km — fine for 0.5 degree cells. Swap in a real GeoJSON if you need more.
CA_POLYGON = [
    (-124.21, 42.00), (-120.00, 42.00), (-120.00, 39.00), (-114.63, 35.00),
    (-114.57, 34.80), (-114.40, 34.50), (-114.14, 34.30), (-114.43, 34.08),
    (-114.53, 33.93), (-114.50, 33.60), (-114.72, 33.40), (-114.70, 33.08),
    (-114.53, 32.97), (-114.47, 32.84), (-114.72, 32.72), (-117.12, 32.53),
    (-117.25, 32.70), (-117.28, 33.00), (-117.40, 33.20), (-117.60, 33.39),
    (-117.88, 33.60), (-118.10, 33.74), (-118.29, 33.71), (-118.41, 33.77),
    (-118.40, 33.90), (-118.52, 34.03), (-118.80, 34.01), (-119.22, 34.15),
    (-119.70, 34.41), (-120.47, 34.45), (-120.64, 34.58), (-120.62, 34.88),
    (-120.64, 35.14), (-120.88, 35.40), (-121.30, 35.68), (-121.90, 36.30),
    (-121.97, 36.58), (-121.80, 36.80), (-122.03, 36.96), (-122.40, 37.20),
    (-122.51, 37.53), (-122.51, 37.78), (-122.98, 38.00), (-123.07, 38.30),
    (-123.74, 38.95), (-123.82, 39.40), (-123.85, 39.85), (-124.40, 40.44),
    (-124.18, 40.80), (-124.10, 41.20), (-124.22, 41.75), (-124.21, 42.00),
]
CA_BBOX = (-124.5, 32.5, -114.0, 42.1)  # west, south, east, north

PLACES = [
    ("Crescent City", 41.76, -124.20), ("Yreka", 41.73, -122.63), ("Mount Shasta", 41.31, -122.31),
    ("Alturas", 41.49, -120.54), ("Eureka", 40.80, -124.16), ("Weaverville", 40.73, -122.94),
    ("Redding", 40.59, -122.39), ("Susanville", 40.42, -120.65), ("Chico", 39.73, -121.84),
    ("Paradise", 39.76, -121.62), ("Mendocino", 39.31, -123.80), ("Ukiah", 39.15, -123.21),
    ("Clearlake", 38.96, -122.63), ("Truckee", 39.33, -120.18), ("Grass Valley", 39.22, -121.06),
    ("Auburn", 38.90, -121.08), ("South Lake Tahoe", 38.94, -119.98), ("Placerville", 38.73, -120.80),
    ("Santa Rosa", 38.44, -122.71), ("Napa", 38.30, -122.29), ("Sacramento", 38.58, -121.49),
    ("Stockton", 37.96, -121.29), ("San Francisco", 37.77, -122.42), ("Oakland", 37.80, -122.27),
    ("San Jose", 37.34, -121.89), ("Santa Cruz", 36.97, -122.03), ("Monterey", 36.60, -121.89),
    ("Salinas", 36.68, -121.66), ("Modesto", 37.64, -120.99), ("Merced", 37.30, -120.48),
    ("Yosemite Valley", 37.75, -119.59), ("Mammoth Lakes", 37.65, -118.97), ("Bishop", 37.36, -118.40),
    ("Fresno", 36.74, -119.79), ("Visalia", 36.33, -119.29), ("Big Sur", 36.27, -121.81),
    ("Paso Robles", 35.63, -120.69), ("San Luis Obispo", 35.28, -120.66), ("Bakersfield", 35.37, -119.02),
    ("Lone Pine", 36.61, -118.06), ("Furnace Creek", 36.46, -116.87), ("Ridgecrest", 35.62, -117.67),
    ("Baker", 35.27, -116.07), ("Santa Barbara", 34.42, -119.70), ("Ventura", 34.27, -119.23),
    ("Lancaster", 34.69, -118.15), ("Los Angeles", 34.05, -118.24), ("Malibu", 34.03, -118.78),
    ("Pasadena", 34.15, -118.14), ("San Bernardino", 34.11, -117.29), ("Barstow", 34.90, -117.02),
    ("Needles", 34.85, -114.61), ("Joshua Tree", 34.13, -116.31), ("Palm Springs", 33.83, -116.55),
    ("Riverside", 33.98, -117.38), ("Irvine", 33.68, -117.83), ("Temecula", 33.49, -117.15),
    ("Julian", 33.08, -116.60), ("San Diego", 32.72, -117.16), ("El Centro", 32.79, -115.56),
    ("Blythe", 33.61, -114.60),
]

COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def compass(deg):
    return COMPASS[int(((deg % 360) + 22.5) // 45) % 8]


def point_in_polygon(lon, lat, poly=CA_POLYGON):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def in_california(lat, lon):
    return point_in_polygon(lon, lat)


def _clip(subject, inside, intersect):
    out = subject
    for edge in range(4):
        inp, out = out, []
        if not inp:
            break
        prev = inp[-1]
        for cur in inp:
            if inside(cur, edge):
                if not inside(prev, edge):
                    out.append(intersect(prev, cur, edge))
                out.append(cur)
            elif inside(prev, edge):
                out.append(intersect(prev, cur, edge))
            prev = cur
    return out


def clip_polygon_to_box(poly, west, south, east, north):
    """Sutherland-Hodgman: clip an arbitrary polygon against an axis-aligned box."""

    def inside(p, e):
        return (p[0] >= west, p[0] <= east, p[1] >= south, p[1] <= north)[e]

    def intersect(a, b, e):
        (x1, y1), (x2, y2) = a, b
        if e in (0, 1):
            x = west if e == 0 else east
            t = (x - x1) / ((x2 - x1) or 1e-12)
            return (x, y1 + t * (y2 - y1))
        y = south if e == 2 else north
        t = (y - y1) / ((y2 - y1) or 1e-12)
        return (x1 + t * (x2 - x1), y)

    return _clip(list(poly[:-1]), inside, intersect)


def describe_location(lat, lon):
    best = min(PLACES, key=lambda p: haversine_km(lat, lon, p[1], p[2]))
    d = haversine_km(lat, lon, best[1], best[2])
    if d < 25:
        return f"{best[0]} area"
    return f"{round(d / 5) * 5:.0f} km {compass(bearing_deg(best[1], best[2], lat, lon))} of {best[0]}"


@lru_cache(maxsize=4)
def build_grid(step=GRID_STEP_DEG):
    """Grid cells covering California. Each cell carries the CA-clipped shape for the map,
    an anchor point (mean of its on-land sample points) used for weather queries, and a
    3x3 set of on-land sample points used for terrain."""
    west, south, east, north = CA_BBOX
    cells = []
    n_rows = int(math.ceil((north - south) / step))
    n_cols = int(math.ceil((east - west) / step))
    sub = 5
    for r in range(n_rows):
        for c in range(n_cols):
            s, w = south + r * step, west + c * step
            n, e = s + step, w + step
            samples = [
                (s + (i + 0.5) * step / sub, w + (j + 0.5) * step / sub)
                for i in range(sub)
                for j in range(sub)
            ]
            land = [(la, lo) for la, lo in samples if in_california(la, lo)]
            if len(land) < 3:  # skip slivers that are almost all ocean / out of state
                continue
            shape = clip_polygon_to_box(CA_POLYGON, w, s, e, n)
            if len(shape) < 3:
                continue
            lat = sum(p[0] for p in land) / len(land)
            lon = sum(p[1] for p in land) / len(land)
            if not in_california(lat, lon):  # concave cell: snap anchor to nearest land sample
                lat, lon = min(land, key=lambda p: (p[0] - lat) ** 2 + (p[1] - lon) ** 2)
            terrain_pts = land[:: max(1, len(land) // 9)][:9]
            cells.append(
                {
                    "id": f"r{r:02d}c{c:02d}",
                    "row": r,
                    "col": c,
                    "lat": round(lat, 4),
                    "lon": round(lon, 4),
                    "bounds": [[s, w], [n, e]],
                    "shape": [[round(y, 4), round(x, 4)] for x, y in shape],
                    "land_fraction": round(len(land) / len(samples), 2),
                    "terrain_points": terrain_pts,
                    "name": describe_location(lat, lon),
                }
            )
    return tuple(cells)


@lru_cache(maxsize=4)
def cell_index(step=GRID_STEP_DEG):
    """(row, col) -> cell id, for binning fire detections."""
    return {(c["row"], c["col"]): c["id"] for c in build_grid(step)}


def cell_for_point(lat, lon, step=GRID_STEP_DEG):
    west, south, _, _ = CA_BBOX
    r, c = int((lat - south) // step), int((lon - west) // step)
    return cell_index(step).get((r, c))
