# PyroWatch 🛰️🔥

**Satellites watch California so wildfires don't catch us by surprise.**

PyroWatch is a live wildfire-ignition risk map for the next 48 hours. It combines NASA satellite fire
detections (VIIRS and MODIS via FIRMS), weather forecasts, fuel dryness, terrain and 5 years of fire
history. It explains each area's score factor by factor, writes a plain-language briefing with Gemini,
answers questions through a Gemini agent that queries the live data itself, and shows the satellites
doing the watching as they orbit overhead. On the ground, an ESP32 **PyroWatch Beacon** turns one
location's risk into lights, a fire-danger dial and an alarm, and reports local sensor readings back.

Then it **proves itself**: replay a past fire (e.g. the January 2025 LA windstorm), see what the map
said *before* ignition, click **Reveal what happened next** to watch the real detections appear, and
get a validation score (hit rate, lift over random, ROC AUC).

---

## Quick start (5 minutes)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # then paste your keys
uvicorn backend.main:app --reload
```

Open http://localhost:8000.

It runs **with no keys at all**. Any source that isn't configured or doesn't respond falls back to
synthetic data, and the UI shows a yellow **DEMO DATA** badge so you never present fake data by
accident. Set `PYROWATCH_DATA_MODE=demo` to force fully offline mode (for rehearsing on bad wifi), or
`live` to make failures loud instead of falling back.

### Keys

| Key | Needed for | Get it |
|---|---|---|
| `FIRMS_MAP_KEY` | live fire detections | https://firms.modaps.eosdis.nasa.gov/api/map_key/ (free, emailed in minutes) |
| `GEMINI_API_KEY` | AI briefings + "Ask PyroWatch" | https://aistudio.google.com/apikey. Set `GEMINI_MODEL` to the current Flash model listed there |
| `ELEVENLABS_API_KEY` | optional: "Listen to briefing" voice | elevenlabs.io |
| `DATABASE_URL` | optional: Tiger Data time series (risk history chart, recorded predictions) | console.cloud.timescale.com: free service, copy its connection string |

Open-Meteo (weather, terrain) and CelesTrak (orbits) need no key. `STATION_TOKEN` (optional) is a
shared secret that beacons must send to post readings.

### Before the demo (do this early!)

1. **Build the fire history** (powers the "fire history" factor and the ML labels):
   `python scripts/build_fire_history.py --start 2015 --end 2025`
   If the download is slow, grab the MODIS → United States yearly CSVs from https://firms.modaps.eosdis.nasa.gov/country/
   in a browser and run `--csv file1.csv file2.csv …`.
2. **Fetch vegetation once** (`python scripts/fetch_vegetation.py`, 5–15 min) and commit
   `data/vegetation.json`.
3. **Click through every Replay preset** with live data and pick the one that tells the best story
   (`python scripts/check_presets.py` does all of them at once).
4. **Record a real prediction.** The first live map each day is saved to `data/ledger/YYYY-MM-DD.json`,
   and with `DATABASE_URL` set, *every* refresh is stored in Tiger Data. Commit the ledger file to
   git right away: the commit timestamp proves the prediction came before the fires. Two days later,
   "Validate" uses that recorded prediction instead of a hindcast.
5. Record a backup video of the demo.

---

## How the model works

Each 0.5° grid cell (~50 km, 186 cells over California) gets a **risk index from 0 to 100**:

```
points_i = 100 × weight_i × factor_i × fuel_gate × veg_gate        risk = Σ points_i
fuel_gate = 0.35 + 0.65 × fuel_dryness           (wet vegetation damps the weather factors)
veg_gate  = 0.2 + 0.8 × vegetation               (spring NDVI: bare desert can't rank high)
```

| Factor | Weight | Measured as (0 → 1) | Source |
|---|---|---|---|
| Fuel dryness | 30% | days since ≥2 mm rain (0→30), 30-day rain (60→0 mm), 7-day VPD (0→4 kPa) | Open-Meteo |
| Wind | 20% | max gusts in the 48 h window, 15 → 70 km/h | Open-Meteo forecast |
| Low humidity | 18% | min relative humidity, 50% → 5% | Open-Meteo forecast |
| Fire history | 14% | satellite fire-days in the cell over the previous 5 years (log scale) | NASA FIRMS archive |
| Heat | 10% | max temperature, 15 → 40 °C | Open-Meteo forecast |
| Terrain | 8% | elevation variability across the cell (0 → 450 m std. dev.) | Open-Meteo elevation |
| Vegetation | multiplier | spring NDVI 0.10 → 0.40 (×0.2 → ×1.0): is there anything to burn? | NASA MODIS MOD13Q1, Apr 2016 (before every replay preset) |

The UI breakdown is exactly these points, so judges can see why an area scored 72. It is a
**relative risk index, not a probability.** The trained model (below) adds an actual probability.

**Fuel dryness is a proxy** built from precipitation and vapour-pressure deficit. **Vegetation**
comes from MODIS NDVI (via the ORNL DAAC web service, no key), fetched once with
`python scripts/fetch_vegetation.py` into `data/vegetation.json`, which is committed so teammates
get it with `git pull`. Without that file the multiplier is simply off (badge "Vegetation: off").

### Validation (`/api/validate`)

For a start date D: take the prediction as of D (a recorded ledger snapshot if one exists, otherwise
a hindcast using only data up to D), then look at FIRMS detections from D to D+2. A **new fire area**
is a cell with detections in that window and none in the 5 days before. It reports what share of
new fire areas were in our top 20% (hit rate), lift over random (random = 1.0×) and ROC AUC.

Be upfront about these caveats. They're printed in the UI too:
- FIRMS also detects agricultural, prescribed and industrial burns.
- 50 km cells test regional risk, not exact ignition points.
- Hindcasts use observed weather in place of the forecast; recorded predictions don't.

To validate every replay preset at once (with the rank of the area where each named fire started),
run `python scripts/check_presets.py`. It prints a paste-ready table and saves it to `data/preset_results.md`.

### ML upgrade (stretch goal)

```bash
python scripts/build_fire_history.py --start 2015 --end 2024
python scripts/train_model.py --years 2020 2021 2022 2023 --step 7
```

This trains logistic regression and gradient boosting on the *same* features. The label is "a new
fire detection in the cell within 7 days". It uses a time split (train on earlier years, test on the
last one) and compares against the weighted index on the same test set. The best model is saved to
`data/model.joblib`, and every area then shows "ML: xx% chance of a new fire within 7 days".
Show the printed comparison table to the AI/ML judges. `--demo` runs the whole pipeline offline on
synthetic data, as a pipeline test only.

---

## Ask PyroWatch (Gemini agent)

Type a town in the search box to jump to it, or type a question ("Why is Malibu higher than Santa
Barbara?", "When will a satellite next see Paradise?", "Where should crews stage tonight?").
Gemini answers with **function calling** over eight tools that run the same code as the map:
`find_area`, `top_areas`, `compare_areas`, `fires_near`, `satellite_passes` (server-side SGP4),
`statewide_summary`, `validation` and `ground_stations`. It may chain several calls, and every call
is shown above the answer, so judges can see that the agent does real work and doesn't make up
numbers. Without a Gemini key, a keyword router calls the same tools.

## Accessibility

- The default color scale gets steadily lighter from low to high risk, so it reads correctly with
  any type of color blindness and in grayscale. "Colors: Classic" switches to green → red.
- Keyboard: `/` focuses search, arrow keys move between neighboring areas, `+`/`-` zoom, `Esc` closes.
- Screen readers: each selection is announced ("Pasadena area. Risk 60 out of 100, Very high…").
  Toggles expose `aria-pressed`, focus moves to the panel heading, and contrast is AA.
- `prefers-reduced-motion` turns off fly-to animations, pulsing and the reveal animation.

## Hardware: PyroWatch Beacon

An ESP32 that watches one California location. It drives green/yellow/red LEDs, a servo
"fire danger" dial and an OLED. It sounds an alarm when risk climbs to Very high or Extreme, or when
satellites detect new fires within 50 km. With a DHT22 it becomes a ground station whose readings
appear on the map, in the area panel, in the agent's tools and in Tiger Data.
Wiring, parts and flashing steps: [firmware/README.md](firmware/README.md).

## Project layout

```
backend/
  main.py      FastAPI routes + serves the frontend
  sources.py   NASA FIRMS, Open-Meteo (forecast / ERA5 archive / elevation), CelesTrak; caching + fallback
  risk.py      features → factor scores → weighted index; validation; prediction ledger
  ai.py        Gemini briefings (template fallback), ElevenLabs voice
  agent.py     "Ask PyroWatch": Gemini function-calling agent over 8 tools (offline router fallback)
  orbits.py    server-side SGP4 satellite passes (used by the agent)
  ground.py    ground stations: readings posted by PyroWatch Beacons
  store.py     Tiger Data / PostgreSQL time series: predictions, detections, ground readings (optional)
  geo.py       California outline, analysis grid, place names
  demo.py      synthetic stand-ins for every source (offline mode)
frontend/      index.html, app.js (Leaflet + satellite.js SGP4 orbits), style.css, vendor/
scripts/       build_fire_history.py, train_model.py
deploy/        Vultr setup script, systemd unit, Caddyfile (HTTPS for your domain)
firmware/      PyroWatch Beacon: ESP32 sketch, config template, wiring guide
data/          ledger/ (commit it!), cache/ (ignored), fire_history.json, model.joblib
```

API: `/api/risk?as_of=`, `/api/fires?as_of=&days=`, `/api/validate?as_of=`, `/api/briefing/{cell}`,
`POST /api/ask` (agent), `/api/tts/{cell}`, `/api/history/{cell}`, `/api/satellites`, `/api/config`,
`/api/beacon?lat=&lon=` (compact status for microcontrollers), `POST /api/stations/{id}`, `/api/stations`.
Interactive docs are at `/docs`.

## Deploy on Vultr (public URL for judges)

1. Create a Vultr Cloud Compute instance (Ubuntu 24.04; the smallest plan is fine) using the MLH credits.
2. Register the domain through GoDaddy Registry and point an **A record** at the instance's IP.
3. On the server:
   ```bash
   git clone https://github.com/ZweHtetAung7/pyrowatch.git /opt/pyrowatch && cd /opt/pyrowatch
   cp .env.example .env && nano .env            # paste keys
   sudo bash deploy/setup_vultr.sh your-domain.example
   ```
   This installs the app as a systemd service behind Caddy, which serves HTTPS automatically.

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

## Credits & data

NASA FIRMS (VIIRS/MODIS active fires) · Open-Meteo (forecast, ERA5 reanalysis, elevation) ·
CelesTrak (orbital elements) · Leaflet · satellite.js · CARTO/OpenStreetMap basemap.
