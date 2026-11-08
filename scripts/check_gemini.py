"""Check that Gemini works for PyroWatch: the key, the model, a briefing-style call and a tool call.

  python scripts/check_gemini.py

Prints Google's exact error if something fails, and the models your key can use.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import agent, ai  # noqa: E402
from backend.config import GEMINI_API_KEY, GEMINI_MODEL  # noqa: E402


def main():
    if not GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY is not set in .env")
    print(f"Configured model: {GEMINI_MODEL}")
    ok = True

    try:
        models = ai.available_models()
        print(f"Your key can use {len(models)} models; best Flash candidates: {', '.join(models[:4])}")
        if GEMINI_MODEL not in models:
            print(f"  NOTE: {GEMINI_MODEL} is not in that list. Set GEMINI_MODEL={models[0]} in .env")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"Listing models FAILED: {exc}")

    try:
        text = ai._gemini("Reply with exactly: PyroWatch Gemini OK")
        print(f"Briefing-style call ({ai.current_model()}): OK -> {text[:60]!r}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"Briefing-style call FAILED: {exc}")

    try:
        resp = ai.gemini_request(ai.SYSTEM + agent.AGENT_RULES,
                                 [{"role": "user", "parts": [{"text": "Which areas have the highest fire risk right now?"}]}],
                                 tools=[{"functionDeclarations": agent.DECLARATIONS}], temperature=0.3)
        parts = resp["candidates"][0].get("content", {}).get("parts", [])
        calls = [p["functionCall"]["name"] for p in parts if "functionCall" in p]
        print(f"Tool call ({ai.current_model()}): OK -> Gemini asked for {calls or 'no tool (answered directly)'}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"Tool call FAILED: {exc}")

    print("\nAll good: restart the server." if ok else "\nFix the first failure above, then run this again.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
