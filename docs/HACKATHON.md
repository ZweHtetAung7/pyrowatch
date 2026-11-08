# Hackathon notes (team only)

Planning notes from UB Hacking 2026, moved out of the README. The live team plan and pitch are in the team's shared docs.

## Before the demo (do this early!)

1. **Build the fire history** (powers the "fire history" factor and the ML labels):
   `python scripts/build_fire_history.py --start 2015 --end 2025`
   If the download is slow, grab the MODIS → United States yearly CSVs from https://firms.modaps.eosdis.nasa.gov/country/
   in a browser and run `--csv file1.csv file2.csv …`.
2. **Fetch vegetation once** (`python scripts/fetch_vegetation.py`, 5–15 min) and commit
   `data/vegetation.json`.
3. **Click through every Replay preset** with live data and pick the one that tells the best story
   (`python scripts/check_presets.py` does all of them at once).
4. **Record a real prediction.** On one machine only, set `PYROWATCH_RECORD_LEDGER=1` in `.env`; the
   first live map each day is then saved to `data/ledger/YYYY-MM-DD.json`,
   and with `DATABASE_URL` set, *every* refresh is stored in Tiger Data. Commit the ledger file to
   git right away: the commit timestamp proves the prediction came before the fires. Two days later,
   "Validate" uses that recorded prediction instead of a hindcast.
5. Record a backup video of the demo.

## Team split

| Person | Owns | Tracks it carries |
|---|---|---|
| 1 · Data + hardware | `sources.py`, `store.py`, `scripts/build_fire_history.py`, `firmware/` | Best Hardware · Tiger Data |
| 2 · Model | `risk.py`, `scripts/train_model.py` | Best AI/ML |
| 3 · Map/UI | `frontend/` | Best UI/UX · Spirit of the Hackathon (orbit layer) |
| 4 · AI, deploy, pitch | `ai.py`, `agent.py`, `deploy/`, Devpost, demo | Gemini · ElevenLabs · Vultr · GoDaddy Registry |

First hour: 1 gets the FIRMS key, starts the history build, then wires and flashes the beacon. 2 checks
the replay presets on real data. 3 polishes the UI. 4 gets the Gemini and ElevenLabs keys working and
tries the agent, then deploys to Vultr.

## 2-minute demo script

1. **Hook (10 s):** "Fire crews don't need to know where fires *are*. Satellites already tell us
   that. They need to know where the next one will start."
2. **Replay (40 s):** Replay → *LA windstorm, Jan 6 2025*. "This is what PyroWatch saw the day
   before." Click the red Pasadena/Malibu area and read the breakdown: offshore gusts, single-digit
   humidity, months without rain. Show the Gemini briefing. Click **Reveal what happened next**:
   the Palisades and Eaton fire detections appear inside the red zones.
3. **Proof (15 s):** The validation panel shows hit rate, lift over random and AUC. Say the caveats
   out loud; judges respect it.
4. **Live (20 s):** Switch to Live. "This is tonight." Show the satellites orbiting, then type
   "Why is Malibu higher than Santa Barbara, and when does a satellite next pass over it?" and point at the
   agent's tool calls.
5. **Hardware (10 s):** The beacon on the table watches a California town. Its dial points at the
   current risk and it beeps when risk escalates or new fires appear nearby. Its sensor reading is
   on the map as a ground station.
6. **Close (5 s):** "Satellites in orbit, sensors on the ground, and a model that shows its work."

## Prize tracks (UB Hacking)

**Core: the project is built for these**

| Track | Our angle | Remaining work |
|---|---|---|
| Best General Hack | Real impact, working product, validated against real fires | Polish the demo |
| Spirit of the Hackathon (deep space) | The satellites are part of the product: live SGP4 orbits of NOAA-20/21, Suomi NPP, Aqua, Terra; each fire shows which satellite saw it from how high; next-pass countdowns on the map and from the agent | Lead the pitch with it |
| Best AI/ML ("real engineering") | Transparent model + trained classifier, time-split evaluation vs. the baseline, validation with hit rate / lift / AUC, plus a function-calling agent | Run `train_model.py` on real data and put the comparison table in the pitch |
| Best Hardware Hack | PyroWatch Beacon: ESP32 with LEDs, servo dial, buzzer alarm, OLED, DHT22 ground station | Wire it, flash it, make a dial face |
| Best UI/UX ("clear, accessible") | Colorblind-safe scale, keyboard navigation, screen-reader announcements, reduced motion, mobile layout | Polish |
| MLH · Best Use of Gemini API | Gemini agent with 8 tools + per-area briefings | Get a key and test real questions |

**Cheap add-ons (under 1 hour each)**

| Track | How |
|---|---|
| MLH · ElevenLabs | Already wired: add `ELEVENLABS_API_KEY` and the "Listen to briefing" button appears |
| MLH · Tiger Data | Already wired: create a free Tiger Cloud service and set `DATABASE_URL`. Predictions, detections and beacon readings become hypertables; drives the risk-history chart and recorded-prediction validation |
| MLH · Vultr | `deploy/setup_vultr.sh`: judges get a live URL, and the beacon works away from the laptop |
| MLH · GoDaddy Registry domain | Claim through MLH's link and point it at the Vultr server |

**Not targeting:** Best Freshman Hack (the team are seniors), Solana (weak fit), Most Over-Engineered
(opposite of our pitch) and Failure to Launch. **Radial Ventures:** check when the prompt drops.
Ask the organizers whether one project can win several main tracks. MLH prizes are judged separately.
