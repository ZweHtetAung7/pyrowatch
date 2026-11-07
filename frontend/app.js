'use strict';
/* PyroWatch frontend: Leaflet map + risk grid + FIRMS fires + live satellite orbits (satellite.js). */

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ------------------------------------------------------------------ colors
const STOPS = [[0, [26, 152, 80]], [20, [166, 217, 106]], [30, [254, 224, 139]], [45, [253, 174, 97]], [60, [244, 109, 67]], [75, [215, 48, 39]], [100, [142, 1, 82]]];
function riskColor(v) {
  v = Math.max(0, Math.min(100, v));
  for (let i = 1; i < STOPS.length; i++) {
    const [b, cb] = STOPS[i];
    if (v <= b) {
      const [a, ca] = STOPS[i - 1];
      const t = (v - a) / (b - a);
      const c = ca.map((x, k) => Math.round(x + t * (cb[k] - x)));
      return `rgb(${c[0]},${c[1]},${c[2]})`;
    }
  }
  return 'rgb(142,1,82)';
}
const CAT_COLORS = { Low: '#2fbf71', Moderate: '#e0c341', High: '#f39a45', 'Very high': '#ef4b36', Extreme: '#d0247a' };

// ------------------------------------------------------------------ state
const state = {
  config: null, asOf: null, risk: null, fires: null, selected: null, validation: null,
  sats: [], satSource: null, firesRevealed: true, show: { risk: true, fires: true, sats: true },
};

// ------------------------------------------------------------------ map
const map = L.map('map', { zoomControl: false, minZoom: 3, worldCopyJump: true }).setView([37.25, -119.3], 6);
L.control.zoom({ position: 'bottomright' }).addTo(map);
for (const [name, z] of [['labels', 610], ['validation', 615], ['fires', 620], ['sats', 630]]) {
  map.createPane(name).style.zIndex = z;
}
map.getPane('labels').style.pointerEvents = 'none';
const CARTO = 'https://{s}.basemaps.cartocdn.com/{style}/{z}/{x}/{y}{r}.png';
L.tileLayer(CARTO, { style: 'dark_nolabels', subdomains: 'abcd', maxZoom: 18, attribution: '&copy; OpenStreetMap contributors &copy; CARTO · Fire data: NASA FIRMS · Weather: Open-Meteo · Orbits: CelesTrak' }).addTo(map);
L.tileLayer(CARTO, { style: 'dark_only_labels', subdomains: 'abcd', maxZoom: 18, pane: 'labels' }).addTo(map);

const layers = {
  risk: L.layerGroup().addTo(map),
  fires: L.layerGroup().addTo(map),
  sats: L.layerGroup().addTo(map),
  validation: L.layerGroup().addTo(map),
};
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
  loading(state.asOf ? `Rebuilding the risk map as it looked on ${fmtLong(state.asOf)}…` : 'Pulling satellite fire detections and the 48-hour forecast…');
  try {
    const [risk, fires] = await Promise.all([
      api(`/api/risk${qs()}`),
      api(`/api/fires${qs({ days: state.asOf ? 3 : 2 })}`),
    ]);
    state.risk = risk; state.fires = fires; state.validation = null;
    state.firesRevealed = !state.asOf; // in replay, fires stay hidden until "Reveal what happened"
    layers.validation.clearLayers();
    renderRisk(); renderFires(); renderHeader(); renderStats();
    if (state.selected && cellById(state.selected)) selectCell(state.selected, { fly: false });
    else closePanel();
  } catch (e) {
    toast(`Could not load data: ${e.message}`);
  } finally {
    done();
  }
}

