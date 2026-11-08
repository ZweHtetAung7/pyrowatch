/* Overview side panel for one selected area, split into lazily-rendered tabs. */
import { state, cellById, activeHazard } from './store.js';
import { api, withAsOf } from './api.js';
import { $, $$, esc, fmtNum, fmtUtc, haversineKm, ago, fmtReading } from './util.js';
import { riskColor, catColor, catTextColor } from './palette.js';
import { HAZARDS, hazardActions } from '../hazards.js';
import { announce, toast } from './ui.js';
import { ask, answers, renderAnswer, PROMPTS, requestUserLocation } from './ai.js';
import { sources, freshLabel } from './data.js';

const TABS = [['summary', 'Summary'], ['weather', 'Weather'], ['nearby', 'Nearby'], ['assistant', 'Assistant'], ['data', 'Data']];

// ------------------------------------------------------------------ tab bodies
function summaryTab(c) {
  const h = activeHazard(c), info = HAZARDS[state.hazard];
  const factors = [...(h.factors || [])].sort((a, b) => b.points - a.points);
  const gate = state.hazard === 'wildfire' && c.fuel_gate != null
    ? `<p class="note">Fuel gate ×${c.fuel_gate.toFixed(2)}${c.veg_gate != null ? ` · vegetation ×${c.veg_gate.toFixed(2)}` : ''}.</p>` : '';
  return `<section>
      <h3>Contributing factors</h3>
      <p class="muted small">${esc(info.caveat)}</p>
      <ul class="factors">${factors.map((f) => `
        <li>
          <div class="f-top"><span>${esc(f.label)}<span class="w">weight ${(f.weight * 100).toFixed(0)}%</span></span><b>+${f.points.toFixed(1)}</b></div>
          <div class="bar"><i style="width:${((f.value ?? 0) * 100).toFixed(0)}%"></i></div>
          <div class="f-exp">${f.value == null ? 'no data — weight redistributed' : esc(f.explain)}</div>
        </li>`).join('') || '<li class="muted small">No usable measurements for this hazard window.</li>'}</ul>
      ${gate}
    </section>
    <section class="actions">
      <h3>Preparedness suggestions</h3>
      <ul>${hazardActions(state.hazard, h.score).map((a) => `<li>${esc(a)}</li>`).join('')}</ul>
      <p class="note">Follow official local alerts. Call 911 for immediate danger.</p>
    </section>`;
}

function weatherTab(c) {
  const r = c.raw || {};
  const sets = {
    thunderstorm: [['Peak CAPE', fmtNum(r.cape_max_jkg), 'J/kg'], ['Temp max', fmtNum(r.tmax_c, 1), '°C'], ['Gust', fmtNum(r.gust_kmh), 'km/h'], ['RH min', fmtNum(r.rh_min_pct), '%']],
    blizzard: [['Snowfall 48h', fmtNum(r.snowfall_48h_cm, 1), 'cm'], ['Temp max', fmtNum(r.tmax_c, 1), '°C'], ['Gust', fmtNum(r.gust_kmh), 'km/h'], ['Wind', fmtNum(r.wind_kmh), 'km/h']],
    wildfire: [['Temp max', fmtNum(r.tmax_c, 1), '°C'], ['RH min', fmtNum(r.rh_min_pct), '%'], ['Gust', fmtNum(r.gust_kmh), 'km/h'], ['FFWI', fmtNum(r.ffwi), '/100'],
      ['Precip 30d', fmtNum(r.precip_30d_mm, 1), 'mm'], ['Days since rain', r.days_since_rain == null ? '—' : String(r.days_since_rain), ''], ['VPD', fmtNum(r.vpd_kpa, 2), 'kPa'], ['Wind', fmtNum(r.wind_kmh), 'km/h']],
  };
  const cells = [...(sets[state.hazard] || sets.wildfire)];
  if (state.hazard === 'wildfire' && c.ml_prob != null) cells.push(['ML fire (7d)', (c.ml_prob * 100).toFixed(0), '%']);
  const veg = c.vegetation
    ? `<b>Vegetation:</b> ${esc(c.vegetation)}${c.veg_gate != null ? ` · gate ×${c.veg_gate.toFixed(2)}` : ''}${r.ndvi != null ? ` · NDVI ${Number(r.ndvi).toFixed(2)}` : ''}`
    : 'Vegetation: unavailable';
  return `<section><h3>Weather</h3>
      <div class="wx-grid">${cells.map(([k, v, u]) => `<div class="wx-cell"><div class="k">${esc(k)}</div><div class="v">${esc(v)}${u ? `<span class="u"> ${esc(u)}</span>` : ''}</div></div>`).join('')}</div>
    </section>
    <section><h3>Vegetation</h3><div class="muted small">${veg}</div>
      <p class="note">Forecast measurements from Open-Meteo. Values are inputs to the selected hazard index, not official warnings.</p></section>`;
}

