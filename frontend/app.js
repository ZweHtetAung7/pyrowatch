'use strict';
/* PyroWatch frontend: Leaflet map + risk grid + FIRMS fires; satellite orbits live on the provenance view. */
import { HAZARDS, hazardActions, hazardFor } from './hazards.js';

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ------------------------------------------------------------------ colors
// Default palette rises steadily in lightness (dark indigo -> gold), so the order reads correctly
// with any type of color blindness and in grayscale. "Classic" is the familiar green -> red.
const PALETTES = {
  safe: { label: 'Colorblind-safe', stops: [[0, [45, 27, 78]], [20, [94, 44, 140]], [40, [168, 50, 121]], [60, [224, 83, 59]], [75, [249, 142, 9]], [100, [252, 222, 95]]] },
  classic: { label: 'Classic', stops: [[0, [26, 152, 80]], [20, [166, 217, 106]], [30, [254, 224, 139]], [45, [253, 174, 97]], [60, [244, 109, 67]], [75, [215, 48, 39]], [100, [142, 1, 82]]] },
};
let palette = 'safe';
try { palette = PALETTES[localStorage.getItem('pw-palette')] ? localStorage.getItem('pw-palette') : 'safe'; } catch (_) { /* storage blocked */ }
function rgbAt(v) {
  const stops = PALETTES[palette].stops;
  v = Math.max(0, Math.min(100, v));
  for (let i = 1; i < stops.length; i++) {
    const [b, cb] = stops[i];
    if (v <= b) {
      const [a, ca] = stops[i - 1];
      const t = (v - a) / (b - a);
      return ca.map((x, k) => Math.round(x + t * (cb[k] - x)));
    }
  }
  return stops[stops.length - 1][1];
}
const riskColor = (v) => `rgb(${rgbAt(v).join(',')})`;
const CAT_MID = { Low: 10, Moderate: 30, High: 50, 'Very high': 67, Extreme: 88 };
const catColor = (cat) => riskColor(CAT_MID[cat] ?? 50);
function catTextColor(cat) {
  const [r, g, b] = rgbAt(CAT_MID[cat] ?? 50).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.18 ? '#0b0f14' : '#ffffff';
}
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ------------------------------------------------------------------ state
const state = {
  config: null, asOf: null, risk: null, fires: null, selected: null, validation: null,
  sats: [], satSource: null, firesRevealed: true, stations: [],
  hazard: 'wildfire', view: 'overview',
  userLoc: null, // {lat, lon} only after geolocation permission
  show: { risk: true, fires: true, sats: false, stations: true },
};
try {
  const savedHazard = localStorage.getItem('pw-hazard');
  if (HAZARDS[savedHazard]) state.hazard = savedHazard;
} catch (_) { /* storage blocked */ }

// ------------------------------------------------------------------ map
// Leaflet's own arrow-key panning is off: arrow keys move between areas instead (see initKeyboard).
const map = L.map('map', { zoomControl: false, minZoom: 3, worldCopyJump: true, keyboard: false }).setView([37.25, -119.3], 6);
L.control.zoom({ position: 'bottomright' }).addTo(map);
for (const [name, z] of [['labels', 610], ['validation', 615], ['fires', 620], ['sats', 630]]) {
  map.createPane(name).style.zIndex = z;
}
map.getPane('labels').style.pointerEvents = 'none';
const ESRI_BASE = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}';
const ESRI_REF = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}';
L.tileLayer(ESRI_BASE, {
  maxZoom: 16,
  attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ · Fire data: NASA FIRMS · Weather: Open-Meteo · Orbits: CelesTrak',
}).addTo(map);
L.tileLayer(ESRI_REF, { maxZoom: 16, pane: 'labels' }).addTo(map);

const layers = {
  risk: L.layerGroup().addTo(map),
  fires: L.layerGroup().addTo(map),
  sats: L.layerGroup(), // shown only from the Data Sources view
  validation: L.layerGroup().addTo(map),
  stations: L.layerGroup().addTo(map),
};
const go = (latlng, zoom) => (reduceMotion ? map.setView(latlng, zoom) : map.flyTo(latlng, zoom, { duration: 0.8 }));
const polys = {};

