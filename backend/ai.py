"""Gemini briefings and Q&A, plus optional ElevenLabs voice. Both degrade gracefully without keys."""
import hashlib
import json
import logging
import re
import time

import httpx

from .config import (CACHE_DIR, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, GEMINI_API_KEY, GEMINI_MODEL,
                     GEMINI_TIMEOUT, HTTP_TIMEOUT)

log = logging.getLogger("pyrowatch.ai")
_cache: dict = {}

SYSTEM = """You are PyroWatch, a fire-weather analyst writing for emergency managers and residents in California.
Rules:
- Use only the numbers in the context. Do not invent data, place names, road names, or resources.
- Every score is a relative index from 0 to 100, NOT a probability or official warning. Never claim a hazard will definitely occur.
- CAPE indicates atmospheric instability, not that lightning or a thunderstorm will occur. Snowfall, wind and temperature are forecast measurements, not an official blizzard warning.
- Lead with what matters most (the factors with the most points). Be concrete: quote the key numbers.
- If any source in the context has mode "demo", add the sentence "Note: based on simulated demo data."
- Plain text only. No markdown headings, no asterisks."""

BRIEFING_TASK = """Write a briefing for the selected hazard in this area in under 110 words:
Line 1: "<CATEGORY> <HAZARD> — <short reason>" (category in capitals).
Then 2-3 sentences on the selected hazard's score, top drivers and measurements over the 48 h window.
Then "Recommended:" followed by two short bullet points starting with "• " (practical, proportional preparedness actions).
Distinguish relative indices from probabilities and official warnings."""


def _cell_context(cell, risk_payload, fires_in_cell=None, hazard="wildfire"):
    hazard_data = cell.get("hazards", {}).get(hazard) or {
        "score": cell.get("risk"), "category": cell.get("category"), "factors": cell.get("factors", [])
    }
    neighbors = [
        {"name": c["name"], "score": (c.get("hazards", {}).get(hazard) or {}).get("score", c.get("risk"))}
        for c in risk_payload["cells"]
        if c["id"] != cell["id"] and abs(c["lat"] - cell["lat"]) <= 0.6 and abs(c["lon"] - cell["lon"]) <= 0.6
    ]
    return {
        "area": cell["name"],
        "selected_hazard": hazard,
        "hazard_score": hazard_data.get("score"),
        "hazard_category": hazard_data.get("category"),
        "hazard_factors": [{"factor": f["label"], "points": f["points"], "score_0_1": f.get("value"), "measured": f["explain"]}
                           for f in hazard_data.get("factors", [])],
        "available_hazard_scores": {
            key: {"score": data.get("score"), "category": data.get("category")}
            for key, data in cell.get("hazards", {}).items()
        },
        "as_of": risk_payload["as_of"],
        "window": risk_payload["window"],
        "risk_index": cell["risk"],
        "category": cell["category"],
        "rank": f"{cell['rank']} of {len(risk_payload['cells'])} areas statewide",
        "factors": [{"factor": f["label"], "points": f["points"], "score_0_1": f["value"], "measured": f["explain"]} for f in cell["factors"]],
        "fuel_gate_multiplier": cell["fuel_gate"],
        "vegetation_multiplier": cell.get("veg_gate"),
        "fosberg_fire_weather_index_0_100": cell["raw"].get("ffwi"),
        "vegetation": cell.get("vegetation"),
        "ml_7day_ignition_probability": cell.get("ml_prob"),
        "active_fire_detections_in_area_last_48h": fires_in_cell,
        "neighbors": neighbors,
        "statewide_top": risk_payload["top"][:5],
        "data_sources": {k: v.get("mode") for k, v in risk_payload["sources"].items()},
    }


GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
_model = {"name": GEMINI_MODEL}


class GeminiError(RuntimeError):
    """A Gemini API error with Google's own message (shown in the UI, so problems are fixable)."""

    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status


def _error_message(r):
    try:
        msg = r.json().get("error", {}).get("message", "") or r.text
    except ValueError:
        msg = r.text
    return " ".join(msg.split())[:300]


def _raise_for(r):
    if r.status_code >= 400:
        raise GeminiError(r.status_code, _error_message(r))