function nearbyTab(c) {
  let fires;
  if (state.hazard !== 'wildfire') {
    fires = '<p class="muted small">Fire detection overlays are wildfire-specific. Switch hazard to Wildfire to inspect FIRMS hotspots near this area.</p>';
  } else if (!state.fires?.detections || !state.firesRevealed) {
    fires = `<p class="muted small">${state.asOf && !state.firesRevealed ? 'Detections are hidden until you reveal the replay outcome.' : 'Fire detection layer unavailable.'}</p>`;
  } else {
    const near = state.fires.detections
      .map((d) => ({ ...d, km: haversineKm(c.lat, c.lon, d.lat, d.lon) }))
      .filter((d) => d.km <= 75).sort((a, b) => a.km - b.km).slice(0, 6);
    fires = near.length
      ? `<ul class="near-list">${near.map((d) => `<li><span><b>${esc(d.satellite)}</b> · ${esc(d.instrument)}<div class="meta">${esc(fmtUtc(d.time_utc))} · FRP ${d.frp.toFixed(1)} MW · ${esc(d.confidence)}</div></span><span class="dist">${d.km < 1 ? `${Math.round(d.km * 1000)} m` : `${d.km.toFixed(1)} km`}</span></li>`).join('')}</ul>
         <p class="note">Distances are from the cell center. Hotspots are satellite observations, not confirmed ignitions.</p>`
      : '<p class="muted small">No FIRMS detections within ~75 km of this cell center in the current window. Absence of detections is not proof of safety.</p>';
  }
  const st = state.stations
    .map((s) => ({ ...s, km: haversineKm(c.lat, c.lon, s.lat, s.lon) }))
    .filter((s) => s.km <= 80 && Date.now() - Date.parse(s.latest.time_utc) < 6 * 3.6e6)
    .sort((a, b) => a.km - b.km).slice(0, 5);
  const stations = st.length
    ? `<ul class="near-list">${st.map((s) => `<li><span><b>${esc(s.label)}</b><div class="meta">${esc(fmtReading(s.latest))} · ${esc(ago(s.latest.time_utc))}</div></span><span class="dist">${s.km.toFixed(1)} km</span></li>`).join('')}</ul>`
    : '<p class="muted small">No recent Beacon readings within ~80 km.</p>';
  return `<section><h3>Fire detections</h3>${fires}</section><section><h3>Ground stations</h3>${stations}</section>`;
}

function assistantTab(c) {
  return `<div class="ai-safety">Uses the selected area via <b>/api/ask</b> tools. Does not invent distances or alerts. A low score is not "safe." For immediate danger call <b>911</b>.</div>
    <div class="muted small">Context: ${esc(c.name)} · ${esc(HAZARDS[state.hazard].label)}${state.userLoc ? ' · your location shared' : ''}</div>
    <div id="briefing" class="briefing loading" style="margin-top:8px">Preparing area briefing…</div>
    <div class="row">${state.config?.has_tts ? '<button id="listen" class="btn-ghost">▶ Listen</button>' : ''}<span class="engine" id="engine"></span></div>
    <div class="ai-chips">${PROMPTS.map((p) => `<button type="button" class="ai-chip" data-q="${esc(p)}">${esc(p)}</button>`).join('')}</div>
    <form class="ask-form" id="ask-form"><input id="ask-q" type="text" placeholder="Ask a practical safety question…" autocomplete="off"><button class="btn-ghost">Ask</button></form>
    <div id="answer" class="answer" hidden></div>
    <p class="note"><button type="button" id="share-loc" class="link-btn">${state.userLoc ? 'Location permission granted' : 'Share my location for proximity questions'}</button></p>`;
}