// ------------------------------------------------------------------ rendering: risk grid
function renderRisk() {
  layers.risk.clearLayers();
  for (const k of Object.keys(polys)) delete polys[k];
  for (const c of state.risk.cells) {
    const col = riskColor(c.risk);
    const p = L.polygon(c.shape, { color: col, weight: 0.6, opacity: 0.55, fillColor: col, fillOpacity: 0.5 });
    p.bindTooltip(`<b>${esc(c.name)}</b><br>Risk ${c.risk.toFixed(0)} · ${esc(c.category)}`, { sticky: true, className: 'tip', direction: 'top', offset: [0, -8] });
    p.on('click', () => selectCell(c.id));
    p.on('mouseover', () => { if (state.selected !== c.id) p.setStyle({ fillOpacity: 0.7 }); });
    p.on('mouseout', () => { if (state.selected !== c.id) p.setStyle({ fillOpacity: 0.5 }); });
    p.addTo(layers.risk);
    polys[c.id] = p;
  }
  highlight();
}
function highlight() {
  for (const [id, p] of Object.entries(polys)) {
    const sel = id === state.selected;
    p.setStyle(sel ? { color: '#ffffff', weight: 2.5, opacity: 1, fillOpacity: 0.72 } : { color: p.options.fillColor, weight: 0.6, opacity: 0.55, fillOpacity: 0.5 });
    if (sel) p.bringToFront();
  }
}

// ------------------------------------------------------------------ rendering: fires
function firePopup(d) {
  const sat = state.sats.find((s) => s.name === d.satellite);
  const alt = sat?.alt ? `${Math.round(sat.alt)} km` : d.instrument === 'MODIS' ? '~705 km' : '~830 km';
  return `<b>🔥 Active fire detection</b><br>
    Seen by <b>${esc(d.satellite)}</b> (${esc(d.instrument)}) from ${alt} up<br>
    ${esc(fmtUtc(d.time_utc))} · ${d.daynight === 'N' ? 'night pass' : 'day pass'}<br>
    Fire radiative power: <b>${d.frp.toFixed(1)} MW</b> · confidence ${esc(d.confidence)}<br>
    <span class="muted small">${d.lat.toFixed(3)}, ${d.lon.toFixed(3)} · ${esc(d.source)}</span>`;
}
function fireMarker(d, latest) {
  const ageH = (latest - Date.parse(d.time_utc)) / 3.6e6;
  const recent = ageH < 12;
  return L.circleMarker([d.lat, d.lon], {
    pane: 'fires', radius: 2.5 + Math.min(6, Math.log1p(d.frp)), weight: 1, color: '#ffd27a', opacity: 0.9,
    fillColor: recent ? '#ff3b1f' : '#ff8a1f', fillOpacity: 0.9, className: recent ? 'fire-dot recent' : 'fire-dot',
  }).bindPopup(firePopup(d));
}
function renderFires(animate = false) {
  layers.fires.clearLayers();
  if (!state.fires || !state.firesRevealed) return;
  const dets = [...state.fires.detections].sort((a, b) => a.time_utc.localeCompare(b.time_utc));
  const latest = dets.length ? Date.parse(dets[dets.length - 1].time_utc) : Date.now();
  if (!animate) { dets.forEach((d) => fireMarker(d, latest).addTo(layers.fires)); return; }
  const step = Math.min(40, 2200 / Math.max(1, dets.length));
  dets.forEach((d, i) => setTimeout(() => fireMarker(d, latest).addTo(layers.fires), i * step));
}