// ------------------------------------------------------------------ helpers
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}
function toast(msg, ms = 6000) {
  const t = $('#toast');
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, ms);
}
function loading(text) { $('#loading-text').textContent = text; $('#loading').hidden = false; }
function done() { $('#loading').hidden = true; }
const fmtDate = (iso, opts = { weekday: 'short', month: 'short', day: 'numeric' }) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, opts);
const fmtLong = (iso) => fmtDate(iso, { month: 'short', day: 'numeric', year: 'numeric' });
const fmtTime = (d) => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const fmtUtc = (iso) => new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
const cellById = (id) => state.risk?.cells.find((c) => c.id === id);
const activeHazard = (cell) => hazardFor(cell, state.hazard);
const activeScore = (cell) => activeHazard(cell).score;
function haversineKm(a, b, c, d) {
  const R = 6371, r = Math.PI / 180;
  const x = Math.sin(((c - a) * r) / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin(((d - b) * r) / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
const qs = (extra = {}) => {
  const p = new URLSearchParams();
  if (state.asOf) p.set('as_of', state.asOf);
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  const s = p.toString();
  return s ? `?${s}` : '';
};

// ------------------------------------------------------------------ data loading
async function loadData() {
  loading(state.asOf ? `Rebuilding the risk map as it looked on ${fmtLong(state.asOf)}…` : 'Loading weather forecast and fire detections…');
  try {
    const [risk, fires] = await Promise.all([
      api(`/api/risk${qs()}`),
      api(`/api/fires${qs({ days: state.asOf ? 3 : 2 })}`),
    ]);
    state.risk = risk; state.fires = fires; state.validation = null;
    state.firesRevealed = !state.asOf; // in replay, fires stay hidden until "Reveal what happened"
    layers.validation.clearLayers();
    renderRisk(); renderFires(); renderHeader(); renderStats(); buildAreaList();
    if (state.selected && cellById(state.selected)) selectCell(state.selected, { fly: false });
    else closePanel();
  } catch (e) {
    toast(`Could not load data: ${e.message}`);
    if (!state.risk) {
      renderHeader();
      renderStats(`Could not load data. ${e.message}`);
    }
  } finally {
    done();
  }
}

// ------------------------------------------------------------------ rendering: risk grid
// "smooth": a continuous color surface clipped to California (areas stay clickable on top);
// "grid": the 50 km squares themselves.
let riskView = 'smooth';
try { riskView = localStorage.getItem('pw-view') === 'grid' ? 'grid' : 'smooth'; } catch (_) { /* storage blocked */ }
let surfaceLayer = null;
const restStyle = (col) => (riskView === 'smooth'
  ? { color: '#ffffff', weight: 0, opacity: 0, fillColor: col, fillOpacity: 0 }
  : { color: col, weight: 0.6, opacity: 0.55, fillColor: col, fillOpacity: 0.5 });
const hoverStyle = () => (riskView === 'smooth' ? { weight: 1, opacity: 0.6 } : { fillOpacity: 0.7 });

function renderRisk() {
  layers.risk.clearLayers();
  for (const k of Object.keys(polys)) delete polys[k];
  for (const c of state.risk.cells) {
    const score = activeScore(c);
    const col = score == null ? '#424b55' : riskColor(score);
    const p = L.polygon(c.shape, restStyle(col));
    const category = activeHazard(c).category;
    p.bindTooltip(`<b>${esc(c.name)}</b><br>${esc(HAZARDS[state.hazard].label)} ${score == null ? 'unavailable' : `${score.toFixed(0)} · ${esc(category || '')}`}`, { sticky: true, className: 'tip', direction: 'top', offset: [0, -8] });
    p.on('click', () => selectCell(c.id));
    p.on('mouseover', () => { if (state.selected !== c.id) p.setStyle(hoverStyle()); });
    p.on('mouseout', () => { if (state.selected !== c.id) p.setStyle(restStyle(p.options.fillColor)); });
    p.addTo(layers.risk);
    polys[c.id] = p;
  }
  renderSurface();
  highlight();
}
function highlight() {
  for (const [id, p] of Object.entries(polys)) {
    const sel = id === state.selected;
    p.setStyle(sel ? { color: '#ffffff', weight: 2.5, opacity: 1, fillOpacity: riskView === 'smooth' ? 0.08 : 0.72 } : restStyle(p.options.fillColor));
    if (sel) p.bringToFront();
  }
}

// Smooth surface: bilinear interpolation between area centers, drawn on a canvas whose rows are
// spaced in Web Mercator (so it lines up with the map), then clipped to the areas' outlines.
const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
const invMercY = (y) => ((2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180) / Math.PI;
function renderSurface() {
  if (surfaceLayer) { layers.risk.removeLayer(surfaceLayer); surfaceLayer = null; }
  if (riskView !== 'smooth' || !state.risk || !state.config?.grid) return;
  const g = state.config.grid;
  const vals = new Map();
  let maxR = 0, maxC = 0;
  for (const c of state.risk.cells) {
    const m = /^r(\d+)c(\d+)$/.exec(c.id);
    if (!m) continue;
    const score = activeScore(c);
    if (score != null) vals.set(+m[1] * 1000 + +m[2], score);
    maxR = Math.max(maxR, +m[1]); maxC = Math.max(maxC, +m[2]);
  }
  const west = g.west, south = g.south, east = west + (maxC + 1) * g.step, north = south + (maxR + 1) * g.step;
  const W = 640, H = 640, yN = mercY(north), yS = mercY(south);
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const ctx = cv.getContext('2d');
  const img = ctx.createImageData(W, H), d = img.data;
  for (let j = 0; j < H; j++) {
    const fr = (invMercY(yN - ((j + 0.5) / H) * (yN - yS)) - south) / g.step - 0.5;
    const r0 = Math.floor(fr), tr = fr - r0;
    for (let i = 0; i < W; i++) {
      const fc = (((i + 0.5) / W) * (east - west)) / g.step - 0.5;
      const c0 = Math.floor(fc), tc = fc - c0;
      let s = 0, w = 0;
      for (const [dr, dc, wt] of [[0, 0, (1 - tr) * (1 - tc)], [1, 0, tr * (1 - tc)], [0, 1, (1 - tr) * tc], [1, 1, tr * tc]]) {
        const v = vals.get((r0 + dr) * 1000 + (c0 + dc));
        if (v !== undefined && wt > 0) { s += v * wt; w += wt; }
      }
      if (!w) continue;
      const v = s / w, [R, G, B] = rgbAt(v), k = (j * W + i) * 4;
      d[k] = R; d[k + 1] = G; d[k + 2] = B; d[k + 3] = Math.round(255 * (0.55 + (0.4 * v) / 100));
    }
  }
  ctx.putImageData(img, 0, 0);
  ctx.globalCompositeOperation = 'destination-in';
  ctx.beginPath();
  const px = (lat, lon) => [((lon - west) / (east - west)) * W, ((yN - mercY(lat)) / (yN - yS)) * H];
  for (const c of state.risk.cells) {
    c.shape.forEach(([la, lo], k) => { const [x, y] = px(la, lo); if (k) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
    ctx.closePath();
  }
  ctx.fill();
  surfaceLayer = L.imageOverlay(cv.toDataURL(), [[south, west], [north, east]], { interactive: false, className: 'risk-surface' }).addTo(layers.risk);
  surfaceLayer.bringToBack();
}
function applyView() {
  const b = $('#view-btn');
  if (!b) return;
  b.textContent = riskView === 'smooth' ? 'View: smooth ⇄' : 'View: grid ⇄';
  b.setAttribute('aria-label', `Map view: ${riskView}. Switch.`);
}

// ------------------------------------------------------------------ rendering: fires
function firePopup(d) {
  const sat = state.sats.find((s) => s.name === d.satellite);
  const alt = sat?.alt ? `${Math.round(sat.alt)} km` : d.instrument === 'MODIS' ? '~705 km' : '~830 km';
  return `<b>Active fire detection</b><br>
    Seen by <b>${esc(d.satellite)}</b> (${esc(d.instrument)}) from ${alt} up<br>
    ${esc(fmtUtc(d.time_utc))} · ${d.daynight === 'N' ? 'night pass' : 'day pass'}<br>
    Fire radiative power: <b>${d.frp.toFixed(1)} MW</b> · confidence ${esc(d.confidence)}<br>
    <span class="muted small">${d.lat.toFixed(3)}, ${d.lon.toFixed(3)} · ${esc(d.source)}</span>`;
}
function fireMarker(d, latest) {
  const ageH = (latest - Date.parse(d.time_utc)) / 3.6e6;
  const recent = ageH < 12;
  return L.circleMarker([d.lat, d.lon], {
    pane: 'fires', radius: 2.5 + Math.min(6, Math.log1p(d.frp)), weight: 1.3, color: '#ffffff', opacity: 0.95,
    fillColor: recent ? '#ff1f1f' : '#ff6a1f', fillOpacity: 0.95, className: recent ? 'fire-dot recent' : 'fire-dot',
  }).bindPopup(firePopup(d));
}
function renderFires(animate = false) {
  layers.fires.clearLayers();
  if (!state.fires || !state.firesRevealed) return;
  const dets = [...state.fires.detections].sort((a, b) => a.time_utc.localeCompare(b.time_utc));
  const latest = dets.length ? Date.parse(dets[dets.length - 1].time_utc) : Date.now();
  if (!animate) { dets.forEach((d) => fireMarker(d, latest).addTo(layers.fires)); return; }
  const step = reduceMotion ? 0 : Math.min(40, 2200 / Math.max(1, dets.length));
  dets.forEach((d, i) => setTimeout(() => fireMarker(d, latest).addTo(layers.fires), i * step));
}

// ------------------------------------------------------------------ rendering: header + stats
function renderHeader() {
  const r = state.risk;
  if (!r) { $('#window-line').textContent = ''; $('#badges').innerHTML = ''; return; }
  const [a, b] = [r.as_of, new Date(Date.parse(`${r.as_of}T12:00:00`) + 864e5).toISOString().slice(0, 10)];
  $('#window-line').innerHTML = state.asOf
    ? `Replay · risk as it stood on <b>${esc(fmtLong(a))}</b>, using only data available that day`
    : `Forecast window <b>${esc(fmtDate(a))} → ${esc(fmtDate(b))}</b> · updated ${esc(fmtTime(new Date(r.generated_at)))}`;
  const src = [
    ['Weather', r.sources.weather], ['Fires', state.fires?.source], ['Terrain', r.sources.terrain],
    ['History', r.sources.history], ['Vegetation', r.sources.vegetation], ['Orbits', state.satSource],
  ].filter(([, s]) => s);
  const anyDemo = src.some(([, s]) => s.mode === 'demo');
  $('#badges').innerHTML = (anyDemo ? '<span class="badge demo-all" title="Some layers use synthetic data — open Data Sources for details">DEMO DATA</span>' : '')
    + src.map(([k, s]) => `<span class="badge ${s.mode !== 'live' ? 'demo' : ''}" title="${esc(s.source)}${s.note ? ` — ${esc(s.note)}` : ''}"><i></i>${k}: ${s.mode === 'live' ? 'live' : s.mode === 'off' ? 'off' : 'demo'}</span>`).join('');
  if (state.view === 'satellites') renderSatellitesBody();
  if (state.view === 'stations') renderStationsBody();
}
function fmtWx(v, digits = 0, unit = '') {
  if (v == null || Number.isNaN(v)) return '—';
  return `${Number(v).toFixed(digits)}${unit}`;
}
function sourceFreshnessLabel(status) {
  if (!status) return 'UNAVAILABLE';
  if (status.mode === 'demo') return 'DEMO';
  if (status.mode === 'off') return 'UNAVAILABLE';
  if (status.mode === 'live' && /cach/i.test(status.note || '')) return 'CACHED';
  if (status.mode === 'live') return 'LIVE';
  return String(status.mode || 'UNAVAILABLE').toUpperCase();
}
function freshnessBlock() {
  const r = state.risk;
  const rows = [
    ['Weather', r?.sources?.weather],
    ['Fires', state.fires?.source],
    ['Terrain', r?.sources?.terrain],
    ['History', r?.sources?.history],
    ['Vegetation', r?.sources?.vegetation],
    ['Orbits', state.satSource],
  ];
  return `<div class="panel-section">
    <h3>Data freshness</h3>
    <div class="freshness">${rows.map(([k, s]) => {
      const label = sourceFreshnessLabel(s);
      const tip = s ? `${s.source || k}${s.note ? ` — ${s.note}` : ''}` : 'No status';
      return `<span class="fresh ${label}" title="${esc(tip)}">${esc(k)} · ${label}</span>`;
    }).join('')}</div>
    <p class="note">DEMO/UNAVAILABLE layers are not observed live conditions. A low risk index is not a safety clearance.</p>
  </div>`;
}
function nearbyFires(c, radiusKm = 75) {
  if (!state.fires?.detections || !state.firesRevealed) return null;
  return state.fires.detections
    .map((d) => ({ ...d, dist_km: haversineKm(c.lat, c.lon, d.lat, d.lon) }))
    .filter((d) => d.dist_km <= radiusKm)
    .sort((a, b) => a.dist_km - b.dist_km)
    .slice(0, 6);
}
function nearbyStations(c, radiusKm = 80) {
  return state.stations
    .map((s) => ({ ...s, dist_km: haversineKm(c.lat, c.lon, s.lat, s.lon) }))
    .filter((s) => s.dist_km <= radiusKm && Date.now() - Date.parse(s.latest.time_utc) < 6 * 3.6e6)
    .sort((a, b) => a.dist_km - b.dist_km)
    .slice(0, 5);
}
function weatherBlock(c) {
  const r = c.raw || {};
  let cells;
  if (state.hazard === 'thunderstorm') {
    cells = [
      ['Peak CAPE', fmtWx(r.cape_max_jkg, 0), 'J/kg'],
      ['Temp max', fmtWx(r.tmax_c, 1), '°C'],
      ['Gust', fmtWx(r.gust_kmh, 0), 'km/h'],
      ['RH min', fmtWx(r.rh_min_pct, 0), '%'],
    ];
  } else if (state.hazard === 'blizzard') {
    cells = [
      ['Snowfall 48h', fmtWx(r.snowfall_48h_cm, 1), 'cm'],
      ['Temp max', fmtWx(r.tmax_c, 1), '°C'],
      ['Gust', fmtWx(r.gust_kmh, 0), 'km/h'],
      ['Wind', fmtWx(r.wind_kmh, 0), 'km/h'],
    ];
  } else {
    cells = [
      ['Temp max', fmtWx(r.tmax_c, 1), '°C'],
      ['RH min', fmtWx(r.rh_min_pct, 0), '%'],
      ['Gust', fmtWx(r.gust_kmh, 0), 'km/h'],
      ['FFWI', fmtWx(r.ffwi, 0), '/100'],
      ['Precip 30d', fmtWx(r.precip_30d_mm, 1), 'mm'],
      ['Days since rain', r.days_since_rain == null ? '—' : String(r.days_since_rain), ''],
      ['VPD', fmtWx(r.vpd_kpa, 2), 'kPa'],
      ['Wind', fmtWx(r.wind_kmh, 0), 'km/h'],
    ];
    if (c.ml_prob != null) cells.push(['ML fire (7d)', `${(c.ml_prob * 100).toFixed(0)}`, '%']);
  }
  const veg = c.vegetation
    ? `<div class="muted small" style="margin-top:6px"><b>Vegetation:</b> ${esc(c.vegetation)}${c.veg_gate != null ? ` · gate ×${c.veg_gate.toFixed(2)}` : ''}${r.ndvi != null ? ` · NDVI ${Number(r.ndvi).toFixed(2)}` : ''}</div>`
    : '<div class="muted small" style="margin-top:6px">Vegetation: unavailable</div>';
  return `<div class="panel-section">
    <h3>Weather &amp; vegetation</h3>
    <div class="wx-grid">${cells.map(([k, v, u]) => `
      <div class="wx-cell"><div class="k">${esc(k)}</div><div class="v">${esc(v)}${u ? `<span class="u"> ${esc(u)}</span>` : ''}</div></div>`).join('')}
    </div>
    ${veg}
    <p class="note">Forecast measurements from Open-Meteo. Values are inputs to the selected hazard index, not official warnings.</p>
  </div>`;
}
function firesBlock(c) {
  if (state.hazard !== 'wildfire') {
    return `<div class="panel-section"><h3>Active detections</h3>
      <p class="muted small">Fire detection overlays are wildfire-specific. Switch hazard to Wildfire to inspect FIRMS hotspots near this area.</p></div>`;
  }
  const near = nearbyFires(c);
  if (near == null) {
    return `<div class="panel-section"><h3>Nearby fire detections</h3>
      <p class="muted small">${state.asOf && !state.firesRevealed ? 'Detections are hidden until you reveal the replay outcome.' : 'Fire detection layer unavailable.'}</p></div>`;
  }
  if (!near.length) {
    return `<div class="panel-section"><h3>Nearby fire detections</h3>
      <p class="muted small">No FIRMS detections within ~75 km of this cell center in the current window. Absence of detections is not proof of safety.</p></div>`;
  }
  return `<div class="panel-section"><h3>Nearby fire detections</h3>
    <ul class="near-list">${near.map((d) => `
      <li>
        <span><b>${esc(d.satellite)}</b> · ${esc(d.instrument)}
          <div class="meta">${esc(fmtUtc(d.time_utc))} · FRP ${d.frp.toFixed(1)} MW · ${esc(d.confidence)}</div>
        </span>
        <span class="dist">${d.dist_km < 1 ? `${Math.round(d.dist_km * 1000)} m` : `${d.dist_km.toFixed(1)} km`}</span>
      </li>`).join('')}</ul>
    <p class="note">Distances are from the cell center to the detection point. Hotspots are satellite observations, not confirmed ignitions.</p>
  </div>`;
}
function stationsNearBlock(c) {
  const near = nearbyStations(c);
  if (!near.length) {
    return `<div class="panel-section"><h3>Nearby ground stations</h3>
      <p class="muted small">No recent Beacon readings within ~80 km.</p></div>`;
  }
  return `<div class="panel-section"><h3>Nearby ground stations</h3>
    <ul class="near-list">${near.map((s) => `
      <li>
        <span><b>${esc(s.label)}</b>
          <div class="meta">${esc(fmtReading(s.latest))} · ${esc(ago(s.latest.time_utc))} · ${esc(s.place || '')}</div>
        </span>
        <span class="dist">${s.dist_km.toFixed(1)} km</span>
      </li>`).join('')}</ul>
  </div>`;
}
function assistantBlock(c) {
  const prompts = [
    'Is this area safe right now?',
    'A fire is across the road from me. What should I do?',
    'Should I evacuate?',
    'Which direction should I move?',
    'What should I do if the fire gets closer?',
  ];
  return `<details class="ai-assistant" id="ai-assistant">
    <summary>Ask AI about this area</summary>
    <div class="ai-body">
      <div class="ai-safety">Uses the selected cell via <b>/api/ask</b> tools (risk, fires nearby, stations). Does not invent distances or alerts. A low score is not “safe.” For immediate danger call <b>911</b>.</div>
      <div class="muted small">Context loaded: ${esc(c.name)} · ${esc(HAZARDS[state.hazard].label)} · ${c.lat.toFixed(2)}°, ${c.lon.toFixed(2)}°${state.userLoc ? ` · your location shared` : ''}</div>
      <div id="briefing" class="briefing loading">Preparing area briefing…</div>
      <div class="row">${state.config?.has_tts ? '<button id="listen" class="btn-ghost">▶ Listen to briefing</button>' : ''}<span class="engine" id="engine">…</span></div>
      <div class="ai-chips">${prompts.map((p) => `<button type="button" class="ai-chip" data-q="${esc(p)}">${esc(p)}</button>`).join('')}</div>
      <form id="ask-form"><input id="ask-q" type="text" placeholder="Ask a practical safety question about this area…" autocomplete="off"><button class="btn-ghost">Ask</button></form>
      <div id="answer" class="answer" hidden></div>
      <button type="button" id="share-loc" class="link-btn">${state.userLoc ? 'Location permission granted' : 'Share my location for proximity questions'}</button>
    </div>
  </details>`;
}
function renderStats(message) {
  // The reveal button lives in the replay controls (top left), where an open area panel can't cover it.
  $('#reveal').hidden = !(state.asOf && !state.firesRevealed && state.risk && !message);
  if (message) {
    $('#stats').innerHTML = `<div class="stat" style="border-right:0;min-width:0"><div class="k">Status</div><div class="v name" style="max-width:none;white-space:normal">${esc(message)}</div></div>`;
    return;
  }
  const r = state.risk, f = state.fires;
  if (!r) { renderStats('Waiting for data…'); return; }
  const counts = r.counts_by_hazard?.[state.hazard] || r.counts;
  const hot = (counts.Extreme || 0) + (counts['Very high'] || 0) + (counts.High || 0);
  const top = r.top_by_hazard?.[state.hazard]?.[0] || r.top?.[0];
  const topScore = state.hazard === 'wildfire' ? top?.risk : top?.score;
  const topCell = top ? cellById(top.id) : null;
  const firesLabel = state.asOf ? 'Fires, next 72 h' : 'Fire detections, 48 h';
  const firesVal = state.asOf && !state.firesRevealed ? '<small>hidden</small>' : `${f?.count ?? '–'}`;
  const wxBits = topCell?.raw
    ? `${fmtWx(topCell.raw.tmax_c, 0)}°C · RH ${fmtWx(topCell.raw.rh_min_pct, 0)}% · gust ${fmtWx(topCell.raw.gust_kmh, 0)}`
    : '—';
  $('#stats').innerHTML = `
    <div class="stat"><div class="k">${firesLabel}</div><div class="v">${firesVal}</div></div>
    <div class="stat"><div class="k">High+ ${esc(HAZARDS[state.hazard].label.toLowerCase())} areas</div><div class="v">${hot}<small> / ${r.cells.length}</small></div></div>
    <div class="stat"><div class="k">Highest ${esc(HAZARDS[state.hazard].label.toLowerCase())}</div><div class="v name" title="${esc(top ? top.name : '')}">${top ? `${esc(top.name)} · ${Number(topScore).toFixed(0)}` : '–'}</div></div>
    <div class="stat"><div class="k">Top-area weather</div><div class="v name" title="${esc(wxBits)}">${esc(wxBits)}</div></div>`;
}
async function reveal() {
  state.firesRevealed = true;
  renderFires(true); renderStats();
  setTimeout(runValidation, 2600);
}

// ------------------------------------------------------------------ side panel: one area
function closePanel() {
  state.selected = null; $('#panel').hidden = true; highlight();
}
function firesInCell(id) {
  if (!state.fires || !state.firesRevealed) return null;
  return state.fires.detections.filter((d) => d.cell_id === id).length;
}
function selectCell(id, { fly = true, focus = false } = {}) {
  const c = cellById(id);
  if (!c) return;
  state.selected = id;
  highlight();
  if (fly && map.getZoom() < 7) go([c.lat, c.lon], 7);
  else if (fly && !map.getBounds().pad(-0.15).contains([c.lat, c.lon])) go([c.lat, c.lon], map.getZoom());
  const n = state.risk.cells.length;
  const fic = firesInCell(id);
  const hazardData = activeHazard(c);
  const factors = [...(hazardData.factors || [])].sort((a, b) => b.points - a.points);
  const score = hazardData.score;
  const category = hazardData.category;
  const hazardInfo = HAZARDS[state.hazard];
  const rank = hazardData.rank || c.rank;
  const actions = hazardActions(state.hazard, score);
  if (state.view !== 'overview') setAppView('overview');
  const panel = $('#panel');
  panel.hidden = false;
  panel.innerHTML = `
    <button class="close" aria-label="Close">×</button>
    <div class="eyebrow">${esc(hazardInfo.label).toUpperCase()} · AREA ${esc(c.id)}</div>
    <h2 tabindex="-1">${esc(c.name)}</h2>
    <div class="muted small">${c.lat.toFixed(3)}°, ${c.lon.toFixed(3)}°</div>
    <div class="score-row" style="margin-top:10px">
      <div class="score" style="--c:${riskColor(score ?? 0)}">${score == null ? '—' : Math.round(score)}<span>/100</span></div>
      <div>
        ${category ? `<span class="chip" style="background:${catColor(category)};color:${catTextColor(category)}">${esc(category)}</span>` : '<span class="chip unavailable">Score unavailable</span>'}
        <div class="muted small" style="margin-top:6px">${rank ? `Rank #${rank} of ${n} statewide` : 'Required forecast measurements unavailable'}</div>
        ${state.hazard === 'wildfire' && fic != null ? `<div class="muted small">${fic} detection${fic === 1 ? '' : 's'} inside this cell</div>` : ''}
        <p class="note" style="margin:6px 0 0">Relative index, not a probability or official warning.</p>
      </div>
    </div>

    ${firesBlock(c)}
    ${weatherBlock(c)}

    <div class="panel-section">
      <h3>Contributing factors</h3>
      <p class="muted small hazard-caveat">${esc(hazardInfo.caveat)}</p>
      <ul class="factors">${factors.map((f) => `
        <li>
          <div class="f-top"><span>${esc(f.label)}<span class="w">weight ${(f.weight * 100).toFixed(0)}%</span></span><span class="pts">+${f.points.toFixed(1)}</span></div>
          <div class="bar"><i style="width:${((f.value ?? 0) * 100).toFixed(0)}%"></i></div>
          <div class="f-exp">${f.value == null ? 'no data — weight redistributed' : esc(f.explain)}</div>
        </li>`).join('')}
      </ul>
      ${factors.length ? '' : '<div class="muted small">No usable measurements for this hazard window.</div>'}
      ${state.hazard === 'wildfire' ? `<p class="gate muted">Fuel gate ×${c.fuel_gate.toFixed(2)}${c.veg_gate != null ? ` · vegetation ×${c.veg_gate.toFixed(2)}` : ''}.</p>` : ''}
    </div>

    ${stationsNearBlock(c)}
    ${freshnessBlock()}

    <div class="hazard-actions"><b>Preparedness suggestions</b><ul>${actions.map((action) => `<li>${esc(action)}</li>`).join('')}</ul>
      <p class="note" style="margin-top:6px">Follow official local alerts. Call 911 for immediate danger.</p>
    </div>

    ${assistantBlock(c)}

    ${!state.asOf && state.config?.database?.ready ? `<div class="panel-section"><h3>Risk history <span class="engine">${state.config.database.timescale ? 'Tiger Data · TimescaleDB' : 'PostgreSQL'}</span></h3>
    <div id="history" class="history"><div class="muted small">Loading history…</div></div></div>` : ''}

    <p class="note">Satellite tracks live on <button type="button" class="link-btn" id="panel-open-satellites">Satellite Tracking</button>.</p>`;
  panel.scrollTop = 0;
  $('.close', panel).onclick = closePanel;
  $('#ask-form').onsubmit = (e) => { e.preventDefault(); ask(id); };
  $('#listen')?.addEventListener('click', () => listen(id));
  $('#panel-open-satellites')?.addEventListener('click', () => setAppView('satellites'));
  panel.querySelectorAll('.ai-chip').forEach((b) => b.addEventListener('click', () => {
    $('#ask-q').value = b.dataset.q;
    $('#ai-assistant').open = true;
    ask(id);
  }));
  $('#share-loc')?.addEventListener('click', () => requestUserLocation());
  loadBriefing(id);
  if ($('#history')) loadHistory(id, 14);
  setTimeout(() => renderPasses(c), 30);
  const top = factors[0];
  announce(`${c.name}. ${hazardInfo.label} score ${score == null ? 'unavailable' : `${Math.round(score)} out of 100, ${category}`}.${rank ? ` Rank ${rank} of ${n}.` : ''}${top ? ` Biggest factor: ${top.label}, ${top.explain}.` : ''}`);
  if (focus) $('h2', panel).focus({ preventScroll: true });
}
function announce(text) {
  const el = $('#sr-status');
  el.textContent = '';
  setTimeout(() => { el.textContent = text; }, 50);
}
async function loadHistory(id, days = 14) {
  try {
    const hazardKey = state.hazard || 'wildfire';
    const hazardLabel = (HAZARDS[hazardKey] && HAZARDS[hazardKey].label) || 'Risk';
    const h = await api(`/api/history/${id}?days=${days}`);
    const el = $('#history');
    if (!el || state.selected !== id) return;
    const series = h[hazardKey] || h.wildfire || h.risk || [];
    if (series.length < 2) {
      el.innerHTML = `<div class="muted small">Not enough ${esc(hazardLabel.toLowerCase())} history yet for the past ${days} days. Backfill with <code>scripts/backfill_tiger_history.py --days ${days} --fire-days 3</code>, then refreshes will add more points.</div>`;
      return;
    }
    const W = 360, H = 70, end = Date.now(), start = end - days * 864e5;
    const x = (t) => ((Date.parse(t) - start) / (end - start)) * W;
    const y = (v) => H - 4 - (Math.max(0, Math.min(100, v)) / 100) * (H - 8);
    const pts = series.map((p) => `${x(p.t).toFixed(1)},${y(p.avg).toFixed(1)}`).join(' ');
    const showFireBars = hazardKey === 'wildfire' && (h.fires || []).length;
    const bars = showFireBars ? h.fires.map((f) => `<rect x="${(x(f.t) - 2).toFixed(1)}" y="${H - 10}" width="${Math.max(3, W / days - 2).toFixed(1)}" height="10" rx="2" fill="#ff3b1f" opacity=".85"><title>${f.count} detections</title></rect>`).join('') : '';
    const last = series[series.length - 1];
    const weeks = Math.round(days / 7);
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="${esc(hazardLabel)} index over the past ${weeks} weeks">
        ${[25, 50, 75].map((v) => `<line x1="0" x2="${W}" y1="${y(v)}" y2="${y(v)}" stroke="rgba(148,163,184,.15)"/>`).join('')}
        ${bars}<polyline points="${pts}" fill="none" stroke="${riskColor(last.avg)}" stroke-width="2" stroke-linejoin="round"/>
        <circle cx="${x(last.t)}" cy="${y(last.avg)}" r="3.5" fill="${riskColor(last.avg)}"/></svg>
      <div class="muted small spark-legend"><span>${weeks} weeks ago</span><span>${showFireBars ? '<i></i> days with fire detections · ' : ''}${series.length} points · saved in DB for fast lookup</span><span>now ${last.avg.toFixed(0)}</span></div>`;
  } catch (e) {
    const el = $('#history');
    if (el) el.innerHTML = `<div class="muted small">History unavailable: ${esc(e.message)}</div>`;
  }
}
async function loadBriefing(id) {
  const asOf = state.asOf;
  const hazard = state.hazard;
  try {
    const b = await api(`/api/briefing/${id}${qs({ hazard })}`);
    if (state.selected !== id || state.asOf !== asOf || state.hazard !== hazard) return;
    const el = $('#briefing');
    el.classList.remove('loading');
    el.textContent = b.text;
    $('#engine').textContent = b.engine !== 'template' ? b.engine : b.error ? 'template · Gemini failed (hover)' : 'template (add GEMINI_API_KEY)';
    if (b.error) $('#engine').title = b.error;
  } catch (e) {
    if (state.selected === id) { $('#briefing').textContent = `Briefing unavailable: ${e.message}`; $('#briefing').classList.remove('loading'); }
  }
}
const askApi = (question, cellId) => api('/api/ask', {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ question, cell_id: cellId, as_of: state.asOf }),
});
function renderAnswer(el, a) {
  const trace = a.steps?.length
    ? `<ol class="trace" aria-label="Tools the agent called">${a.steps.map((s) => `<li class="${s.ok ? '' : 'bad'}">${esc(s.tool)}<span>(${esc(s.args)})</span></li>`).join('')}</ol>` : '';
  const links = (a.areas || []).filter((id) => id !== state.selected && cellById(id)).slice(0, 4);
  el.innerHTML = `${trace}<div class="answer-text"></div>
    ${links.length ? `<div class="row">${links.map((id) => `<button class="btn-ghost area-link" data-id="${esc(id)}">${esc(cellById(id).name)} →</button>`).join('')}</div>` : ''}
    <div class="note">${esc(a.engine)}${a.steps?.length ? ` · ${a.steps.length} tool call${a.steps.length === 1 ? '' : 's'}` : ''}</div>`;
  $('.answer-text', el).textContent = a.text;
  el.querySelectorAll('.area-link').forEach((b) => b.addEventListener('click', () => selectCell(b.dataset.id, { focus: true })));
  announce(a.text);
}
function composeAssistantQuestion(q, cell) {
  // Keep the user question short; backend attaches selected-cell context when cell_id is sent.
  let prefix = '';
  if (state.userLoc) {
    prefix = `[User shared location ${state.userLoc.lat.toFixed(4)}, ${state.userLoc.lon.toFixed(4)}. Use it only for proximity relative to tool-verified detections; otherwise say permission was granted but you still need fires_near results.]\n`;
  } else if (/near me|my location|how far|from me|proximity/i.test(q)) {
    prefix = '[User asked about personal proximity but has not shared geolocation. Ask them to share location or answer only for the selected map area.]\n';
  }
  if (cell) {
    prefix += `[Answer for selected area ${cell.id} (${cell.name}). Prefer fires_near + find_area tools.]\n`;
  }
  return `${prefix}${q}`.slice(0, 600);
}
function requestUserLocation() {
  if (!navigator.geolocation) { toast('Geolocation is not available in this browser.'); return; }
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      state.userLoc = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      toast('Location shared for this session. Ask again for proximity-aware guidance.');
      if (state.selected) selectCell(state.selected, { fly: false });
    },
    () => toast('Location permission denied. Proximity answers will use the selected map area only.'),
    { enableHighAccuracy: false, timeout: 10000 },
  );
}
async function ask(id) {
  const q = $('#ask-q').value.trim();
  if (!q) return;
  const out = $('#answer');
  const details = $('#ai-assistant');
  if (details) details.open = true;
  out.hidden = false; out.innerHTML = '<div class="muted thinking">Assistant is querying PyroWatch tools…</div>';
  try {
    const a = await askApi(composeAssistantQuestion(q, cellById(id)), id);
    if (state.selected === id) renderAnswer(out, a);
  } catch (e) { out.textContent = `Error: ${e.message}`; }
}
async function askGlobal(q, cached = null) {
  state.selected = null; highlight();
  setAppView('overview');
  const panel = $('#panel');
  panel.hidden = false;
  panel.innerHTML = `
    <button class="close" aria-label="Close">×</button>
    <div class="eyebrow">ASK PYROWATCH · ${state.config?.agent === 'gemini' ? 'GEMINI AGENT' : 'OFFLINE ROUTER'}</div>
    <h2 tabindex="-1"></h2>
    <div class="ai-safety">Statewide questions still use real backend tools. Select a map area for location-specific emergency assistance.</div>
    <div id="g-answer" class="answer"><div class="muted thinking">Assistant is querying PyroWatch tools…</div></div>`;
  $('h2', panel).textContent = q;
  $('.close', panel).onclick = closePanel;
  $('h2', panel).focus({ preventScroll: true });
  try {
    const a = cached || await askApi(composeAssistantQuestion(q, null), null);
    state.lastAnswer = a;
    renderAnswer($('#g-answer'), a);
  } catch (e) { $('#g-answer').textContent = `Error: ${e.message}`; }
}
let audio;
async function listen(id) {
  const btn = $('#listen');
  btn.disabled = true; btn.textContent = 'Generating voice…';
  try {
    const r = await fetch(`/api/tts/${id}${qs({ hazard: state.hazard })}`);
    if (!r.ok) throw new Error((await r.json()).detail);
    audio?.pause();
    audio = new Audio(URL.createObjectURL(await r.blob()));
    audio.play();
    btn.textContent = '▶ Playing'; audio.onended = () => { btn.textContent = '▶ Listen to briefing'; btn.disabled = false; };
  } catch (e) { toast(`Voice unavailable: ${e.message}`); btn.textContent = '▶ Listen to briefing'; btn.disabled = false; }
}

// ------------------------------------------------------------------ validation
async function runValidation() {
  loading('Checking the prediction against what the satellites actually saw…');
  try {
    const v = await api(`/api/validate${qs()}`);
    state.validation = v;
    renderValidation(v);
  } catch (e) { toast(`Validation failed: ${e.message}`); } finally { done(); }
}
function renderValidation(v) {
  layers.validation.clearLayers();
  const sig = v.cells.filter((c) => c.significant);
  for (const c of sig) {
    L.circleMarker([c.lat, c.lon], {
      pane: 'validation', radius: 17, fill: false, weight: 2.5, color: c.in_top ? '#1e4d7b' : '#9fb0c3',
      dashArray: c.in_top ? null : '4 4', className: 'val-ring',
    }).bindTooltip(`<b>New fire</b> · ${esc(c.name)}<br>We ranked it #${c.rank} (risk ${c.risk.toFixed(0)}) before detection`, { className: 'tip' })
      .on('click', () => selectCell(c.id)).addTo(layers.validation);
  }
  state.selected = null; highlight(); closePanel();
  const pc = (x) => (x == null ? '–' : `${Math.round(x * 100)}%`);
  const topPct = Math.round(v.top_fraction * 100);
  const a = v.all_hotspots || {};
  const html = `
    <div class="eyebrow">VALIDATION · PREDICTION ${esc(v.as_of)} · FIRES ${esc(v.fire_window)}</div>
    ${v.new_fire_cells === 0
      ? `<p class="v-claim">No significant new fires were detected in this window${v.already_burning_cells ? ` (${v.already_burning_cells} area(s) were already burning beforehand)` : ''}. Try a replay date from Overview.</p>`
      : `<p class="v-claim"><b>${v.hits} of ${v.new_fire_cells}</b> significant new fires (${pc(v.hit_rate)}) started in areas already in our <b>top ${topPct}%</b> before any satellite saw them.</p>
         <div class="v-hero">
           <div><div class="k">Hit rate</div><div class="v">${pc(v.hit_rate)}</div></div>
           <div><div class="k">vs. random</div><div class="v">${v.lift}×</div></div>
           <div><div class="k">ROC AUC</div><div class="v">${v.auc ?? '–'}</div></div>
         </div>
         <p class="muted small">Random guessing would put ${topPct}% of fires in the top ${topPct}% (1.0×). AUC 0.5 = random, 1.0 = perfect ranking.</p>`}
    ${v.baseline_ffwi?.significant?.new_fire_cells ? `<p class="small">Fosberg-only baseline: ${v.baseline_ffwi.significant.hits} of ${v.baseline_ffwi.significant.new_fire_cells} (${pc(v.baseline_ffwi.significant.hit_rate)}), ${v.baseline_ffwi.significant.lift}× vs random, AUC ${v.baseline_ffwi.significant.auc ?? '–'}.</p>` : ''}
    ${a.new_fire_cells ? `<p class="small">All hotspots: ${a.hits} of ${a.new_fire_cells} (${pc(a.hit_rate)}), ${a.lift}× vs random, AUC ${a.auc ?? '–'}.</p>` : ''}
    <p class="small"><b>Method:</b> ${esc(v.method)}. "Significant" = ${esc(v.significant_rule)}; ${esc(v.new_rule || '')}.</p>
    ${sig.length ? `<h3 class="sources-section-title">Significant new fires</h3>
    <ul class="v-list">${sig.map((c) => `
      <li data-id="${esc(c.id)}">
        <span class="rk ${c.in_top ? 'hit' : ''}">#${c.rank}</span>
        <span><b>${esc(c.name)}</b><div class="meta">first seen ${esc(fmtUtc(c.first_detection_utc))} by ${esc(c.first_satellite)} · ${c.detections} detections · peak ${c.max_frp} MW</div></span>
        <span class="pts">${c.risk.toFixed(0)}</span>
      </li>`).join('')}</ul>` : ''}
    ${v.cells.length > sig.length ? `<p class="note">${v.cells.length - sig.length} smaller hotspot area(s) not listed.</p>` : ''}
    <ul class="caveats">${v.caveats.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
  setAppView('analytics');
  const body = $('#analytics-body');
  body.innerHTML = html;
  body.querySelectorAll('.v-list li').forEach((li) => li.addEventListener('click', () => selectCell(li.dataset.id)));
  if (state.asOf && !state.firesRevealed) { state.firesRevealed = true; renderFires(true); renderStats(); }
}

// ------------------------------------------------------------------ satellites (SGP4 in the browser)
function subpoint(satrec, t) {
  const pv = satellite.propagate(satrec, t);
  if (!pv || !pv.position) return null;
  const g = satellite.eciToGeodetic(pv.position, satellite.gstime(t));
  return { lat: satellite.degreesLat(g.latitude), lon: satellite.degreesLong(g.longitude), alt: g.height };
}
function satIcon(name) {
  return L.divIcon({
    className: '', iconSize: [120, 20], iconAnchor: [9, 10],
    html: `<div class="sat-icon"><svg width="18" height="18" viewBox="0 0 24 24"><g fill="#1e4d7b"><rect x="9" y="9" width="6" height="6" rx="1"/><rect x="1" y="10" width="7" height="4" opacity=".75"/><rect x="16" y="10" width="7" height="4" opacity=".75"/></g></svg>${esc(name)}</div>`,
  });
}
function setSatLayerVisible(on) {
  state.show.sats = on;
  if (on) layers.sats.addTo(map); else map.removeLayer(layers.sats);
  const btn = $('#sources-toggle-sats');
  if (btn) {
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-pressed', String(on));
    btn.textContent = on ? 'Hide tracks on map' : 'Show tracks on map';
  }
}
async function initSatellites() {
  try {
    const s = await api('/api/satellites');
    state.satSource = s.source;
    state.sats = s.satellites.map((x) => {
      const satrec = satellite.twoline2satrec(x.tle1, x.tle2);
      const marker = L.marker([0, 0], { pane: 'sats', icon: satIcon(x.name), keyboard: false })
        .bindTooltip(`${esc(x.name)} · ${esc(x.instrument)} · ${x.swath_km} km swath`, { className: 'tip' });
      const footprint = L.circle([0, 0], { pane: 'sats', radius: (x.swath_km / 2) * 1000, color: '#1e4d7b', weight: 1, opacity: 0.35, fillOpacity: 0.04, interactive: false, className: 'footprint' });
      const track = L.polyline([], { pane: 'sats', color: '#1e4d7b', weight: 1.2, opacity: 0.45, dashArray: '3 6', interactive: false });
      [track, footprint, marker].forEach((l) => l.addTo(layers.sats));
      return { ...x, satrec, marker, footprint, track };
    });
    if (state.risk) renderHeader();
    if (state.view === 'satellites') renderSatellitesBody();
    updateTracks(); tick();
    setInterval(tick, 1000);
    setInterval(updateTracks, 60000);
  } catch (e) {
    $('#ticker').innerHTML = `<span class="lbl">Satellites unavailable: ${esc(e.message)}</span>`;
    if (state.view === 'satellites') renderSatellitesBody();
  }
}
function updateTracks() {
  const now = Date.now();
  for (const s of state.sats) {
    const segs = [[]];
    let prev = null;
    for (let m = -50; m <= 50; m += 1) {
      const p = subpoint(s.satrec, new Date(now + m * 60000));
      if (!p) continue;
      if (prev && Math.abs(p.lon - prev.lon) > 180) segs.push([]);
      segs[segs.length - 1].push([p.lat, p.lon]);
      prev = p;
    }
    s.track.setLatLngs(segs);
  }
}
const overCA = (p) => p.lat > 32.4 && p.lat < 42.1 && p.lon > -124.6 && p.lon < -114;
function tick() {
  const now = new Date();
  const chips = [];
  for (const s of state.sats) {
    const p = subpoint(s.satrec, now);
    if (!p) continue;
    s.pos = p; s.alt = p.alt;
    s.marker.setLatLng([p.lat, p.lon]);
    s.footprint.setLatLng([p.lat, p.lon]);
    const where = overCA(p) ? 'over California' : `${Math.abs(p.lat).toFixed(1)}°${p.lat >= 0 ? 'N' : 'S'} ${Math.abs(p.lon).toFixed(1)}°${p.lon >= 0 ? 'E' : 'W'}`;
    chips.push(`<button class="sat-chip ${overCA(p) ? 'over' : ''}" data-name="${esc(s.name)}"><b>${esc(s.name)}</b><span>${esc(s.instrument)} · ${Math.round(p.alt)} km · ${where}</span></button>`);
  }
  const t = $('#ticker');
  if (!t) return;
  t.innerHTML = `<span class="lbl">Fire-watch satellites${state.sats.some((s) => s.simulated) ? ' (synthetic TLEs)' : ''}</span>${chips.join('')}`;
  t.querySelectorAll('.sat-chip').forEach((b) => b.addEventListener('click', () => {
    const s = state.sats.find((x) => x.name === b.dataset.name);
    if (s?.pos) go([s.pos.lat, s.pos.lon], 4);
  }));
}
function nextPasses(c, hours = 12) {
  const out = [];
  const now = Date.now();
  for (const s of state.sats) {
    const reach = (s.swath_km / 2) * 0.7; // keep to the better-resolved middle of the swath
    let best = null;
    for (let m = 0; m <= hours * 60; m += 1) {
      const p = subpoint(s.satrec, new Date(now + m * 60000));
      if (!p) continue;
      const d = haversineKm(c.lat, c.lon, p.lat, p.lon);
      if (d <= reach && (!best || d < best.d)) best = { d, t: now + m * 60000 };
      else if (best && d > reach) break; // left the pass: keep the closest approach
    }
    if (best) out.push({ sat: s, ...best });
  }
  return out.sort((a, b) => a.t - b.t);
}
function renderPasses(c) {
  const el = $('#passes');
  if (!el) return;
  if (!state.sats.length) { el.innerHTML = '<div class="muted small">Orbit data unavailable.</div>'; return; }
  const passes = nextPasses(c);
  if (!passes.length) { el.innerHTML = '<div class="muted small">No passes in the next 12 hours.</div>'; return; }
  el.innerHTML = passes.map((p) => {
    const mins = Math.round((p.t - Date.now()) / 60000);
    const when = mins < 1 ? 'overhead now' : mins < 90 ? `in ${mins} min` : `in ${(mins / 60).toFixed(1)} h`;
    return `<div class="pass"><span><b>${esc(p.sat.name)}</b> · ${esc(p.sat.instrument)}<br><span class="muted">closest ${Math.round(p.d)} km off-track</span></span><span class="t">${when}<br><span class="muted">${fmtTime(new Date(p.t))}</span></span></div>`;
  }).join('');
}

// ------------------------------------------------------------------ data sources & satellite provenance
const SOURCE_META = {
  Weather: { provider: 'Open-Meteo', cadence: 'Forecast/archive pull; risk cache ~30 min near present', role: 'Temperature, humidity, wind, precipitation, CAPE, snowfall' },
  Fires: { provider: 'NASA FIRMS (VIIRS / MODIS)', cadence: 'NRT detections; cache ~15 min near present', role: 'Active fire / thermal anomaly detections' },
  Terrain: { provider: 'Open-Meteo elevation', cadence: 'Cached locally after first fetch', role: 'Relief / terrain samples per grid cell' },
  History: { provider: 'NASA FIRMS archive (local)', cadence: 'Static dataset rebuilt by scripts', role: 'Five-year fire-day counts by cell' },
  Vegetation: { provider: 'NASA MODIS via ORNL DAAC', cadence: 'Static NDVI dataset', role: 'Spring greenness / vegetation gate' },
  Orbits: { provider: 'CelesTrak TLE', cadence: 'TLE refresh TTL ~4 hours', role: 'Orbital elements for fire-watch satellites' },
};
function statusPill(mode) {
  const m = mode === 'live' ? 'live' : mode === 'off' ? 'off' : 'demo';
  return `<span class="status-pill ${m}">${m === 'demo' ? 'synthetic' : m}</span>`;
}
function renderSatellitesBody() {
  const body = $('#satellites-body');
  if (!body) return;
  const orbit = state.satSource;
  const cards = `
    <article class="prov-card">
      <h3>Orbits ${statusPill(orbit?.mode || 'off')}</h3>
      <div class="prov-meta">
        <div><b>API / dataset:</b> ${esc(orbit?.source || SOURCE_META.Orbits.provider)}</div>
        <div><b>Role:</b> ${esc(SOURCE_META.Orbits.role)}</div>
        <div><b>Update cadence:</b> ${esc(SOURCE_META.Orbits.cadence)}</div>
        ${orbit?.note ? `<div>${esc(orbit.note)}</div>` : ''}
      </div>
    </article>
    <article class="prov-card">
      <h3>Risk linkage</h3>
      <div class="prov-meta">
        <div>Satellite tracks visualize detection platforms (VIIRS/MODIS). They do <b>not</b> contribute points to the hazard risk index.</div>
        <div>Fire detections shown on Overview come from NASA FIRMS, not from these live track markers.</div>
      </div>
    </article>`;

  const satRows = state.sats.length
    ? state.sats.map((s) => {
      const sim = s.simulated || state.satSource?.mode === 'demo';
      const pos = s.pos ? `${s.pos.lat.toFixed(2)}°, ${s.pos.lon.toFixed(2)}° · ${Math.round(s.alt || 0)} km` : 'propagating…';
      return `<tr>
        <td><b>${esc(s.name)}</b><div class="muted small">NORAD ${esc(s.norad || s.norad_id || '—')}</div></td>
        <td>${esc(s.instrument)}<div class="muted small">${esc(String(s.swath_km))} km swath</div></td>
        <td>${statusPill(sim ? 'demo' : (state.satSource?.mode || 'live'))}<div class="muted small">${esc(pos)}</div></td>
        <td><code>${esc(s.tle1 || '')}\n${esc(s.tle2 || '')}</code></td>
      </tr>`;
    }).join('')
    : `<tr><td colspan="4" class="muted">Orbit catalog unavailable.</td></tr>`;

  const selected = state.selected ? cellById(state.selected) : null;
  const passSection = selected
    ? `<div>
        <h3 class="sources-section-title">Estimated passes over ${esc(selected.name)}</h3>
        <div id="passes" class="passes"><div class="muted small">Propagating orbits…</div></div>
        <p class="note">Browser SGP4 from ${state.sats.some((s) => s.simulated) ? '<b>synthetic</b> TLEs (demo/offline)' : 'CelesTrak TLEs'}. Pass estimates are not risk inputs.</p>
      </div>`
    : `<p class="note">Select an Overview map area first to estimate overhead passes for that cell.</p>`;

  body.innerHTML = `
    <div class="prov-grid">${cards}</div>
    <div>
      <div class="sources-actions" style="justify-content:space-between;margin-bottom:8px">
        <h3 class="sources-section-title" style="margin:0">Orbital parameters &amp; TLEs</h3>
        <button type="button" id="sources-toggle-sats" class="btn-ghost" aria-pressed="${state.show.sats}">${state.show.sats ? 'Hide tracks on map' : 'Show tracks on map'}</button>
      </div>
      <div class="sat-table-wrap">
        <table class="sat-table">
          <thead><tr><th>Satellite</th><th>Instrument / swath</th><th>Status</th><th>TLE</th></tr></thead>
          <tbody>${satRows}</tbody>
        </table>
      </div>
    </div>
    ${passSection}`;
  $('#sources-toggle-sats')?.addEventListener('click', () => setSatLayerVisible(!state.show.sats));
  if (selected) setTimeout(() => renderPasses(selected), 30);
}
function renderStationsBody() {
  const body = $('#stations-body');
  if (!body) return;
  if (!state.stations.length) {
    body.innerHTML = `<p class="muted">No ground stations are reporting yet. Beacons POST sensor readings to <code>/api/stations/{id}</code> and appear here within the poll window.</p>`;
    return;
  }
  body.innerHTML = `
    <div class="station-table-wrap">
      <table class="station-table">
        <thead><tr><th>Station</th><th>Place / cell</th><th>Latest reading</th><th>Age</th></tr></thead>
        <tbody>${state.stations.map((s) => `
          <tr>
            <td><b>${esc(s.label)}</b><div class="muted small">${esc(s.station_id || s.id || '')}</div></td>
            <td>${esc(s.place || '—')}<div class="muted small">${esc(s.cell_id || '')} · ${s.lat?.toFixed?.(3) ?? s.lat}, ${s.lon?.toFixed?.(3) ?? s.lon}</div></td>
            <td>${esc(fmtReading(s.latest))}</td>
            <td>${esc(ago(s.latest.time_utc))}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>
    <p class="note">${state.stations.length} station${state.stations.length === 1 ? '' : 's'} in memory. Readings are process-local and reset if the server restarts.</p>`;
}
function setAppView(view) {
  const allowed = ['overview', 'satellites', 'analytics', 'stations'];
  state.view = allowed.includes(view) ? view : 'overview';
  document.body.classList.remove('view-overview', 'view-satellites', 'view-analytics', 'view-stations');
  document.body.classList.add(`view-${state.view}`);
  $('#satellites-view').hidden = state.view !== 'satellites';
  $('#analytics-view').hidden = state.view !== 'analytics';
  $('#stations-view').hidden = state.view !== 'stations';
  $('#ticker').hidden = state.view !== 'satellites';
  const tabMap = {
    overview: '#view-overview',
    satellites: '#view-satellites',
    analytics: '#view-analytics',
    stations: '#view-stations',
  };
  Object.entries(tabMap).forEach(([name, sel]) => {
    const el = $(sel);
    if (!el) return;
    const on = state.view === name;
    el.classList.toggle('active', on);
    el.setAttribute('aria-selected', String(on));
  });
  if (state.view === 'satellites') {
    renderSatellitesBody();
    setSatLayerVisible(true);
  } else if (state.show.sats) {
    setSatLayerVisible(false);
  }
  if (state.view === 'stations') renderStationsBody();
  setTimeout(() => map.invalidateSize(), 50);
}

// ------------------------------------------------------------------ controls
function setMode(replay) {
  $('#mode-live').classList.toggle('active', !replay);
  $('#mode-replay').classList.toggle('active', replay);
  $('#mode-live').setAttribute('aria-selected', String(!replay));
  $('#mode-replay').setAttribute('aria-selected', String(replay));
  $('#replay-controls').hidden = !replay;
  state.asOf = replay ? ($('#replay-date').value || state.config.replay_presets[0].date) : null;
  if (replay) $('#replay-date').value = state.asOf;
  return loadData();
}
function initControls() {
  const presets = state.config.replay_presets;
  $('#preset').innerHTML = presets.map((p) => `<option value="${p.date}">${esc(p.label)}</option>`).join('') + '<option value="">Custom date…</option>';
  $('#replay-date').max = state.config.today;
  $('#replay-date').value = presets[0].date;
  $('#preset').onchange = (e) => {
    if (!e.target.value) { $('#replay-date').focus(); return; }
    $('#replay-date').value = e.target.value; state.asOf = e.target.value; loadData();
  };
  $('#replay-date').onchange = (e) => {
    if (!e.target.value) return;
    $('#preset').value = presets.some((p) => p.date === e.target.value) ? e.target.value : '';
    state.asOf = e.target.value; loadData();
  };
  $('#mode-live').onclick = () => setMode(false);
  $('#mode-replay').onclick = () => setMode(true);
  $('#validate').onclick = () => { setAppView('analytics'); runValidation(); };
  $('#analytics-run').onclick = runValidation;
  $('#reveal').onclick = reveal;
  $('#hazard-select').value = state.hazard;
  $('#hazard-select').onchange = (e) => {
    if (!HAZARDS[e.target.value]) return;
    state.hazard = e.target.value;
    try { localStorage.setItem('pw-hazard', state.hazard); } catch (_) { /* storage blocked */ }
    $('.legend-title').textContent = `${HAZARDS[state.hazard].mapLabel} · next 48 h`;
    if (state.risk) {
      renderRisk(); renderStats();
      if (state.selected) selectCell(state.selected, { fly: false });
    }
  };
  document.querySelectorAll('.toggle').forEach((b) => b.addEventListener('click', () => {
    const k = b.dataset.layer;
    state.show[k] = !state.show[k];
    b.classList.toggle('on', state.show[k]);
    b.setAttribute('aria-pressed', String(state.show[k]));
    if (state.show[k]) layers[k].addTo(map); else map.removeLayer(layers[k]);
  }));
  $('#palette-btn').onclick = () => {
    palette = palette === 'safe' ? 'classic' : 'safe';
    try { localStorage.setItem('pw-palette', palette); } catch (_) { /* storage blocked */ }
    applyPalette();
    if (state.risk) { renderRisk(); renderStats(); if (state.selected) selectCell(state.selected, { fly: false }); }
  };
  applyPalette();
  $('#view-btn').onclick = () => {
    riskView = riskView === 'smooth' ? 'grid' : 'smooth';
    try { localStorage.setItem('pw-view', riskView); } catch (_) { /* storage blocked */ }
    applyView();
    if (state.risk) renderRisk();
  };
  applyView();
  $('#omni').onsubmit = (e) => {
    e.preventDefault();
    const q = $('#omni-q').value.trim();
    if (!q) return;
    const hit = findArea(q);
    if (hit) { selectCell(hit, { focus: true }); $('#omni-q').value = ''; } else askGlobal(q);
  };
  initKeyboard();
  $('#about-btn').onclick = showIntro;
  $('#intro-close').onclick = hideIntro;
  $('#intro-tour').onclick = () => startTour();
  $('#tour-stop').onclick = stopTour;
  $('#view-overview').onclick = () => setAppView('overview');
  $('#view-satellites').onclick = () => setAppView('satellites');
  $('#view-analytics').onclick = () => setAppView('analytics');
  $('#view-stations').onclick = () => setAppView('stations');
  $('#satellites-close').onclick = () => setAppView('overview');
  $('#analytics-close').onclick = () => setAppView('overview');
  $('#stations-close').onclick = () => setAppView('overview');
  $('#open-satellites-link').onclick = () => setAppView('satellites');
}

// ------------------------------------------------------------------ intro card + guided tour
// The intro shows once per browser (not under automation, so screenshot runs stay clean).
// The tour steps through every feature hands-free; ?tour=1 loops it for an expo screen and
// reuses the first agent answer so a looping kiosk doesn't spend Gemini quota.
const tour = { running: false, run: 0, loop: false, answer: null };
const TOUR_QUESTION = 'Where is fire risk highest right now, and when does a satellite next pass over it?';
function maybeShowIntro() {
  let seen = false;
  try { seen = localStorage.getItem('pw-intro-seen') === '1'; } catch (_) { /* storage blocked */ }
  if (!seen && !navigator.webdriver) showIntro();
}
function showIntro() { $('#intro').hidden = false; $('#intro-tour').focus(); }
function hideIntro() {
  $('#intro').hidden = true;
  try { localStorage.setItem('pw-intro-seen', '1'); } catch (_) { /* storage blocked */ }
}
const tourWait = (ms, run) => new Promise((resolve, reject) => {
  setTimeout(() => (tour.running && tour.run === run ? resolve() : reject(new Error('stopped'))), ms);
});
async function tourUntil(test, run, timeout = 30000) {
  const t0 = Date.now();
  while (!test() && Date.now() - t0 < timeout) await tourWait(250, run);
}
function tourInterrupt(e) { if (!e.target.closest('#tourbar')) stopTour(); }
function stopTour() {
  if (!tour.running) return;
  tour.running = false;
  tour.run++;
  $('#tourbar').hidden = true;
  document.removeEventListener('pointerdown', tourInterrupt, true);
}
async function startTour({ loop = false } = {}) {
  hideIntro();
  stopTour();
  tour.running = true;
  tour.loop = loop;
  const run = ++tour.run;
  $('#tourbar').hidden = false;
  document.addEventListener('pointerdown', tourInterrupt, true);
  const preset = state.config.replay_presets[0];
  const steps = [
    ['Overview: live hazard risk for every 50 km area of California.', async () => {
      setAppView('overview');
      if (state.asOf) await setMode(false);
      closePanel(); layers.validation.clearLayers(); go([37.25, -119.3], 6);
    }, 6000],
    ['Click a cell for weather, factors, nearby detections, and a contextual AI assistant.', async () => {
      selectCell(state.risk.top[0].id);
    }, 8000],
    ['Open Ask AI about this area for practical guidance grounded in backend tools.', async () => {
      const details = $('#ai-assistant');
      if (details) details.open = true;
      await tourUntil(() => !$('#briefing')?.classList.contains('loading'), run, 20000);
      details?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'nearest' });
    }, 8000],
    ["Ask a statewide question — Gemini still calls PyroWatch's own tools.", async () => {
      await askGlobal(TOUR_QUESTION, tour.answer);
      if (!tour.answer && state.lastAnswer?.engine !== 'error') tour.answer = state.lastAnswer;
    }, 9000],
    ['Satellite Tracking keeps orbits and TLEs off the Overview map.', async () => {
      closePanel();
      setAppView('satellites');
      const s = state.sats.filter((x) => x.pos).sort((a, b) =>
        Math.abs(a.pos.lat - 37) + Math.abs(a.pos.lon + 119) - Math.abs(b.pos.lat - 37) - Math.abs(b.pos.lon + 119))[0];
      if (s) go([s.pos.lat, s.pos.lon], 3);
    }, 7000],
    [`Replay: the map as it stood on ${fmtLong(preset.date)}, the day before the fires started.`, async () => {
      setAppView('overview');
      go([35.6, -119.3], 7);
      $('#replay-date').value = preset.date;
      $('#preset').value = preset.date;
      await setMode(true);
      const c = state.risk.cells.find((x) => x.name === '25 km N of Malibu') || state.risk.top[0];
      selectCell(c.id);
    }, 9000],
    ['Analytics & Validation compares the ranking with what satellites saw next.', async () => {
      await reveal();
      await tourUntil(() => !!document.querySelector('.v-claim'), run, 30000);
    }, 11000],
  ];
  try {
    do {
      for (let i = 0; i < steps.length; i++) {
        const [text, act, ms] = steps[i];
        $('#tour-step').textContent = `${i + 1}/${steps.length}`;
        $('#tour-text').textContent = text;
        announce(text);
        try { await act(); } catch (e) { if (e.message === 'stopped') throw e; }
        await tourWait(ms, run);
      }
    } while (tour.loop);
    stopTour();
  } catch (_) { /* stopped by the user */ }
}
function applyPalette() {
  const stops = PALETTES[palette].stops;
  $('.scale').style.background = `linear-gradient(90deg, ${stops.map(([v, c]) => `rgb(${c.join(',')}) ${v}%`).join(', ')})`;
  $('#palette-btn').textContent = `Colors: ${PALETTES[palette].label} ⇄`;
  $('#palette-btn').setAttribute('aria-label', `Color scale: ${PALETTES[palette].label}. Switch.`);
  $('.legend-title').textContent = `${HAZARDS[state.hazard].mapLabel} · next 48 h`;
}

// ------------------------------------------------------------------ keyboard + search
function cellAt(lat, lon) {
  const g = state.config.grid;
  const id = `r${String(Math.floor((lat - g.south) / g.step)).padStart(2, '0')}c${String(Math.floor((lon - g.west) / g.step)).padStart(2, '0')}`;
  if (cellById(id)) return id;
  const near = state.risk.cells.reduce((best, c) => {
    const d = haversineKm(lat, lon, c.lat, c.lon);
    return d < best.d ? { id: c.id, d } : best;
  }, { id: null, d: Infinity });
  return near.d < 60 ? near.id : null;
}
function findArea(q) {
  const ql = q.toLowerCase().replace(/\s+—.*$/, '').trim();
  const place = state.config.places.find(([n]) => n.toLowerCase() === ql);
  if (place) return cellAt(place[1], place[2]);
  const cell = state.risk.cells.find((c) => c.name.toLowerCase() === ql || c.id === ql);
  return cell ? cell.id : null;
}
function buildAreaList() {
  const opts = [...state.config.places.map(([n]) => n), ...state.risk.cells.filter((c) => !c.name.endsWith(' area')).map((c) => c.name)];
  $('#area-list').innerHTML = [...new Set(opts)].sort().map((n) => `<option value="${esc(n)}"></option>`).join('');
}
function neighbor(id, dr, dc) {
  const m = /^r(\d+)c(\d+)$/.exec(id);
  if (!m) return null;
  for (let k = 1; k <= 4; k++) {
    const nid = `r${String(+m[1] + dr * k).padStart(2, '0')}c${String(+m[2] + dc * k).padStart(2, '0')}`;
    if (cellById(nid)) return nid;
  }
  return null;
}
function initKeyboard() {
  const moves = { ArrowUp: [1, 0], ArrowDown: [-1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName);
    if (e.key === 'Escape') {
      if (tour.running) { stopTour(); return; }
      if (!$('#intro').hidden) { hideIntro(); return; }
      if (state.view !== 'overview') { setAppView('overview'); return; }
      if (typing) document.activeElement.blur(); else closePanel();
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === '/') { e.preventDefault(); $('#omni-q').focus(); return; }
    if (e.key === '+' || e.key === '=') { map.zoomIn(); return; }
    if (e.key === '-') { map.zoomOut(); return; }
    if (moves[e.key] && state.risk) {
      e.preventDefault();
      const from = state.selected || state.risk.top[0].id;
      const next = state.selected ? neighbor(from, ...moves[e.key]) : from;
      if (next) selectCell(next, { focus: true });
    }
  });
}

