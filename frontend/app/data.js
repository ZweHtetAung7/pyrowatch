/* Loading the risk grid + fire detections and pushing them onto the map. */
import { state, emit, activeHazard } from './store.js';
import { api, withAsOf } from './api.js';
import { riskLayer, layers, drawFires, fitToCells } from './map.js';
import { loading, done, toast } from './ui.js';
import { fmtLong, savePref } from './util.js';
import { HAZARDS } from '../hazards.js';

let fitted = false;

export const applyRisk = () => riskLayer.setData(state.config.grid, state.risk.cells, activeHazard);
export const applyFires = (animate = false) =>
  drawFires(state.firesRevealed ? state.fires?.detections ?? [] : [], animate);

export async function loadData() {
  loading(state.asOf ? `Rebuilding the risk map as it looked on ${fmtLong(state.asOf)}…` : 'Loading weather forecast and fire detections…');
  try {
    const [risk, fires] = await Promise.all([
      api(withAsOf('/api/risk')),
      api(withAsOf('/api/fires', { days: state.asOf ? 3 : 2 })),
    ]);
    Object.assign(state, { risk, fires, validation: null, firesRevealed: !state.asOf });
    state.cellMap = new Map(risk.cells.map((c) => [c.id, c]));
    if (state.selected && !state.cellMap.has(state.selected)) state.selected = null;
    layers.validation.clearLayers();
    applyRisk(); applyFires();
    if (!fitted) { fitToCells(risk.cells); fitted = true; }
    emit('data');
  } catch (e) {
    toast(`Could not load data: ${e.message}`);
    emit('data');
  } finally {
    done();
  }
}

export function setHazard(h) {
  if (!HAZARDS[h]) return;
  state.hazard = h;
  savePref('pw-hazard', h);
  if (state.risk) applyRisk();
  emit('hazard');
}

// ---- data source status
export const sources = () => [
  ['Weather', state.risk?.sources?.weather],
  ['Fires', state.fires?.source],
  ['Terrain', state.risk?.sources?.terrain],
  ['History', state.risk?.sources?.history],
  ['Vegetation', state.risk?.sources?.vegetation],
  ['Orbits', state.satSource],
];
export function freshLabel(s) {
  if (!s) return 'UNAVAILABLE';
  if (s.mode === 'demo') return 'DEMO';
  if (s.mode === 'off') return 'UNAVAILABLE';
  if (s.mode === 'live') return /cach/i.test(s.note || '') ? 'CACHED' : 'LIVE';
  return String(s.mode || 'UNAVAILABLE').toUpperCase();
}