// ------------------------------------------------------------------ rendering: header + stats
function renderHeader() {
  const r = state.risk;
  const [a, b] = [r.as_of, new Date(Date.parse(`${r.as_of}T12:00:00`) + 864e5).toISOString().slice(0, 10)];
  $('#window-line').innerHTML = state.asOf
    ? `Replay · risk as it stood on <b>${esc(fmtLong(a))}</b>, using only data available that day`
    : `Forecast window <b>${esc(fmtDate(a))} → ${esc(fmtDate(b))}</b> · updated ${esc(fmtTime(new Date(r.generated_at)))}`;
  const src = [
    ['Weather', r.sources.weather], ['Fires', state.fires?.source], ['Terrain', r.sources.terrain],
    ['History', r.sources.history], ['Orbits', state.satSource],
  ].filter(([, s]) => s);
  const anyDemo = src.some(([, s]) => s.mode !== 'live');
  $('#badges').innerHTML = (anyDemo ? '<span class="badge demo-all" title="Some layers use synthetic data — see each badge">DEMO DATA</span>' : '')
    + src.map(([k, s]) => `<span class="badge ${s.mode !== 'live' ? 'demo' : ''}" title="${esc(s.source)}${s.note ? ` — ${esc(s.note)}` : ''}"><i></i>${k}: ${s.mode === 'live' ? 'live' : 'demo'}</span>`).join('');
}
function renderStats() {
  const r = state.risk, f = state.fires;
  const hot = (r.counts.Extreme || 0) + (r.counts['Very high'] || 0);
  const top = r.top[0];
  const firesLabel = state.asOf ? 'Fires, next 72 h' : 'Fire detections, 48 h';
  const firesVal = state.asOf && !state.firesRevealed ? '<small>hidden</small>' : `${f?.count ?? '–'}`;
  $('#stats').innerHTML = `
    <div class="stat"><div class="k">${firesLabel}</div><div class="v">${firesVal}</div></div>
    <div class="stat"><div class="k">Areas very high+</div><div class="v">${hot}<small> / ${r.cells.length}</small></div></div>
    <div class="stat"><div class="k">Highest risk</div><div class="v name" title="${esc(top.name)}">${esc(top.name)} · ${top.risk.toFixed(0)}</div></div>
    ${state.asOf && !state.firesRevealed ? '<button class="btn-reveal" id="reveal">Reveal what happened next ▸</button>' : ''}`;
  $('#reveal')?.addEventListener('click', reveal);
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
function selectCell(id, { fly = true } = {}) {
  const c = cellById(id);
  if (!c) return;
  state.selected = id;
  highlight();
  if (fly && map.getZoom() < 7) map.flyTo([c.lat, c.lon], 7, { duration: 0.8 });
  const n = state.risk.cells.length;
  const fic = firesInCell(id);
  const factors = [...c.factors].sort((a, b) => b.points - a.points);
  const panel = $('#panel');
  panel.hidden = false;
  panel.innerHTML = `
    <button class="close" aria-label="Close">×</button>
    <div class="eyebrow">AREA ${esc(c.id)} · ${c.lat.toFixed(2)}°, ${c.lon.toFixed(2)}°</div>
    <h2>${esc(c.name)}</h2>
    <div class="score-row">
      <div class="score" style="--c:${riskColor(c.risk)}">${Math.round(c.risk)}<span>/100</span></div>
      <div>
        <span class="chip" style="background:${CAT_COLORS[c.category]}">${esc(c.category)} risk</span>
        <div class="muted small" style="margin-top:6px">Rank #${c.rank} of ${n} areas statewide</div>
        ${c.ml_prob != null ? `<div class="small" style="margin-top:2px">ML model: <b>${(c.ml_prob * 100).toFixed(0)}%</b> chance of a new fire within 7 days</div>` : ''}
        ${fic != null ? `<div class="muted small">${fic} fire detection${fic === 1 ? '' : 's'} here ${state.asOf ? 'in the next 72 h' : 'in the last 48 h'}</div>` : ''}
      </div>
    </div>

    <h3>Why this score</h3>
    <ul class="factors">${factors.map((f) => `
      <li>
        <div class="f-top"><span>${esc(f.label)}<span class="w">weight ${(f.weight * 100).toFixed(0)}%</span></span><span class="pts">+${f.points.toFixed(1)}</span></div>
        <div class="bar"><i style="width:${((f.value ?? 0) * 100).toFixed(0)}%"></i></div>
        <div class="f-exp">${f.value == null ? 'no data — weight redistributed' : esc(f.explain)}</div>
      </li>`).join('')}
    </ul>
    <p class="gate muted">Points = 100 × weight × factor score × fuel gate (×${c.fuel_gate.toFixed(2)}). Wet vegetation shrinks the gate, so wind over green hills doesn't read as fire weather.</p>

    <h3>AI briefing <span class="engine" id="engine">…</span></h3>
    <div id="briefing" class="briefing loading">Analyzing conditions…</div>
    <div class="row">${state.config?.has_tts ? '<button id="listen" class="btn-ghost">▶ Listen to briefing</button>' : ''}</div>

    <h3>Ask PyroWatch</h3>
    <form id="ask-form"><input id="ask-q" type="text" placeholder="Why is this higher than the area next to it?" autocomplete="off"><button class="btn-ghost">Ask</button></form>
    <div id="answer" class="answer" hidden></div>

    <h3>Next satellite passes overhead</h3>
    <div id="passes" class="passes"><div class="muted small">Propagating orbits…</div></div>
    <div class="note">Fire detections come from VIIRS and MODIS instruments on these satellites. Orbits computed in your browser from ${state.sats.some((s) => s.simulated) ? '<b>simulated</b> orbital elements (offline mode)' : 'CelesTrak orbital elements (SGP4)'}.</div>`;
  panel.scrollTop = 0;
  $('.close', panel).onclick = closePanel;
  $('#ask-form').onsubmit = (e) => { e.preventDefault(); ask(id); };
  $('#listen')?.addEventListener('click', () => listen(id));
  loadBriefing(id);
  setTimeout(() => renderPasses(c), 30);
}
async function loadBriefing(id) {
  const asOf = state.asOf;
  try {
    const b = await api(`/api/briefing/${id}${qs()}`);
    if (state.selected !== id || state.asOf !== asOf) return;
    const el = $('#briefing');
    el.classList.remove('loading');
    el.textContent = b.text;
    $('#engine').textContent = b.engine === 'template' ? 'template (add GEMINI_API_KEY)' : b.engine;
    if (b.error) $('#engine').title = b.error;
  } catch (e) {
    if (state.selected === id) { $('#briefing').textContent = `Briefing unavailable: ${e.message}`; $('#briefing').classList.remove('loading'); }
  }
}
async function ask(id) {
  const q = $('#ask-q').value.trim();
  if (!q) return;
  const out = $('#answer');
  out.hidden = false; out.textContent = 'Thinking…';
  try {
    const a = await api('/api/ask', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ question: q, cell_id: id, as_of: state.asOf }) });
    if (state.selected === id) out.textContent = a.text;
  } catch (e) { out.textContent = `Error: ${e.message}`; }
}
let audio;
async function listen(id) {
  const btn = $('#listen');
  btn.disabled = true; btn.textContent = 'Generating voice…';
  try {
    const r = await fetch(`/api/tts/${id}${qs()}`);
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
  for (const c of v.cells) {
    L.circleMarker([c.lat, c.lon], {
      pane: 'validation', radius: 17, fill: false, weight: 2.5, color: c.in_top ? '#ffffff' : '#9fb0c3',
      dashArray: c.in_top ? null : '4 4', className: 'val-ring',
    }).bindTooltip(`<b>New fire</b> · ${esc(c.name)}<br>We ranked it #${c.rank} (risk ${c.risk.toFixed(0)}) before detection`, { className: 'tip' })
      .on('click', () => selectCell(c.id)).addTo(layers.validation);
  }
  state.selected = null; highlight();
  const pct = v.hit_rate != null ? Math.round(v.hit_rate * 100) : null;
  const topPct = Math.round(v.top_fraction * 100);
  const panel = $('#panel');
  panel.hidden = false;
  panel.innerHTML = `
    <button class="close" aria-label="Close">×</button>
    <div class="eyebrow">VALIDATION · PREDICTION ${esc(v.as_of)} · FIRES ${esc(v.fire_window)}</div>
    <h2>Did fires start where we predicted?</h2>
    ${v.new_fire_cells === 0
      ? `<p class="v-claim">No new fire starts were detected in this window${v.already_burning_cells ? ` (${v.already_burning_cells} area(s) were already burning beforehand)` : ''}. Try a replay date from the presets.</p>`
      : `<p class="v-claim"><b>${v.hits} of ${v.new_fire_cells}</b> new fire areas (${pct}%) were already in our <b>top ${topPct}%</b> highest-risk areas before any satellite saw them.</p>
         <div class="v-hero">
           <div><div class="k">Hit rate</div><div class="v">${pct}%</div></div>
           <div><div class="k">vs. random</div><div class="v">${v.lift}×</div></div>
           <div><div class="k">ROC AUC</div><div class="v">${v.auc ?? '–'}</div></div>
         </div>
         <p class="muted small">Random guessing would put ${topPct}% of fires in the top ${topPct}% (1.0×). AUC 0.5 = random, 1.0 = perfect ranking.</p>`}
    <p class="small"><b>Method:</b> ${esc(v.method)}. "New" = no detections in that area in the 5 days before.</p>
    ${v.cells.length ? `<h3>New fire areas, ranked by our earlier risk score</h3>
    <ul class="v-list">${v.cells.map((c) => `
      <li data-id="${esc(c.id)}">
        <span class="rk ${c.in_top ? 'hit' : ''}">#${c.rank}</span>
        <span><b>${esc(c.name)}</b><div class="meta">first seen ${esc(fmtUtc(c.first_detection_utc))} by ${esc(c.first_satellite)} · ${c.detections} detections · peak ${c.max_frp} MW</div></span>
        <span class="pts">${c.risk.toFixed(0)}</span>
      </li>`).join('')}</ul>` : ''}
    <ul class="caveats">${v.caveats.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
  panel.scrollTop = 0;
  $('.close', panel).onclick = () => { layers.validation.clearLayers(); closePanel(); };
  panel.querySelectorAll('.v-list li').forEach((li) => li.addEventListener('click', () => selectCell(li.dataset.id)));
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
    html: `<div class="sat-icon"><svg width="18" height="18" viewBox="0 0 24 24"><g fill="#7fd1ff"><rect x="9" y="9" width="6" height="6" rx="1"/><rect x="1" y="10" width="7" height="4" opacity=".75"/><rect x="16" y="10" width="7" height="4" opacity=".75"/></g></svg>${esc(name)}</div>`,
  });
}
async function initSatellites() {
  try {
    const s = await api('/api/satellites');
    state.satSource = s.source;
    state.sats = s.satellites.map((x) => {
      const satrec = satellite.twoline2satrec(x.tle1, x.tle2);
      const marker = L.marker([0, 0], { pane: 'sats', icon: satIcon(x.name), keyboard: false })
        .bindTooltip(`${esc(x.name)} · ${esc(x.instrument)} · ${x.swath_km} km swath`, { className: 'tip' });
      const footprint = L.circle([0, 0], { pane: 'sats', radius: (x.swath_km / 2) * 1000, color: '#7fd1ff', weight: 1, opacity: 0.35, fillOpacity: 0.04, interactive: false, className: 'footprint' });
      const track = L.polyline([], { pane: 'sats', color: '#7fd1ff', weight: 1.2, opacity: 0.45, dashArray: '3 6', interactive: false });
      [track, footprint, marker].forEach((l) => l.addTo(layers.sats));
      return { ...x, satrec, marker, footprint, track };
    });
    if (state.risk) renderHeader();
    updateTracks(); tick();
    setInterval(tick, 1000);
    setInterval(updateTracks, 60000);
  } catch (e) {
    $('#ticker').innerHTML = `<span class="lbl">Satellites unavailable: ${esc(e.message)}</span>`;
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
  t.innerHTML = `<span class="lbl">Fire-watch satellites${state.sats.some((s) => s.simulated) ? ' (simulated orbits)' : ''}</span>${chips.join('')}`;
  t.querySelectorAll('.sat-chip').forEach((b) => b.addEventListener('click', () => {
    const s = state.sats.find((x) => x.name === b.dataset.name);
    if (s?.pos) map.flyTo([s.pos.lat, s.pos.lon], 4, { duration: 1 });
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

// ------------------------------------------------------------------ controls
function setMode(replay) {
  $('#mode-live').classList.toggle('active', !replay);
  $('#mode-replay').classList.toggle('active', replay);
  $('#mode-live').setAttribute('aria-selected', String(!replay));
  $('#mode-replay').setAttribute('aria-selected', String(replay));
  $('#replay-controls').hidden = !replay;
  state.asOf = replay ? ($('#replay-date').value || state.config.replay_presets[0].date) : null;
  if (replay) $('#replay-date').value = state.asOf;
  loadData();
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
  $('#validate').onclick = runValidation;
  document.querySelectorAll('.toggle').forEach((b) => b.addEventListener('click', () => {
    const k = b.dataset.layer;
    state.show[k] = !state.show[k];
    b.classList.toggle('on', state.show[k]);
    if (state.show[k]) layers[k].addTo(map); else map.removeLayer(layers[k]);
  }));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closePanel(); });
}

(async function init() {
  try {
    state.config = await api('/api/config');
  } catch (e) {
    done(); toast(`Backend not reachable: ${e.message}`); return;
  }
  initControls();
  initSatellites();
  await loadData();
})();