def available_models():
    """Model names this key can call with generateContent, best Flash candidates first."""
    r = httpx.get(f"{GEMINI_API}/models", headers={"x-goog-api-key": GEMINI_API_KEY},
                  params={"pageSize": 200}, timeout=HTTP_TIMEOUT)
    _raise_for(r)
    names = [m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    skip = ("image", "tts", "audio", "live", "embedding", "vision", "learnlm", "gemma", "robotics")
    flash = [n for n in names if "flash" in n and not any(s in n for s in skip)]

    def key(n):
        version = [int(x) for x in re.findall(r"\d+", n.split("-flash")[0])] or [0]
        return (any(t in n for t in ("preview", "exp")), "lite" in n, [-v for v in version], n)

    return sorted(flash, key=key) + [n for n in names if n not in flash]


def current_model():
    return _model["name"]


def _thinking_options(model):
    """Thinking settings to try, lightest first. Briefings and tool routing don't need deep thinking,
    and on a busy day it is most of the wait. A model that rejects one setting gets the next."""
    if "2.5" in model:
        return [{"thinkingBudget": 0}, None]
    if re.match(r"gemini-([3-9]|\d\d)", model):
        return ([{"thinkingLevel": "minimal"}] if "flash" in model else []) + [{"thinkingLevel": "low"}, None]
    return [None]


_thinking_step: dict = {}  # model -> index of the thinking setting that model accepts
RETRYABLE = (404, 429, 500, 503)  # retired model, quota or overload: another model may work
MAX_MODELS = 4  # models tried per request
TOTAL_BUDGET_S = 45  # stop trying models after this long and let the caller fall back


def _post(model, body, timeout):
    """One generateContent call to one model, adapting the thinking setting if the model rejects it."""
    while True:
        options = _thinking_options(model)
        cfg = options[min(_thinking_step.get(model, 0), len(options) - 1)]
        gen = {k: v for k, v in body["generationConfig"].items() if k != "thinkingConfig"}
        if cfg:
            gen["thinkingConfig"] = cfg
        r = httpx.post(f"{GEMINI_API}/models/{model}:generateContent",
                       headers={"x-goog-api-key": GEMINI_API_KEY}, json={**body, "generationConfig": gen},
                       timeout=httpx.Timeout(timeout, connect=10))
        msg = _error_message(r).lower() if r.status_code == 400 else ""
        if cfg and ("think" in msg or "budget" in msg):
            _thinking_step[model] = _thinking_step.get(model, 0) + 1
            continue
        return r


def gemini_request(system, contents, tools=None, temperature=0.4, model=None, fallback=True, timeout=None):
    """One generateContent call (REST). Returns the parsed JSON response.

    If the model is retired (404), rate-limited (429), overloaded (500/503) or too slow to answer,
    try the next model this key can use (best Flash first, up to MAX_MODELS) and remember the first
    one that works. Quotas are per model, so switching also helps with 429s during a busy demo."""
    model = model or _model["name"]
    timeout = timeout or GEMINI_TIMEOUT
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 2048},
    }
    if tools:
        body["tools"] = tools
    deadline = time.monotonic() + max(TOTAL_BUDGET_S, timeout)
    tried, failures, candidates = [], [], None
    while True:
        wait = min(timeout, max(deadline - time.monotonic(), 1))
        try:
            r = _post(model, body, wait)
            error = None if r.status_code < 400 else GeminiError(r.status_code, _error_message(r))
            retryable = r.status_code in RETRYABLE
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise GeminiError(None, f"could not reach Google ({exc}); check the internet connection") from exc
        except httpx.TimeoutException:
            error = GeminiError(None, f"no answer within {wait:.0f} s (busy?)")
            retryable = True
        if error is None:
            if fallback and model != _model["name"]:
                log.warning("Gemini: now using %s (set GEMINI_MODEL=%s in .env to start with it)", model, model)
                _model["name"] = model
            return r.json()
        tried.append(model)
        failures.append(f"{model}: {str(error)[:140]}")
        if fallback and retryable and len(tried) < MAX_MODELS and deadline - time.monotonic() > 3:
            if candidates is None:
                try:
                    candidates = available_models()
                except Exception:  # noqa: BLE001 - can't list models: report the original error
                    candidates = []
            nxt = next((m for m in candidates if m not in tried), None)
            if nxt:
                log.warning("Gemini model %s failed (%s); trying %s", model, error, nxt)
                model = nxt
                continue
        if len(failures) > 1 or error.status is None:
            error = GeminiError(None, "; ".join(failures))
        raise error


