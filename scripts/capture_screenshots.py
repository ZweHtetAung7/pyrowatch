"""Capture Devpost screenshots of every PyroWatch feature from your running app.

One-time setup (downloads a ~150 MB browser):
  pip install playwright
  python -m playwright install chromium

Then, with the app running on live data in another terminal (keys in .env):
  python scripts/capture_screenshots.py

Writes screenshots/01-...png at 2x resolution plus screenshots/CAPTIONS.md with a caption for each,
ready for the Devpost gallery. It warns if any data badge says "demo": don't submit those.
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_Q = "Where is fire risk highest right now, and when does a satellite next pass over it?"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--out", default="screenshots")
    ap.add_argument("--question", default=DEFAULT_Q, help="question for the Ask PyroWatch shot")
    ap.add_argument("--replay-date", default="2025-01-06")
    ap.add_argument("--replay-area", default="25 km N of Malibu", help="area to open in the replay shot")
    ap.add_argument("--chromium", help="path to a Chromium binary (optional)")
    args = ap.parse_args()

    out = ROOT / args.out
    out.mkdir(exist_ok=True)
    captions, failed = [], []

    def shot(page, name, caption):
        path = out / f"{name}.png"
        page.screenshot(path=str(path))
        captions.append((path.name, caption))
        print(f"  saved {args.out}/{path.name}")

    def ready(page, timeout=90000):
        page.wait_for_selector("#loading[hidden]", state="attached", timeout=timeout)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)  # map tiles
        except PWTimeout:
            pass
        page.wait_for_timeout(1000)

    def briefing_done(page):
        page.wait_for_function(
            "() => { const b = document.querySelector('#briefing'); return b && !b.classList.contains('loading'); }",
            timeout=90000)

    def step(name, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - keep going, report at the end
            failed.append(f"{name}: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}")
            print(f"  FAILED {name}: {str(exc).splitlines()[0][:120]}")

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chromium) if args.chromium else p.chromium.launch()
        page = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        print(f"Opening {args.url} ...")
        page.goto(args.url)
        ready(page)

        badges = page.evaluate("() => [...document.querySelectorAll('#badges .badge')].map(b => b.textContent.trim())")
        demo = [b for b in badges if "demo" in b.lower()]
        if demo:
            print(f"WARNING: synthetic data on screen ({', '.join(demo)}). Fix keys/data before using these for Devpost.")

        top = page.evaluate("() => ({id: state.risk.top[0].id, name: state.risk.top[0].name, n: state.risk.cells.length})")

        def overview():
            shot(page, "01-live-risk-map",
                 f"Live 48-hour wildfire risk for all {top['n']} areas of California, with NASA satellite fire "
                 "detections and the fire-watch satellites' orbits.")
        step("overview", overview)

        def breakdown():
            page.evaluate("id => selectCell(id)", top["id"])
            briefing_done(page)
            page.wait_for_timeout(1200)
            ml = page.evaluate("() => !!state.risk.cells.find(c => c.id === state.selected && c.ml_prob != null)")
            shot(page, "02-why-this-score",
                 f"Click any area ({top['name']} here): its score broken down factor by factor"
                 + (", plus the trained model's 7-day fire probability." if ml else "."))
        step("area breakdown", breakdown)

        def briefing():
            page.evaluate("() => document.querySelector('#briefing').previousElementSibling.scrollIntoView({block: 'start'})")
            page.wait_for_timeout(600)
            shot(page, "03-briefing-and-satellite-passes",
                 "A plain-language briefing written by Gemini, and when each fire-watch satellite next passes overhead "
                 "(computed from live orbital data).")
        step("briefing", briefing)

        def agent():
            page.keyboard.press("Escape")
            page.fill("#omni-q", args.question)
            page.press("#omni-q", "Enter")
            page.wait_for_selector("#g-answer .answer-text", timeout=120000)
            page.wait_for_timeout(800)
            calls = page.evaluate("() => document.querySelectorAll('#g-answer .trace li').length")
            shot(page, "04-ask-pyrowatch",
                 f"Ask PyroWatch: a Gemini agent answers by calling PyroWatch's own tools "
                 f"({calls} call{'' if calls == 1 else 's'} here), listed above the answer.")
            page.fill("#omni-q", "")
        step("agent", agent)

        def orbits():
            page.evaluate("""() => {
                closePanel();
                const near = state.sats.filter(s => s.pos).sort((a, b) =>
                  Math.abs(a.pos.lat - 37) + Math.abs(a.pos.lon + 119) - Math.abs(b.pos.lat - 37) - Math.abs(b.pos.lon + 119))[0];
                if (near) map.setView([near.pos.lat, near.pos.lon], 3); }""")
            page.wait_for_timeout(1500)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PWTimeout:
                pass
            shot(page, "05-fire-watch-satellites",
                 "The five satellites that detect fires (NOAA-20, NOAA-21, Suomi NPP, Aqua, Terra), tracked live "
                 "with their ground tracks and sensor footprints.")
        step("satellites", orbits)

        def replay():
            page.evaluate("() => map.setView([37.25, -119.3], 6)")
            page.click("#mode-replay")
            ready(page)
            has_preset = page.evaluate("d => [...document.querySelectorAll('#preset option')].some(o => o.value === d)", args.replay_date)
            if has_preset:
                page.select_option("#preset", args.replay_date)
            else:
                page.fill("#replay-date", args.replay_date)
                page.dispatch_event("#replay-date", "change")
            ready(page)
            cell = page.evaluate("name => { const c = state.risk.cells.find(x => x.name === name) || state.risk.top[0]; "
                                 "return {id: c.id, name: c.name, rank: c.rank}; }", args.replay_area)
            page.evaluate("id => selectCell(id)", cell["id"])
            briefing_done(page)
            page.wait_for_timeout(1200)
            shot(page, "06-replay-day-before",
                 f"Replay: the map as it stood on {args.replay_date}, using only data available that day. "
                 f"{cell['name']} ranked #{cell['rank']} of {top['n']}.")

            page.click("#reveal")
            page.wait_for_selector(".v-claim", timeout=120000)
            ready(page)
            page.wait_for_timeout(1500)
            claim = page.evaluate("() => document.querySelector('.v-claim').textContent.trim()")
            shot(page, "07-what-happened-next", f"Reveal: the fires satellites saw over the next 3 days. {claim}")
        step("replay", replay)

        def ground():
            page.click("#mode-live")
            ready(page)
            target = page.evaluate("""() => {
                const s = state.stations.find(x => x.cell_id);
                if (s) return {id: s.cell_id, what: 'station'};
                if (state.config.database && state.config.database.ready) return {id: state.risk.top[0].id, what: 'history'};
                return null; }""")
            if not target:
                print("  skipped ground station / history shot (no beacon reporting and no Tiger Data)")
                return
            page.evaluate("id => selectCell(id)", target["id"])
            briefing_done(page)
            sel = ".ground" if target["what"] == "station" else "#history"
            page.evaluate("s => document.querySelector(s)?.scrollIntoView({block: 'center'})", sel)
            page.wait_for_timeout(1500)
            if target["what"] == "station":
                shot(page, "08-ground-station",
                     "Ground truth: the ESP32 beacon's temperature and humidity reading, shown next to the forecast.")
            else:
                shot(page, "08-risk-history", "Risk over the past week for one area, stored as time series in Tiger Data.")
        step("ground station", ground)

        def phone():
            mobile = browser.new_context(**p.devices["iPhone 13"]).new_page()
            mobile.goto(args.url)
            ready(mobile)
            mobile.evaluate("id => selectCell(id)", top["id"])
            mobile.wait_for_timeout(2000)
            shot(mobile, "09-phone", "Works on a phone: tap an area and its details slide up from the bottom.")
        step("phone", phone)

        browser.close()

    lines = ["# PyroWatch screenshots", "", "| File | Caption |", "| --- | --- |"]
    lines += [f"| {f} | {c} |" for f, c in captions]
    (out / "CAPTIONS.md").write_text("\n".join(lines) + "\n")
    print(f"\n{len(captions)} screenshots in {args.out}/, captions in {args.out}/CAPTIONS.md")
    if errors:
        print("Page errors:", errors[:3])
    if failed:
        print("Some shots failed:\n  " + "\n  ".join(failed))
    if demo:
        print("REMINDER: these used synthetic data; retake on live data before submitting.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
