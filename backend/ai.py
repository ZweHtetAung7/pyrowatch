"""Gemini briefings and Q&A, plus optional ElevenLabs voice. Both degrade gracefully without keys."""
import json
import logging

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
        "ml_7day_ignition_probability": cell.get("ml_prob"),
        "active_fire_detections_in_area_last_48h": fires_in_cell,
        "neighbors": neighbors,
        "statewide_top": risk_payload["top"][:5],
        "data_sources": {k: v.get("mode") for k, v in risk_payload["sources"].items()},
    }


def _gemini(prompt):
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1024},
    }
    if "2.5" in GEMINI_MODEL:  # skip "thinking" for a snappier demo on 2.5 models
        body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": GEMINI_API_KEY},
        json=body,
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
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
            result = {"engine": GEMINI_MODEL, "text": _gemini(f"{BRIEFING_TASK}\n\nContext (JSON):\n{json.dumps(ctx)}")}
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini briefing failed: %s", exc)
            result["error"] = f"Gemini unavailable ({type(exc).__name__}); showing template briefing"
    if not result["text"]:
        result["text"] = _template_briefing(ctx)
    _cache[key] = result
    return result


def ask(question, cell, risk_payload, fires_in_cell=None):
    question = (question or "").strip()[:500]
    ctx = {
        "selected_area": _cell_context(cell, risk_payload, fires_in_cell) if cell else None,
        "statewide_top": risk_payload["top"],
        "category_counts": risk_payload["counts"],
        "model": risk_payload["model"],
    }
    if not GEMINI_API_KEY:
        return {"engine": "none", "text": "Set GEMINI_API_KEY to enable questions. Meanwhile, the factor breakdown shows exactly how the score is built."}
    try:
        text = _gemini(
            "Answer the user's question in under 120 words using only the context. "
            "If the question compares areas, compare their factor points.\n\n"
            f"Question: {question}\n\nContext (JSON):\n{json.dumps(ctx)}"
        )
        return {"engine": GEMINI_MODEL, "text": text}
    except Exception as exc:  # noqa: BLE001
        log.warning("Gemini ask failed: %s", exc)
        return {"engine": "error", "text": f"Gemini request failed ({type(exc).__name__}). Try again in a moment."}


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
