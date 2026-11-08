"""PyroWatch API + static frontend.  Run:  uvicorn backend.main:app --reload"""
import logging
import threading
import unicodedata
from datetime import date

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, ai, ground, risk, sources, store
from .config import (DATA_MODE, ELEVENLABS_API_KEY, FIRMS_MAP_KEY, FRONTEND_DIR, GEMINI_API_KEY,
                     GRID_STEP_DEG, STATION_TOKEN)
from .geo import CA_BBOX, PLACES, cell_for_point, haversine_km

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pyrowatch.main")
app = FastAPI(title="PyroWatch", version="0.1")


@app.on_event("startup")
def _startup():
    """Hit external APIs once at load, then serve from DB/cache; keep a 2-week rolling window."""

    def warm():
        try:
            log.info("warmup: refreshing live data once (DB/cache first)...")
            risk.compute_risk(None)
            risk.fires(None, days=2)
            store.prune_history(14)
            log.info("warmup: done (database=%s)", store.status())
        except Exception as exc:  # noqa: BLE001
            log.warning("warmup failed (app still serving): %s", exc)

    threading.Thread(target=warm, daemon=True, name="warmup").start()

REPLAY_PRESETS = [
    {"date": "2025-01-06", "label": "LA windstorm — Palisades & Eaton fires (started Jan 7, 2025)"},
    {"date": "2018-11-07", "label": "Camp Fire, Paradise (started Nov 8, 2018)"},
    {"date": "2017-10-08", "label": "Wine Country fires — Tubbs (started Oct 8, 2017)"},
    {"date": "2020-08-16", "label": "Lightning siege — SCU & LNU complexes (Aug 16, 2020)"},
]


def _parse_date(s: str | None):
    if not s:
        return None
    try:
        d = date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, "as_of must be YYYY-MM-DD")
    if d > sources.today_local():
        raise HTTPException(400, "as_of cannot be in the future")
    if d.year < 2001:
        raise HTTPException(400, "satellite fire records start in 2001")
    return d


def _cell(payload, cell_id):
    cell = next((c for c in payload["cells"] if c["id"] == cell_id), None)
    if not cell:
        raise HTTPException(404, f"unknown cell {cell_id}")
    return cell


def _fires_in_cell(as_of, cell_id):
    try:
        f = risk.fires(as_of, days=2)
        return sum(1 for d in f["detections"] if d.get("cell_id") == cell_id)
    except Exception:  # noqa: BLE001
        return None


@app.get("/api/config")
def config():
    return {
        "data_mode": DATA_MODE,
        "today": sources.today_local().isoformat(),
        "grid_step_deg": GRID_STEP_DEG,
        "has_firms_key": bool(FIRMS_MAP_KEY),
        "has_gemini": bool(GEMINI_API_KEY),
        "gemini_model": ai.current_model() if GEMINI_API_KEY else None,
        "has_tts": bool(ELEVENLABS_API_KEY),
        "agent": "gemini" if GEMINI_API_KEY else "offline router",
        "has_ml_model": risk.load_model() is not None,
        "database": store.status(),
        "replay_presets": REPLAY_PRESETS,
        "places": [[n, la, lo] for n, la, lo in PLACES],
        "grid": {"west": CA_BBOX[0], "south": CA_BBOX[1], "step": GRID_STEP_DEG},
        "factors": risk.FACTORS,
        "hazards": risk.HAZARDS,
    }


@app.get("/api/risk")
def get_risk(as_of: str | None = None):
    return risk.compute_risk(_parse_date(as_of))


@app.get("/api/fires")
def get_fires(as_of: str | None = None, days: int = Query(2, ge=1, le=10)):
    return risk.fires(_parse_date(as_of), days)


@app.get("/api/validate")
def get_validate(as_of: str | None = None, top_fraction: float = Query(0.2, gt=0, lt=1)):
    return risk.validate(_parse_date(as_of), top_fraction)


@app.get("/api/satellites")
def get_satellites():
    tles, status = sources.get_satellite_tles()
    return {"source": status, "satellites": tles}


@app.get("/api/briefing/{cell_id}")
def get_briefing(cell_id: str, as_of: str | None = None, hazard: str = "wildfire"):
    if hazard not in risk.HAZARDS:
        raise HTTPException(400, f"hazard must be one of: {', '.join(risk.HAZARDS)}")
    d = _parse_date(as_of)
    payload = risk.compute_risk(d)
    return ai.briefing(_cell(payload, cell_id), payload, _fires_in_cell(d, cell_id), hazard)


