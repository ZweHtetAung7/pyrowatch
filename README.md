# PyroWatch 🛰️🔥

**Satellites watch California so wildfires don't catch us by surprise.**

PyroWatch is a live wildfire-ignition risk map for the next 48 hours. It combines NASA satellite fire
detections (VIIRS and MODIS via FIRMS), weather forecasts, fuel dryness, terrain and 5 years of fire
history. It explains each area's score factor by factor, writes a plain-language briefing with Gemini,
and shows the satellites doing the watching as they orbit overhead.

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
| `MONGODB_URI` | optional: prediction ledger in Atlas | mongodb.com/atlas |

Open-Meteo (weather, terrain) and CelesTrak (orbits) need no key.

### Before the demo (do this early!)

1. **Build the fire history** (powers the "fire history" factor and the ML labels):
   `python scripts/build_fire_history.py --start 2015 --end 2025`
   If the download is slow, grab the MODIS → United States yearly CSVs from https://firms.modaps.eosdis.nasa.gov/country/
   in a browser and run `--csv file1.csv file2.csv …`.
2. **Click through every Replay preset** with live data and pick the one that tells the best story.
3. **Record a real prediction.** The first live map each day is saved to `data/ledger/YYYY-MM-DD.json`.
   Commit it to git right away: the commit timestamp proves the prediction came before the fires.
   Two days later, "Validate" uses that recorded prediction instead of a hindcast.
4. Record a backup video of the demo.

---

## How the model works

Each 0.5° grid cell (~50 km, 186 cells over California) gets a **risk index from 0 to 100**:

```
points_i = 100 × weight_i × factor_i × fuel_gate        risk = Σ points_i
fuel_gate = 0.35 + 0.65 × fuel_dryness                   (wet vegetation damps the weather factors)
```

| Factor | Weight | Measured as (0 → 1) | Source |
|---|---|---|---|
| Fuel dryness | 30% | days since ≥2 mm rain (0→30), 30-day rain (60→0 mm), 7-day VPD (0→4 kPa) | Open-Meteo |
| Wind | 20% | max gusts in the 48 h window, 15 → 70 km/h | Open-Meteo forecast |
| Low humidity | 18% | min relative humidity, 50% → 5% | Open-Meteo forecast |
| Fire history | 14% | satellite fire-days in the cell over the previous 5 years (log scale) | NASA FIRMS archive |
| Heat | 10% | max temperature, 15 → 40 °C | Open-Meteo forecast |
| Terrain | 8% | elevation variability across the cell (0 → 450 m std. dev.) | Open-Meteo elevation |

The UI breakdown is exactly these points, so judges can see why an area scored 72. It is a
**relative risk index, not a probability.** The trained model (below) adds an actual probability.

**Fuel dryness is a proxy** built from precipitation and vapour-pressure deficit, not NDVI from raw
imagery, which would take the whole hackathon to process. Upgrade path:
the ORNL DAAC MODIS web service returns MOD13Q1 NDVI per point with no key needed.

### Validation (`/api/validate`)

For a start date D: take the prediction as of D (a recorded ledger snapshot if one exists, otherwise
a hindcast using only data up to D), then look at FIRMS detections from D to D+2. A **new fire area**
is a cell with detections in that window and none in the 5 days before. It reports what share of
new fire areas were in our top 20% (hit rate), lift over random (random = 1.0×) and ROC AUC.

Be upfront about these caveats. They're printed in the UI too:
- FIRMS also detects agricultural, prescribed and industrial burns.
- 50 km cells test regional risk, not exact ignition points.
- Hindcasts use observed weather in place of the forecast; recorded predictions don't.

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

## Project layout

```
backend/
  main.py      FastAPI routes + serves the frontend
  sources.py   NASA FIRMS, Open-Meteo (forecast / ERA5 archive / elevation), CelesTrak; caching + fallback
  risk.py      features → factor scores → weighted index; validation; prediction ledger
  ai.py        Gemini briefings & Q&A (template fallback), ElevenLabs voice
  geo.py       California outline, analysis grid, place names
  demo.py      synthetic stand-ins for every source (offline mode)
frontend/      index.html, app.js (Leaflet + satellite.js SGP4 orbits), style.css, vendor/
scripts/       build_fire_history.py, train_model.py
data/          ledger/ (commit it!), cache/ (ignored), fire_history.json, model.joblib
```

API: `/api/risk?as_of=`, `/api/fires?as_of=&days=`, `/api/validate?as_of=`, `/api/briefing/{cell}`,
`POST /api/ask`, `/api/tts/{cell}`, `/api/satellites`, `/api/config`. Interactive docs are at `/docs`.

## Team split

| Person | Owns | First task |
|---|---|---|
| 1 · Data | `sources.py`, `scripts/build_fire_history.py` | Get the FIRMS key and confirm live fires on the map. Build the fire history. |
| 2 · Model | `risk.py`, `scripts/train_model.py` | Check replay presets on real data and tune weights/thresholds. Then train the ML model. |
| 3 · Map/UI | `frontend/` | Polish, mobile, the reveal animation, county names. |
| 4 · AI + pitch | `ai.py`, Devpost, demo | Gemini prompt, voice, .tech domain, 90-second script, backup video. |

## 90-second demo script

1. **Hook (10 s):** "Fire crews don't need to know where fires *are*. Satellites already tell us
   that. They need to know where the next one will start."
2. **Replay (40 s):** Replay → *LA windstorm, Jan 6 2025*. "This is what PyroWatch saw the day
   before." Click the red Pasadena/Malibu area and read the breakdown: offshore gusts, single-digit
   humidity, months without rain. Show the Gemini briefing. Click **Reveal what happened next**:
   the Palisades and Eaton fire detections appear inside the red zones.
3. **Proof (15 s):** The validation panel shows hit rate, lift over random and AUC. Say the caveats
   out loud; judges respect it.
4. **Live (20 s):** Switch to Live. "This is tonight." Show the satellites orbiting and "NOAA-21 passes
   over this area in 14 min". Ask Gemini a question live.
5. **Close (5 s):** "Satellites protecting Earth. Every number is real, and the model shows its work."

## Prize-track coverage

Check this against the official UB Hacking track list and drop anything that isn't offered.

| Track | Why PyroWatch fits |
|---|---|
| Best General | Real-world impact, working product, validated against real fires |
| Best AI/ML | Transparent model + trained classifier with time-split evaluation vs. baseline |
| Best UI/UX | Mission-control map, factor breakdown, reveal animation, mobile layout |
| Theme: "Into the Hackosmos" | Live SGP4 orbits of the actual fire-watch satellites, per-detection "seen by NOAA-20 from 830 km", next-pass countdown |
| MLH · Best use of Gemini API | Per-area briefings + grounded Q&A |
| MLH · ElevenLabs (if offered) | Spoken briefings ("Listen") |
| MLH · MongoDB Atlas (if offered) | Prediction ledger: timestamped forecasts for honest validation |
| MLH · .tech domain (if offered) | e.g. `pyrowatch.tech` |

## Credits & data

NASA FIRMS (VIIRS/MODIS active fires) · Open-Meteo (forecast, ERA5 reanalysis, elevation) ·
CelesTrak (orbital elements) · Leaflet · satellite.js · CARTO/OpenStreetMap basemap.