function dataTab() {
  return `<section><h3>Data freshness</h3>
      <div class="fresh-row">${sources().map(([k, s]) => {
    const l = freshLabel(s);
    return `<span class="fresh ${l}" title="${esc(s ? `${s.source || k}${s.note ? ` — ${s.note}` : ''}` : 'No status')}">${esc(k)} · ${l}</span>`;
  }).join('')}</div>
      <p class="note">DEMO/UNAVAILABLE layers are not observed live conditions. A low risk index is not a safety clearance.</p></section>
    ${!state.asOf && state.config?.database?.ready ? `<section><h3>${esc(HAZARDS[state.hazard].label)} over the past 2 weeks <span class="engine">${state.config.database.timescale ? 'Tiger Data · TimescaleDB' : 'PostgreSQL'}</span></h3><div id="history"><div class="muted small">Loading history…</div></div></section>` : ''}`;
}

// ------------------------------------------------------------------ async tab behavior
const briefings = new Map();
async function loadBriefing(c) {
  const el = $('#briefing');
  const k = `${c.id}|${state.hazard}|${state.asOf || ''}|${state.risk.generated_at}`;
  let b = briefings.get(k);
  if (!b) {
    try { b = await api(withAsOf(`/api/briefing/${c.id}`, { hazard: state.hazard })); briefings.set(k, b); }
    catch (e) { if (el.isConnected) { el.textContent = `Briefing unavailable: ${e.message}`; el.classList.remove('loading'); } return; }
  }
  if (!el.isConnected) return;
  el.classList.remove('loading');
  el.textContent = b.text;
  const eng = $('#engine');
  eng.textContent = b.engine !== 'template' ? b.engine : b.error ? 'template · Gemini failed (hover)' : 'template (add GEMINI_API_KEY)';
  if (b.error) eng.title = b.error;
}

let audio;
async function listen(id) {
  const btn = $('#listen');
  btn.disabled = true; btn.textContent = 'Generating voice…';
  const reset = () => { btn.textContent = '▶ Listen'; btn.disabled = false; };
  try {
    const r = await fetch(withAsOf(`/api/tts/${id}`, { hazard: state.hazard }));
    if (!r.ok) throw new Error((await r.json()).detail);
    audio?.pause();
    audio = new Audio(URL.createObjectURL(await r.blob()));
    audio.onended = reset;
    audio.play();
    btn.textContent = '▶ Playing';
  } catch (e) { toast(`Voice unavailable: ${e.message}`); reset(); }
}

