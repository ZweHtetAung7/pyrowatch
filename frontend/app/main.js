import { state, on } from './store.js';
import { api } from './api.js';
import { $ } from './util.js';
import { bindMapEvents } from './map.js';
import { loadData } from './data.js';
import { register, setView, refreshView, selectCell, closeCell, toast, announce, done } from './ui.js';
import { initChrome, renderChrome } from './chrome.js';
import { panel } from './panel.js';
import { initSatellites } from './space.js';
import { pollStations } from './stations.js';
import './ai.js';
import './analytics.js';

register('overview', panel);

// Close button inside any sheet: cell panel closes the selection, other views return to Overview.
$('#sheet').addEventListener('click', (e) => {
  if (!e.target.closest('[data-close]')) return;
  if (state.view === 'overview') closeCell(); else setView('overview');
});

on('data', () => { renderChrome(); refreshView(); });
on('hazard', () => { renderChrome(); refreshView(); });
on('revealed', () => { renderChrome(); refreshView(); });
on('sources', renderChrome);
on('stations', () => { if (state.view === 'stations') refreshView(); });

// Arrow keys move between areas; Escape backs out; "/" jumps to the AI view.
const ID_RE = /^r(\d+)c(\d+)$/;
function neighbor(id, dr, dc) {
  const m = ID_RE.exec(id);
  if (!m) return null;
  for (let k = 1; k <= 4; k++) {
    const nid = `r${String(+m[1] + dr * k).padStart(2, '0')}c${String(+m[2] + dc * k).padStart(2, '0')}`;
    if (state.cellMap.has(nid)) return nid;
  }
  return null;
}
const MOVES = { ArrowUp: [1, 0], ArrowDown: [-1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
document.addEventListener('keydown', (e) => {
  const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName);
  if (e.key === 'Escape') {
    if (typing) document.activeElement.blur();
    else if (state.view !== 'overview') setView('overview');
    else closeCell();
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey || state.view !== 'overview' || !state.risk) return;
  if (e.key === '/') { e.preventDefault(); setView('ai'); setTimeout(() => $('#g-q')?.focus(), 0); return; }
  if (MOVES[e.key]) {
    e.preventDefault();
    const next = state.selected ? neighbor(state.selected, ...MOVES[e.key]) : state.risk.top?.[0]?.id;
    if (next) selectCell(next, { focus: true });
  }
});

(async function init() {
  try {
    state.config = await api('/api/config');
  } catch (e) {
    done();
    $('#foot-left').textContent = `Backend not reachable. Start the server, then refresh. (${e.message})`;
    toast(`Backend not reachable: ${e.message}`);
    announce('Backend not reachable. Start the server, then refresh.');
    return;
  }
  initChrome();
  renderChrome();
  bindMapEvents({ onSelect: (id) => selectCell(id) });
  initSatellites();
  pollStations();
  setInterval(pollStations, 20000);
  await loadData();
})();