def response_text(resp):
    parts = resp["candidates"][0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


def _gemini(prompt):
    text = response_text(gemini_request(SYSTEM, [{"role": "user", "parts": [{"text": prompt}]}]))
    if not text:
        raise RuntimeError("empty response")
    return text


def _template_briefing(ctx):
    hazard = ctx.get("selected_hazard", "wildfire")
    top = sorted(ctx.get("hazard_factors", ctx["factors"]), key=lambda f: -f["points"])[:3]
    reason = ", ".join(f["factor"].lower() for f in top[:2]) or "forecast measurements unavailable"
    score = ctx.get("hazard_score")
    category = ctx.get("hazard_category") or "Unavailable"
    label = {"wildfire": "WILDFIRE", "thunderstorm": "THUNDERSTORM", "blizzard": "BLIZZARD"}.get(hazard, hazard.upper())
    if score is None:
        return f"{label} SCORE UNAVAILABLE — required forecast measurements are not available for this area."
    lines = [
        f"{category.upper()} {label} — driven by {reason}.",
        f"Relative index {score:.0f}/100 for {ctx['window']}. "
        + " ".join(f"{f['factor']}: {f['measured']}." for f in top),
    ]
    if hazard == "thunderstorm":
        lines.append("CAPE describes atmospheric instability; it is not a probability of lightning or a thunderstorm.")
        lines.append("Recommended:\n• Monitor official weather alerts and radar for developing storms.\n• If thunder is heard, move indoors and avoid exposed high ground.")
    elif hazard == "blizzard":
        lines.append("This is a screening index, not an official blizzard warning.")
        lines.append("Recommended:\n• Check official winter-weather and road advisories before travel.\n• Prepare warm layers, food, water and a charged phone if travel is necessary.")
    elif score >= 60:
        lines.append("Recommended:\n• Pre-position initial-attack crews and check evacuation routes.\n• Restrict equipment use and open burning.")
    elif score >= 40:
        lines.append("Recommended:\n• Increase patrols and lookout coverage during the afternoon peak.\n• Remind residents about defensible space.")
    else:
        lines.append("Recommended:\n• Normal staffing.\n• Monitor the forecast for wind or humidity changes.")
    if "demo" in ctx["data_sources"].values():
        lines.append("Note: based on simulated demo data.")
    return "\n".join(lines)


def briefing(cell, risk_payload, fires_in_cell=None, hazard="wildfire"):
    key = ("brief", risk_payload["as_of"], cell["id"], cell["risk"], fires_in_cell, hazard)
    if key in _cache:
        return _cache[key]
    ctx = _cell_context(cell, risk_payload, fires_in_cell, hazard)
    result = {"engine": "template", "text": None}
    if GEMINI_API_KEY:
        try:
            result = {"engine": current_model(), "text": _gemini(f"{BRIEFING_TASK}\n\nContext (JSON):\n{json.dumps(ctx)}")}
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini briefing failed: %s", exc)
            result["error"] = f"Gemini failed, showing the template briefing. {exc}"[:400]
    if not result["text"]:
        result["text"] = _template_briefing(ctx)
    _cache[key] = result
    return result


TTS_MODEL = "eleven_flash_v2_5"


class VoiceError(RuntimeError):
    """An ElevenLabs API error with ElevenLabs' own reason (quota, permissions, blocked free tier...)."""


def _elevenlabs_reason(r):
    try:
        detail = r.json().get("detail", r.text)
    except ValueError:
        detail = r.text
    if isinstance(detail, dict):
        detail = " ".join(str(detail.get(k, "")) for k in ("status", "message")).strip() or str(detail)
    elif isinstance(detail, list):
        detail = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
    return f"HTTP {r.status_code}: {' '.join(str(detail).split())[:300]}"


def tts(text, use_cache=True):
    """MP3 bytes from ElevenLabs, or None when not configured. Each clip is saved to data/cache, so
    replaying the same briefing (a judge clicking Listen twice) costs no extra credits."""
    if not ELEVENLABS_API_KEY:
        return None
    text = text.replace("•", "")
    digest = hashlib.md5(f"{ELEVENLABS_VOICE_ID}|{TTS_MODEL}|{text}".encode()).hexdigest()
    path = CACHE_DIR / f"tts_{digest}.mp3"
    if use_cache and path.exists():
        return path.read_bytes()
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": ELEVENLABS_API_KEY},
        json={"text": text, "model_id": TTS_MODEL},
        timeout=HTTP_TIMEOUT,
    )
    if r.status_code >= 400:
        raise VoiceError(_elevenlabs_reason(r))
    if use_cache:
        path.write_bytes(r.content)
    return r.content
