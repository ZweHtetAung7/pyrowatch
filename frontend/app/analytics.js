/* Analytics & Validation: compares a replayed prediction with what satellites saw next. */
import { state, emit } from './store.js';
import { api, withAsOf } from './api.js';
import { $, $$, esc, fmtUtc, reduceMotion } from './util.js';
import { layers } from './map.js';
import { applyFires } from './data.js';
import { loading, done, toast, selectCell, setView, sheetHead, register } from './ui.js';

export function revealFires() {
  if (state.firesRevealed) return;
  state.firesRevealed = true;
  applyFires(true);
  emit('revealed');
}
export function revealAndValidate() {
  revealFires();
  setTimeout(runValidation, reduceMotion ? 0 : 2600);
}

export async function runValidation() {
  if (state.view !== 'analytics') setView('analytics');
  loading('Checking the prediction against what the satellites actually saw…');
  try {
    state.validation = await api(withAsOf('/api/validate'));
    drawRings(state.validation);
    revealFires();
    setView('analytics', { force: true });
  } catch (e) { toast(`Validation failed: ${e.message}`); } finally { done(); }
}

function drawRings(v) {
  layers.validation.clearLayers();
  for (const c of v.cells.filter((x) => x.significant)) {
    L.circleMarker([c.lat, c.lon], {
      pane: 'validation', radius: 17, fill: false, weight: 2.5, color: c.in_top ? '#7cc4ff' : '#9fb0c3',
      dashArray: c.in_top ? null : '4 4', bubblingMouseEvents: false,
    }).bindTooltip(`<b>New fire</b> · ${esc(c.name)}<br>We ranked it #${c.rank} (risk ${c.risk.toFixed(0)}) before detection`, { className: 'tip' })
      .on('click', () => selectCell(c.id)).addTo(layers.validation);
  }
}

function resultsHtml(v) {
  const sig = v.cells.filter((c) => c.significant);
  const pc = (x) => (x == null ? '–' : `${Math.round(x * 100)}%`);
  const topPct = Math.round(v.top_fraction * 100);
  const a = v.all_hotspots || {}, f = v.baseline_ffwi?.significant;
  return `<div class="eyebrow" style="margin-bottom:8px">Prediction ${esc(v.as_of)} · fires ${esc(v.fire_window)}</div>
    ${v.new_fire_cells === 0
    ? `<p class="v-claim">No significant new fires were detected in this window${v.already_burning_cells ? ` (${v.already_burning_cells} area(s) were already burning beforehand)` : ''}. Try another replay date.</p>`
    : `<p class="v-claim"><b>${v.hits} of ${v.new_fire_cells}</b> significant new fires (${pc(v.hit_rate)}) started in areas already in our <b>top ${topPct}%</b> before any satellite saw them.</p>
       <div class="v-hero">
         <div><div class="k">Hit rate</div><div class="v">${pc(v.hit_rate)}</div></div>
         <div><div class="k">vs. random</div><div class="v">${v.lift}×</div></div>
         <div><div class="k">ROC AUC</div><div class="v">${v.auc ?? '–'}</div></div>
       </div>
       <p class="note">Random guessing puts ${topPct}% of fires in the top ${topPct}% (1.0×). AUC 0.5 = random, 1.0 = perfect ranking.</p>`}
    ${f?.new_fire_cells ? `<p class="small">Fosberg-only baseline: ${f.hits} of ${f.new_fire_cells} (${pc(f.hit_rate)}), ${f.lift}× vs random, AUC ${f.auc ?? '–'}.</p>` : ''}
    ${a.new_fire_cells ? `<p class="small">All hotspots: ${a.hits} of ${a.new_fire_cells} (${pc(a.hit_rate)}), ${a.lift}× vs random, AUC ${a.auc ?? '–'}.</p>` : ''}
    <p class="small"><b>Method:</b> ${esc(v.method)}. "Significant" = ${esc(v.significant_rule)}; ${esc(v.new_rule || '')}.</p>
    ${sig.length ? `<section style="margin-top:16px"><h3>Significant new fires</h3><ul class="v-list">${sig.map((c) => `
      <li data-id="${esc(c.id)}"><span class="rk ${c.in_top ? 'hit' : ''}">#${c.rank}</span>
        <span><b>${esc(c.name)}</b><div class="muted small">first seen ${esc(fmtUtc(c.first_detection_utc))} by ${esc(c.first_satellite)} · ${c.detections} detections · peak ${c.max_frp} MW</div></span>
        <b>${c.risk.toFixed(0)}</b></li>`).join('')}</ul></section>` : ''}
    ${v.cells.length > sig.length ? `<p class="note">${v.cells.length - sig.length} smaller hotspot area(s) not listed.</p>` : ''}
    <ul class="caveats note">${v.caveats.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
}

register('analytics', {
  render(root) {
    root.innerHTML = `${sheetHead('Analytics & Validation', 'Prediction vs. observation')}
      <div class="sheet-scroll">
        <div class="row"><button class="btn" id="run-validation">${state.validation ? 'Re-run validation' : 'Run validation'}</button>
          <span class="muted small">${state.asOf ? `Replay date: ${esc(state.asOf)}` : 'Tip: switch to Replay (top right) to pick a date.'}</span></div>
        ${state.validation ? resultsHtml(state.validation) : '<p class="muted">Run validation to compare the risk ranking with the fires satellites detected afterwards.</p>'}
      </div>`;
    $('#run-validation').onclick = runValidation;
    $$('.v-list li', root).forEach((li) => li.addEventListener('click', () => selectCell(li.dataset.id)));
  },
});
