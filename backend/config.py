"""Runtime configuration, read from environment variables (or a .env file)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
LEDGER_DIR = DATA_DIR / "ledger"
FRONTEND_DIR = ROOT / "frontend"
for d in (DATA_DIR, CACHE_DIR, LEDGER_DIR):
    d.mkdir(parents=True, exist_ok=True)

FIRMS_MAP_KEY = os.getenv("FIRMS_MAP_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip() or "gemini-3.7-flash"
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb").strip()
# Tiger Data (TimescaleDB) or any PostgreSQL: time series of predictions and fire detections.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
# Save each day's first live prediction to data/ledger/. Turn this on for ONE machine only (the model
# owner's, or the deployed server); if every teammate records, their copies overwrite each other in git.
RECORD_LEDGER = os.getenv("PYROWATCH_RECORD_LEDGER", "").strip().lower() in ("1", "true", "yes")

# Optional shared secret that PyroWatch Beacons send as X-Station-Token when posting readings.
STATION_TOKEN = os.getenv("STATION_TOKEN", "").strip()

# "auto": use live APIs, fall back to synthetic data per source if a call fails.
# "demo": never touch the network (offline rehearsals, bad venue wifi).
# "live": use live APIs only; errors surface instead of silently falling back.
DATA_MODE = os.getenv("PYROWATCH_DATA_MODE", "auto").strip().lower()

GRID_STEP_DEG = float(os.getenv("PYROWATCH_GRID_STEP", "0.5"))
TIMEZONE = "America/Los_Angeles"
HTTP_TIMEOUT = float(os.getenv("PYROWATCH_HTTP_TIMEOUT", "25"))
