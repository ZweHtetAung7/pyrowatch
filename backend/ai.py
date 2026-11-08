"""Gemini briefings and Q&A, plus optional ElevenLabs voice. Both degrade gracefully without keys."""
import json
import logging
import re

import httpx

from .config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, GEMINI_API_KEY, GEMINI_MODEL, HTTP_TIMEOUT

log = logging.getLogger("pyrowatch.ai")
_cache: dict = {}

SYSTEM = """You are PyroWatch, a fire-weather analyst writing for emergency managers and residents in California.
Rules:
- Use only the numbers in the context. Do not invent data, place names, road names, or resources.
- The score is a relative risk index from 0 to 100, NOT a probability. Never say a fire will definitely start.
- Lead with what matters most (the factors with the most points). Be concrete: quote the key numbers.
- If any source in the context has mode "demo", add the sentence "Note: based on simulated demo data."
- Plain text only. No markdown headings, no asterisks."""

BRIEFING_TASK = """Write a briefing for this area in under 110 words:
Line 1: "<CATEGORY> RISK — <short reason>" (category in capitals).
Then 2-3 sentences on why, naming the top drivers and their numbers, and how conditions change over the 48 h window.
Then "Recommended:" followed by two short bullet points starting with "• " (preparedness actions proportional to the risk level)."""


def _cell_context(cell, risk_payload, fires_in_cell=None):
    neighbors = [
        {"name": c["name"], "risk": c["risk"]}
        for c in risk_payload["cells"]
        if c["id"] != cell["id"] and abs(c["lat"] - cell["lat"]) <= 0.6 and abs(c["lon"] - cell["lon"]) <= 0.6
    ]
    return {
        "area": cell["name"],
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
_model = {"name": GEMINI_MODEL, "resolved": False}


class GeminiError(RuntimeError):
    """A Gemini API error with Google's own message (shown in the UI, so problems are fixable)."""

    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def _raise_for(r):
    if r.status_code < 400:
        return
    try:
        msg = r.json().get("error", {}).get("message", "") or r.text
    except ValueError:
        msg = r.text
    raise GeminiError(r.status_code, " ".join(msg.split())[:300])


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


def gemini_request(system, contents, tools=None, temperature=0.4):
    """One generateContent call (REST). Returns the parsed JSON response.
    If the configured model doesn't exist (retired names 404), switch once to the best
    available Flash model and remember it."""
    for attempt in range(2):
        model = _model["name"]
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": 1024},
        }
        if tools:
            body["tools"] = tools
        if "2.5" in model:  # skip "thinking" for a snappier demo on 2.5 models
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        r = httpx.post(f"{GEMINI_API}/models/{model}:generateContent",
                       headers={"x-goog-api-key": GEMINI_API_KEY}, json=body, timeout=HTTP_TIMEOUT)
        if r.status_code == 404 and attempt == 0 and not _model["resolved"]:
            _model["resolved"] = True
            choices = available_models()
            if choices and choices[0] != model:
                log.warning("Gemini model %s not found; switching to %s (set GEMINI_MODEL to keep it)", model, choices[0])
                _model["name"] = choices[0]
                continue
        _raise_for(r)
        return r.json()


def response_text(resp):
    parts = resp["candidates"][0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


def _gemini(prompt):
    text = response_text(gemini_request(SYSTEM, [{"role": "user", "parts": [{"text": prompt}]}]))
    if not text:
        raise RuntimeError("empty response")
    return text


def _template_briefing(ctx):
    top = sorted(ctx["factors"], key=lambda f: -f["points"])[:3]
    reason = ", ".join(f["factor"].lower() for f in top[:2])
    lines = [
        f"{ctx['category'].upper()} RISK — driven by {reason}.",
        f"Risk index {ctx['risk_index']:.0f}/100, ranked {ctx['rank']}. "
        + " ".join(f"{f['factor']}: {f['measured']}." for f in top),
    ]
    if ctx["risk_index"] >= 60:
        lines.append("Recommended:\n• Pre-position initial-attack crews and check evacuation routes.\n• Restrict equipment use and open burning.")
    elif ctx["risk_index"] >= 40:
        lines.append("Recommended:\n• Increase patrols and lookout coverage during the afternoon peak.\n• Remind residents about defensible space.")
    else:
        lines.append("Recommended:\n• Normal staffing.\n• Monitor the forecast for wind or humidity changes.")
    if "demo" in ctx["data_sources"].values():
        lines.append("Note: based on simulated demo data.")
    return "\n".join(lines)


def briefing(cell, risk_payload, fires_in_cell=None):
    key = ("brief", risk_payload["as_of"], cell["id"], cell["risk"], fires_in_cell)
    if key in _cache:
        return _cache[key]
    ctx = _cell_context(cell, risk_payload, fires_in_cell)
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


def tts(text):
    """MP3 bytes from ElevenLabs, or None when not configured."""
    if not ELEVENLABS_API_KEY:
        return None
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": ELEVENLABS_API_KEY},
        json={"text": text.replace("•", ""), "model_id": "eleven_flash_v2_5"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.content