async function loadHistory(id, days = 14) {
  const el = $('#history');
  if (!el) return;
  try {
    const h = await api(`/api/history/${id}?days=${days}`);
    if (!el.isConnected) return;
    const hazardKey = state.hazard || 'wildfire';
    const hazardLabel = HAZARDS[hazardKey]?.label || 'Risk';
    const series = h[hazardKey] || h.wildfire || h.risk || [];
    if (series.length < 2) {
      el.innerHTML = `<div class="muted small">Not enough ${esc(hazardLabel.toLowerCase())} history yet for the past ${days} days. Backfill with <code>scripts/backfill_tiger_history.py --days ${days} --fire-days 3</code>, then refreshes will add more points.</div>`;
      return;
    }
    const W = 360, H = 70, end = Date.now(), start = end - days * 864e5;
    const x = (t) => ((Date.parse(t) - start) / (end - start)) * W;
    const y = (v) => H - 4 - (Math.max(0, Math.min(100, v)) / 100) * (H - 8);
    const showFireBars = hazardKey === 'wildfire' && (h.fires || []).length > 0;
    const bars = showFireBars ? h.fires.map((f) => `<rect x="${(x(f.t) - 2).toFixed(1)}" y="${H - 10}" width="${Math.max(3, W / days - 2).toFixed(1)}" height="10" rx="2" fill="#ff3b1f" opacity=".85"><title>${f.count} detections</title></rect>`).join('') : '';
    const last = series[series.length - 1];
    const weeks = Math.round(days / 7);
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="${esc(hazardLabel)} index over the past ${weeks} weeks">
        ${[25, 50, 75].map((v) => `<line x1="0" x2="${W}" y1="${y(v)}" y2="${y(v)}" stroke="rgba(148,163,184,.15)"/>`).join('')}
        ${bars}
        <polyline points="${series.map((p) => `${x(p.t).toFixed(1)},${y(p.avg).toFixed(1)}`).join(' ')}" fill="none" stroke="${riskColor(last.avg)}" stroke-width="2" stroke-linejoin="round"/>
        <circle cx="${x(last.t)}" cy="${y(last.avg)}" r="3.5" fill="${riskColor(last.avg)}"/></svg>
      <div class="muted small" style="display:flex;justify-content:space-between"><span>${weeks} weeks ago</span><span>${showFireBars ? '<i></i> days with fire detections · ' : ''}${series.length} points · saved in DB for fast lookup</span><span>now ${last.avg.toFixed(0)}</span></div>`;
  } catch (e) { if (el.isConnected) el.innerHTML = `<div class="muted small">History unavailable: ${esc(e.message)}</div>`; }
}

const BODY = { summary: summaryTab, weather: weatherTab, nearby: nearbyTab, assistant: assistantTab, data: dataTab };
const BIND = {
  assistant(c) {
    loadBriefing(c);
    const out = $('#answer');
    const submit = (q) => { q = q.trim(); if (q) ask(out, q, c.id); };
    $('#ask-form').onsubmit = (e) => { e.preventDefault(); submit($('#ask-q').value); };
    $$('.ai-chip').forEach((b) => b.addEventListener('click', () => { $('#ask-q').value = b.dataset.q; submit(b.dataset.q); }));
    $('#listen')?.addEventListener('click', () => listen(c.id));
    $('#share-loc').onclick = () => requestUserLocation(() => showTab(c));
    if (answers.has(c.id)) renderAnswer(out, answers.get(c.id));
  },
  data(c) { loadHistory(c.id); },
};

function showTab(c) {
  $$('.tabs button').forEach((b) => {
    const on = b.dataset.tab === state.tab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', String(on));
  });
  const body = $('#tabbody');
  body.innerHTML = BODY[state.tab](c);
  body.scrollTop = 0;
  BIND[state.tab]?.(c);
}

// ------------------------------------------------------------------ view
export const panel = {
  render(root, opts = {}) {
    const c = cellById(state.selected);
    if (!c) { root.hidden = true; return; }
    const h = activeHazard(c), info = HAZARDS[state.hazard];
    const n = state.risk.cells.length, rank = h.rank || c.rank;
    root.innerHTML = `
      <header class="sheet-head">
        <div>
          <div class="eyebrow">${esc(info.label)} · area ${esc(c.id)}</div>
          <h2 tabindex="-1">${esc(c.name)}</h2>
          <div class="muted small mono">${c.lat.toFixed(3)}°, ${c.lon.toFixed(3)}°</div>
        </div>
        <button class="icon-btn" data-close aria-label="Close">×</button>
      </header>
      <div class="score-row">
        <div class="score" style="--c:${riskColor(h.score ?? 0)}">${h.score == null ? '—' : Math.round(h.score)}<span>/100</span></div>
        <div>
          ${h.category ? `<span class="chip" style="background:${catColor(h.category)};color:${catTextColor(h.category)}">${esc(h.category)}</span>` : '<span class="chip unavailable">Score unavailable</span>'}
          <div class="muted small" style="margin-top:6px">${rank ? `Rank #${rank} of ${n}` : 'Required forecast measurements unavailable'}</div>
          <div class="note" style="margin:2px 0 0">Relative index, not a probability or official warning.</div>
        </div>
      </div>
      <div class="tabs" role="tablist">${TABS.map(([k, label]) => `<button role="tab" data-tab="${k}">${label}</button>`).join('')}</div>
      <div class="tabbody" id="tabbody" role="tabpanel"></div>`;
    $('.tabs', root).addEventListener('click', (e) => {
      const b = e.target.closest('[data-tab]');
      if (b) { state.tab = b.dataset.tab; showTab(c); }
    });
    showTab(c);
    if (opts.announce) {
      const top = [...(h.factors || [])].sort((a, b) => b.points - a.points)[0];
      announce(`${c.name}. ${info.label} score ${h.score == null ? 'unavailable' : `${Math.round(h.score)} out of 100, ${h.category}`}.${rank ? ` Rank ${rank} of ${n}.` : ''}${top ? ` Biggest factor: ${top.label}, ${top.explain}.` : ''}`);
    }
  },
};
