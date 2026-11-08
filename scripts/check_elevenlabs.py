"""Check that ElevenLabs voice works for PyroWatch, from the machine that will serve it.

  python scripts/check_elevenlabs.py

Makes one short clip (about 60 characters of credit), saves it to data/cache/voice_check.mp3, and
prints ElevenLabs' exact error if it fails. Run it on the server too: free-tier keys can be refused
from cloud servers even when they work on a laptop.
"""
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import ai  # noqa: E402
from backend.config import CACHE_DIR, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID  # noqa: E402

HINTS = {
    "quota_exceeded": "Out of credits this month: use another key, or the MLH ElevenLabs promo code.",
    "detected_unusual_activity": "ElevenLabs blocked the free tier from this machine's network (common on cloud "
                                 "servers and VPNs). Use a key on a paid or promo-code plan.",
    "missing_permissions": "This key is restricted: in ElevenLabs > API keys, allow Text to Speech for it.",
    "invalid_api_key": "The key is wrong or was deleted: copy it again from ElevenLabs > API keys.",
    "voice_not_found": "ELEVENLABS_VOICE_ID is not a voice this account can use: remove it from .env to use the default.",
}


def main():
    if not ELEVENLABS_API_KEY:
        sys.exit("ELEVENLABS_API_KEY is not set in .env")
    print(f"Voice {ELEVENLABS_VOICE_ID}, model {ai.TTS_MODEL}")

    try:
        r = httpx.get("https://api.elevenlabs.io/v1/user/subscription",
                      headers={"xi-api-key": ELEVENLABS_API_KEY}, timeout=20)
        if r.status_code < 400:
            sub = r.json()
            left = sub.get("character_limit", 0) - sub.get("character_count", 0)
            print(f"Plan: {sub.get('tier', '?')}, {left:,} credits left this period "
                  f"(a briefing costs very roughly 300-700)")
        else:
            print(f"(Could not read the plan: {ai._elevenlabs_reason(r)}; fine if the key can't read account info.)")
    except httpx.HTTPError as exc:
        print(f"(Could not read the plan: {exc})")

    try:
        audio = ai.tts("PyroWatch voice check. Satellites are watching California.", use_cache=False)
    except ai.VoiceError as exc:
        print(f"Voice FAILED: {exc}")
        hint = next((h for k, h in HINTS.items() if k in str(exc)), None)
        if hint:
            print(f"  -> {hint}")
        sys.exit(1)
    except httpx.HTTPError as exc:
        sys.exit(f"Voice FAILED: could not reach ElevenLabs ({exc})")
    out = CACHE_DIR / "voice_check.mp3"
    out.write_bytes(audio)
    print(f"Voice OK: {len(audio) / 1024:.0f} KB of audio saved to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
