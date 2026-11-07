"""PyroWatch API + static frontend.  Run:  uvicorn backend.main:app --reload"""
import logging
from datetime import date

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, risk, sources
from .config import DATA_MODE, ELEVENLABS_API_KEY, FIRMS_MAP_KEY, FRONTEND_DIR, GEMINI_API_KEY, GEMINI_MODEL, GRID_STEP_DEG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="PyroWatch", version="0.1")

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
        "gemini_model": GEMINI_MODEL if GEMINI_API_KEY else None,
        "has_tts": bool(ELEVENLABS_API_KEY),
        "has_ml_model": risk.load_model() is not None,
        "replay_presets": REPLAY_PRESETS,
        "factors": risk.FACTORS,
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
def get_briefing(cell_id: str, as_of: str | None = None):
    d = _parse_date(as_of)
    payload = risk.compute_risk(d)
    return ai.briefing(_cell(payload, cell_id), payload, _fires_in_cell(d, cell_id))


class AskBody(BaseModel):
    question: str
    cell_id: str | None = None
    as_of: str | None = None


@app.post("/api/ask")
def post_ask(body: AskBody):
    d = _parse_date(body.as_of)
    payload = risk.compute_risk(d)
    cell = _cell(payload, body.cell_id) if body.cell_id else None
    return ai.ask(body.question, cell, payload, _fires_in_cell(d, body.cell_id) if cell else None)


@app.get("/api/tts/{cell_id}")
def get_tts(cell_id: str, as_of: str | None = None):
    if not ELEVENLABS_API_KEY:
        raise HTTPException(404, "ELEVENLABS_API_KEY not set")
    b = get_briefing(cell_id, as_of)
    try:
        audio = ai.tts(b["text"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ElevenLabs request failed: {type(exc).__name__}")
    return Response(audio, media_type="audio/mpeg")


@app.get("/api/health")
def health():
    return {"ok": True}


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
