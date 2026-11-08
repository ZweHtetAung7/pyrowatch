"""Check that Gemini works for PyroWatch: the key, which models answer fast right now, a briefing-style
call and a tool call.

  python scripts/check_gemini.py

Prints Google's exact error if something fails, and which model to put in .env.
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import agent, ai  # noqa: E402
from backend.config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_TIMEOUT  # noqa: E402

PING = [{"role": "user", "parts": [{"text": "Reply with exactly: PyroWatch Gemini OK"}]}]


def probe(model):
    t0 = time.monotonic()
    try:
        ai.gemini_request("Answer in as few words as possible.", PING, model=model, fallback=False)
        return model, True, time.monotonic() - t0, ""
    except Exception as exc:  # noqa: BLE001
        return model, False, time.monotonic() - t0, str(exc).removeprefix(f"{model}: ")[:110]


def timed(label, fn):
    t0 = time.monotonic()
    try:
        out = fn()
        print(f"{label} ({ai.current_model()}, {time.monotonic() - t0:.1f} s): OK -> {out}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"{label} FAILED after {time.monotonic() - t0:.1f} s: {exc}")
        return False


def tool_call():
    resp = ai.gemini_request(ai.SYSTEM + agent.AGENT_RULES,
                             [{"role": "user", "parts": [{"text": "Which areas have the highest fire risk right now?"}]}],
                             tools=[{"functionDeclarations": agent.DECLARATIONS}], temperature=0.3)
    parts = resp["candidates"][0].get("content", {}).get("parts", [])
    calls = [p["functionCall"]["name"] for p in parts if "functionCall" in p]
    return f"Gemini asked for {calls or 'no tool (answered directly)'}"


def main():
    if not GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY is not set in .env")
    print(f"Configured model: {GEMINI_MODEL}")

    try:
        models = ai.available_models()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Listing models FAILED: {exc}\nCheck GEMINI_API_KEY and the internet connection.")
    flash = [m for m in models if "flash" in m]
    candidates = list(dict.fromkeys(([GEMINI_MODEL] if GEMINI_MODEL in models else []) + flash[:6]))
    if GEMINI_MODEL not in models:
        print(f"  NOTE: {GEMINI_MODEL} is not available to your key.")

    print(f"\nTiming {len(candidates)} models at once (each gets {GEMINI_TIMEOUT:.0f} s):")
    with ThreadPoolExecutor(len(candidates)) as pool:
        results = list(pool.map(probe, candidates))
    for model, ok, secs, err in results:
        print(f"  {model:<28} {'OK' if ok else '--'} {secs:5.1f} s  {err}")
    working = sorted((secs, model) for model, ok, secs, _ in results if ok)
    if not working:
        print("\nNo model answered. Google is overloaded or the network is blocking it; run this again in a few"
              "\nminutes. The app still works meanwhile: template briefings and the keyword router.")
        sys.exit(1)
    best = working[0][1]
    if best != GEMINI_MODEL:
        print(f"\n>>> Fastest right now: {best}. Put this in .env:  GEMINI_MODEL={best}")
    ai._model["name"] = best

    print()
    ok = timed("Briefing-style call", lambda: repr(ai._gemini("Reply with exactly: PyroWatch Gemini OK")[:60]))
    ok = timed("Tool call", tool_call) and ok
    print("\nAll good: restart the server." if ok else "\nFix the first failure above, then run this again.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
