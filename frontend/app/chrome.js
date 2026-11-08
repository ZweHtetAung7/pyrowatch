/* Everything outside the map and the right-hand sheet: nav, hazard tabs, replay controls,
   layer menu, legend, banner and status bars. */
import { state } from './store.js';
import { $, $$, esc, fmtDate, fmtLong, fmtTime } from './util.js';
import { HAZARDS } from '../hazards.js';
import { palette } from './palette.js';
import { map, go, setLayerVisible } from './map.js';
import { loadData, setHazard, applyRisk, sources, freshLabel } from './data.js';
import { setView, refreshView } from './ui.js';
import { revealAndValidate } from './analytics.js';
import { requestUserLocation } from './ai.js';

const LAYER_TOGGLES = [['risk', 'Risk grid'], ['fires', 'Fire detections'], ['stations', 'Ground stations']];

export function initChrome() {
  const { replay_presets: presets, today } = state.config;

  $('#hazards').innerHTML = Object.entries(HAZARDS).map(([k, h]) =>
    `<button role="tab" data-hazard="${esc(k)}">${esc(h.label)}</button>`).join('');
  $('#hazards').addEventListener('click', (e) => { const b = e.target.closest('[data-hazard]'); if (b) setHazard(b.dataset.hazard); });

  $$('.nav button').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));

  // replay controls
  $('#preset').innerHTML = presets.map((p) => `<option value="${p.date}">${esc(p.label)}</option>`).join('') + '<option value="">Custom date…</option>';
  $('#replay-date').max = today;
  $('#replay-date').value = presets[0].date;
  const replayTo = (date) => { state.asOf = date; renderChrome(); loadData(); };
  $('#preset').onchange = (e) => { if (!e.target.value) { $('#replay-date').focus(); return; } $('#replay-date').value = e.target.value; replayTo(e.target.value); };
  $('#replay-date').onchange = (e) => {
    if (!e.target.value) return;
    $('#preset').value = presets.some((p) => p.date === e.target.value) ? e.target.value : '';
    replayTo(e.target.value);
  };
  const setMode = (replay) => {
    $('#mode-live').classList.toggle('active', !replay);
    $('#mode-replay').classList.toggle('active', replay);
    state.asOf = replay ? ($('#replay-date').value || presets[0].date) : null;
    if (replay) $('#replay-date').value = state.asOf;
    renderChrome(); loadData();
  };
  $('#mode-live').onclick = () => setMode(false);
  $('#mode-replay').onclick = () => setMode(true);
  $('#reveal').onclick = revealAndValidate;

  // map tools + layers menu
  $('#zoom-in').onclick = () => map.zoomIn();
  $('#zoom-out').onclick = () => map.zoomOut();
  $('#locate-btn').onclick = () => requestUserLocation(() => go([state.userLoc.lat, state.userLoc.lon], 8));
  $('#layers-menu').innerHTML = LAYER_TOGGLES.map(([k, label]) =>
    `<label><input type="checkbox" data-layer="${k}" ${state.show[k] ? 'checked' : ''}> ${label}</label>`).join('');
  $('#layers-menu').addEventListener('change', (e) => setLayerVisible(e.target.dataset.layer, e.target.checked));
  $('#layers-btn').onclick = () => {
    const menu = $('#layers-menu');
    menu.hidden = !menu.hidden;
    $('#layers-btn').setAttribute('aria-expanded', String(!menu.hidden));
  };
  document.addEventListener('pointerdown', (e) => {
    if (!e.target.closest('#layers-menu, #layers-btn')) $('#layers-menu').hidden = true;
  });
}

function renderStatus() {
  const labels = sources().map(([, s]) => freshLabel(s));
  const el = $('#data-status');
  const [cls, text] = !state.risk || labels.every((l) => l === 'UNAVAILABLE') ? ['none', 'DATA UNAVAILABLE']
    : labels.includes('DEMO') ? ['demo', 'DEMO DATA']
      : labels.includes('UNAVAILABLE') ? ['partial', 'PARTIAL DATA']
        : ['live', 'LIVE DATA'];
  el.className = `data-status ${cls}`;
  el.textContent = text;
  el.title = sources().map(([k, s]) => `${k}: ${freshLabel(s)}${s?.source ? ` (${s.source})` : ''}`).join('\n');
}

function renderBanner() {
  const parts = [];
  if (state.asOf) parts.push(`Replay · risk as it stood on <b>${esc(fmtLong(state.asOf))}</b>, using only data available that day`);
  else if (state.risk) {
    const a = state.risk.as_of, b = new Date(Date.parse(`${a}T12:00:00`) + 864e5).toISOString().slice(0, 10);
    parts.push(`Forecast ${esc(fmtDate(a))} → ${esc(fmtDate(b))} · updated ${esc(fmtTime(new Date(state.risk.generated_at)))}`);
  }
  if (sources().some(([, s]) => s?.mode === 'demo')) parts.push('<b>DEMO</b> · some layers use synthetic data');
  const el = $('#banner');
  el.hidden = !parts.length;
  el.innerHTML = parts.join('<br>');
}

function renderLegend() {
  const info = HAZARDS[state.hazard];
  $('#legend').innerHTML = `
    <div class="legend-head"><b>${esc(info.mapLabel)}</b><span>next 48 h</span></div>
    <div class="scale" style="background:${palette.gradient()}"></div>
    <div class="scale-labels"><span>Low</span><span>Moderate</span><span>High</span><span>Extreme</span></div>
    <button class="link-btn" id="palette-btn">Colors: ${esc(palette.label())} ⇄</button>`;
  $('#palette-btn').onclick = () => { palette.toggle(); if (state.risk) applyRisk(); renderLegend(); refreshView(); };
}

function renderFooter() {
  const r = state.risk;
  if (!r) { $('#foot-left').textContent = 'Waiting for data…'; $('#foot-mid').textContent = ''; $('#foot-right').textContent = ''; return; }
  const counts = r.counts_by_hazard?.[state.hazard] || r.counts || {};
  const hot = (counts.Extreme || 0) + (counts['Very high'] || 0) + (counts.High || 0);
  const top = r.top_by_hazard?.[state.hazard]?.[0] || r.top?.[0];
  const topScore = top?.score ?? top?.risk;  // top_by_hazard rows use score; the older top list uses risk
  const fires = state.asOf && !state.firesRevealed ? 'hidden' : state.fires?.count ?? '–';
  $('#foot-left').textContent = `${state.asOf ? 'Fires (72 h)' : 'Fire detections (48 h)'}: ${fires} · High+ areas: ${hot}/${r.cells.length}`;
  $('#foot-mid').textContent = top ? `Highest ${HAZARDS[state.hazard].label}: ${top.name} · ${Number(topScore).toFixed(0)}` : '';
  $('#foot-right').textContent = `Source freshness: ${sources().map(([k, s]) => `${k} ${freshLabel(s)}`).join(' · ')}`;
}

export function renderChrome() {
  $$('[data-hazard]').forEach((b) => {
    const on = b.dataset.hazard === state.hazard;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', String(on));
  });
  $('#replay-wrap').hidden = !state.asOf;
  $('#mode-live').classList.toggle('active', !state.asOf);
  $('#mode-replay').classList.toggle('active', !!state.asOf);
  $('#reveal').hidden = !(state.asOf && !state.firesRevealed && state.risk);
  renderStatus(); renderBanner(); renderLegend(); renderFooter();
}
