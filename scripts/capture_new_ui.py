"""Screenshots of the current interface (four views, three hazards) from your running app.

  python scripts/capture_new_ui.py            # app running on http://localhost:8000 with live data

Writes screenshots/new/*.png at 2x resolution plus screenshots/new/CAPTIONS.md. Run it on the machine
with live data and keys: it warns if anything on screen is demo data. Takes about 2 minutes.
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
QUESTION = "Where is fire risk highest right now, and when does a satellite next pass over it?"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--out", default="screenshots/new")
    ap.add_argument("--replay-date", default="2025-01-06")
    ap.add_argument("--replay-area", default="25 km N of Malibu")
    args = ap.parse_args()
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    captions, failed = [], []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2).new_page()

        def shot(name, caption):
            page.evaluate("() => document.activeElement && document.activeElement.blur()")
            page.screenshot(path=str(out / f"{name}.png"))
            captions.append((f"{name}.png", caption))
            print(f"  saved {args.out}/{name}.png")

        def settle(ms=1200):
            page.wait_for_function("() => window.__pw.store.state.risk && document.querySelector('#loading').hidden",
                                   timeout=240000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)  # map tiles
            except PWTimeout:
                pass
            page.wait_for_timeout(ms)

        def step(name, fn):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - keep going, report at the end
                failed.append(f"{name}: {str(exc).splitlines()[0][:140]}")
                print(f"  FAILED {name}: {str(exc).splitlines()[0][:140]}")

        js = lambda code, arg=None: page.evaluate(code, arg)  # noqa: E731
        print(f"Opening {args.url} (the first load can take a minute if the server just started)...")
        page.goto(args.url)
        page.wait_for_selector("#map")
        js("async () => { window.__pw = { store: await import('/app/store.js'), ui: await import('/app/ui.js') }; }")
        settle(2000)
        status = page.text_content("#data-status") or ""
        if "demo" in status.lower() or "unavailable" in status.lower():
            print(f"WARNING: the app says '{status.strip()}'. Use live data for anything you submit.")
        n = js("() => window.__pw.store.state.risk.cells.length")

        def hazards():
            shot("10-overview-wildfire", f"Live 48-hour wildfire risk for all {n} areas of California on a smooth map, "
                                         "with NASA satellite fire detections.")
            page.click("[data-hazard='thunderstorm']"); settle(800)
            shot("11-overview-thunderstorm", "Thunderstorm potential for the next 48 hours, from atmospheric instability (CAPE).")
            page.click("[data-hazard='blizzard']"); settle(800)
            shot("12-overview-blizzard", "Blizzard conditions for the next 48 hours, from snowfall, wind and cold.")
            page.click("[data-hazard='wildfire']"); settle(800)
        step("hazards", hazards)

        top = js("() => { const s = window.__pw.store.state.risk; const t = (s.top_by_hazard?.wildfire || s.top)[0]; "
                 "return {id: t.id, name: t.name}; }")

        def area():
            js("id => window.__pw.ui.selectCell(id)", top["id"]); page.wait_for_selector("#sheet:not([hidden])"); settle(1500)
            shot("13-area-why-this-score", f"Every score shows its work ({top['name']} here): points per factor, "
                                           "the Fosberg index and the trained model's 7-day fire probability.")
            page.click("#sheet [data-tab='assistant']")
            page.wait_for_function("() => { const b = document.querySelector('#briefing'); return b && !b.classList.contains('loading'); }",
                                   timeout=90000)
            page.wait_for_timeout(800)
            engine = (page.text_content("#engine") or "").strip()
            shot("14-area-briefing", f"A plain-language briefing written by Gemini ({engine or 'Gemini'}), "
                                     "with a Listen button that reads it aloud through ElevenLabs.")
            page.click("#sheet [data-tab='data']"); page.wait_for_timeout(500)
            if page.query_selector("#history"):
                page.wait_for_function("() => !document.querySelector('#history')?.textContent.includes('Loading')", timeout=30000)
                page.wait_for_timeout(800)
                shot("15-area-history-tiger-data", "Two weeks of this area's risk, stored as a time series in Tiger Data (TimescaleDB).")
            else:
                print("  skipped history shot (no Tiger Data on this machine: add DATABASE_URL to .env)")
            js("() => window.__pw.ui.closeCell()")
        step("area", area)

        def agent():
            page.click(".nav button[data-view='ai']"); page.wait_for_selector("#g-q")
            page.fill("#g-q", QUESTION); page.press("#g-q", "Enter")
            page.wait_for_selector("#g-answer .answer-text", timeout=120000); page.wait_for_timeout(800)
            calls = js("() => document.querySelectorAll('#g-answer .trace li').length")
            shot("16-ask-pyrowatch", f"Ask PyroWatch: a Gemini agent answers by calling PyroWatch's own tools "
                                     f"({calls} call{'' if calls == 1 else 's'} here, listed above the answer); "
                                     "Listen reads it aloud with ElevenLabs.")
        step("agent", agent)

        def space():
            page.click(".nav button[data-view='space']"); page.wait_for_timeout(2500)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PWTimeout:
                pass
            shot("17-space-intelligence", "Space Intelligence: the five fire-watch satellites (NOAA-20, NOAA-21, Suomi NPP, "
                                          "Aqua, Terra), tracked live from their orbital elements.")
        step("space", space)

        def replay():
            page.click(".nav button[data-view='overview']"); page.wait_for_timeout(500)
            page.click("#mode-replay"); page.wait_for_timeout(500)
            page.select_option("#preset", args.replay_date); page.wait_for_timeout(500); settle(1500)
            cell = js("name => { const s = window.__pw.store.state.risk; const c = s.cells.find(x => x.name === name) || s.top[0]; "
                      "return {id: c.id, name: c.name, rank: c.rank}; }", args.replay_area)
            js("id => window.__pw.ui.selectCell(id)", cell["id"]); page.wait_for_selector("#sheet:not([hidden])"); settle(1500)
            shot("18-replay-day-before", f"Replay of {args.replay_date}, using only data available that day: "
                                         f"{cell['name']} ranked #{cell['rank']} of {n}.")
            page.click("#reveal")
            page.wait_for_function("() => window.__pw.store.state.validation && document.querySelector('#loading').hidden",
                                   timeout=180000)
            page.wait_for_timeout(2500)
            claim = (page.text_content(".v-claim") or "").strip() if page.query_selector(".v-claim") else ""
            shot("19-what-happened-next", f"Reveal what satellites saw burn next, and score the prediction. {claim}".strip())
        step("replay", replay)

        browser.close()

    lines = ["# PyroWatch screenshots (current interface)", "", "| File | Caption |", "| --- | --- |"]
    lines += [f"| {f} | {c} |" for f, c in captions]
    (out / "CAPTIONS.md").write_text("\n".join(lines) + "\n")
    print(f"\n{len(captions)} screenshots in {args.out}/, captions in {args.out}/CAPTIONS.md")
    if failed:
        print("Some shots failed:\n  " + "\n  ".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