class AskBody(BaseModel):
    question: str
    cell_id: str | None = None
    as_of: str | None = None


@app.post("/api/ask")
def post_ask(body: AskBody):
    """Gemini function-calling agent over PyroWatch's own data. Returns the answer + tool trace."""
    return agent.run(body.question, _parse_date(body.as_of), body.cell_id)


@app.get("/api/tts/{cell_id}")
def get_tts(cell_id: str, as_of: str | None = None, hazard: str = "wildfire"):
    if not ELEVENLABS_API_KEY:
        raise HTTPException(404, "ELEVENLABS_API_KEY not set")
    b = get_briefing(cell_id, as_of, hazard)
    try:
        audio = ai.tts(b["text"])
    except ai.VoiceError as exc:
        raise HTTPException(502, f"ElevenLabs {exc}")
    except Exception as exc:  # noqa: BLE001 - network errors; no key or headers in the message
        raise HTTPException(502, f"ElevenLabs request failed: {type(exc).__name__}: {exc}"[:300])
    return Response(audio, media_type="audio/mpeg")


@app.get("/api/history/{cell_id}")
def get_history(cell_id: str, days: int = Query(14, ge=1, le=365)):
    """Risk over time + daily fire detections for one area, from Tiger Data (if configured)."""
    return store.cell_history(cell_id, days)


# ------------------------------------------------------------------ hardware: PyroWatch Beacon

LEVELS = ["Low", "Moderate", "High", "Very high", "Extreme"]


def _ascii(s):
    """OLED fonts are ASCII-only."""
    for a, b in (("°", ""), ("±", "+/-"), ("·", "-"), ("–", "-"), ("→", "->")):
        s = s.replace(a, b)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


@app.get("/api/beacon")
def get_beacon(lat: float, lon: float, radius_km: float = Query(50, ge=5, le=300)):
    """Compact live status for one location, sized for a microcontroller."""
    cid = cell_for_point(lat, lon)
    if not cid:
        raise HTTPException(404, "location is outside PyroWatch's California grid")
    payload = risk.compute_risk(None)
    cell = _cell(payload, cid)
    fires = risk.fires(None, 2)
    near = sorted((haversine_km(lat, lon, d["lat"], d["lon"]), d) for d in fires["detections"])
    near = [(k, d) for k, d in near if k <= radius_km]
    top = max(cell["factors"], key=lambda f: f["points"])
    demo = fires["source"]["mode"] != "live" or any(s["mode"] != "live" for s in payload["sources"].values())
    return {
        "area": _ascii(cell["name"]),
        "cell_id": cid,
        "risk": round(cell["risk"]),
        "category": cell["category"],
        "level": LEVELS.index(cell["category"]),
        "rank": cell["rank"],
        "of": len(payload["cells"]),
        "fires_nearby": len(near),
        "radius_km": radius_km,
        "nearest_fire_km": round(near[0][0], 1) if near else None,
        "latest_fire_utc": max((d["time_utc"] for _, d in near), default=None),
        "top_factor": _ascii(f"{top['label']}: {top['explain']}")[:90],
        "hazards": {
            name: {"score": value["score"], "category": value["category"]}
            for name, value in cell.get("hazards", {}).items()
        },
        "as_of": payload["as_of"],
        "generated_at": payload["generated_at"],
        "demo": demo,
    }


class Reading(BaseModel):
    lat: float
    lon: float
    temp_c: float | None = None
    rh_pct: float | None = None
    smoke_raw: float | None = None
    label: str | None = None


@app.post("/api/stations/{station_id}")
def post_station(station_id: str, reading: Reading, x_station_token: str | None = Header(None)):
    """A ground station (PyroWatch Beacon with a sensor) reports a reading."""
    if STATION_TOKEN and x_station_token != STATION_TOKEN:
        raise HTTPException(401, "bad or missing X-Station-Token")
    try:
        return ground.record(station_id, reading.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/stations")
def get_stations(max_age_hours: float = Query(24, gt=0, le=720)):
    return {"stations": ground.list_stations(max_age_hours)}


@app.get("/api/stations/{station_id}/history")
def get_station_history(station_id: str):
    h = ground.history(station_id)
    if h is None:
        raise HTTPException(404, "unknown station")
    return {"id": station_id, "readings": h}


@app.get("/api/health")
@app.get("/health")
def health():
    return {"ok": True}


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