// ------------------------------------------------------------------ ground stations (PyroWatch Beacons)
const stationIcon = (label) => L.divIcon({
  className: '', iconSize: [140, 22], iconAnchor: [10, 11],
  html: `<div class="station-icon"><svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 13v9M8 22h8" stroke="#5eead4" stroke-width="2" fill="none"/><circle cx="12" cy="11" r="2.4" fill="#5eead4"/><path d="M7.5 6.5a6.4 6.4 0 0 0 0 9M16.5 6.5a6.4 6.4 0 0 1 0 9M4.6 3.6a10.5 10.5 0 0 0 0 14.8M19.4 3.6a10.5 10.5 0 0 1 0 14.8" stroke="#5eead4" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>${esc(label)}</div>`,
});
const ago = (iso) => {
  const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
  return m < 1 ? 'just now' : m < 90 ? `${m} min ago` : `${(m / 60).toFixed(1)} h ago`;
};
const fmtReading = (r) => [r.temp_c != null ? `${r.temp_c.toFixed(1)} °C` : null, r.rh_pct != null ? `${r.rh_pct.toFixed(0)}% RH` : null, r.smoke_raw != null ? `smoke ${Math.round(r.smoke_raw)}` : null].filter(Boolean).join(' · ') || 'no sensor data';
async function pollStations() {
  try {
    const { stations } = await api('/api/stations');
    state.stations = stations;
    layers.stations.clearLayers();
    for (const s of stations) {
      L.marker([s.lat, s.lon], { pane: 'sats', icon: stationIcon(s.label), keyboard: false })
        .bindPopup(`<b>Ground station · ${esc(s.label)}</b><br>${esc(fmtReading(s.latest))}<br><span class="muted small">${esc(s.place)} · ${esc(ago(s.latest.time_utc))}</span>`)
        .addTo(layers.stations);
    }
    const t = $('.toggle[data-layer="stations"]');
    if (t) {
      t.hidden = !stations.length;
      t.querySelector('.count').textContent = stations.length ? ` (${stations.length})` : '';
    }
    if (state.view === 'stations') renderStationsBody();
    if (state.selected && state.view === 'overview' && $('#panel') && !$('#panel').hidden) {
      // refresh nearby-station section without rebuilding the whole panel unless assistant is idle
    }
  } catch (_) { /* stations are optional */ }
}

(async function init() {
  try {
    state.config = await api('/api/config');
  } catch (e) {
    done();
    renderStats(`Backend not reachable. Start the server, then refresh. (${e.message})`);
    toast(`Backend not reachable: ${e.message}`);
    announce('Backend not reachable. Start the server, then refresh.');
    return;
  }
  initControls();
  initSatellites();
  pollStations();
  setInterval(pollStations, 20000);
  await loadData();
  if (new URLSearchParams(location.search).get('tour') === '1') startTour({ loop: true });
  else maybeShowIntro();
})();
