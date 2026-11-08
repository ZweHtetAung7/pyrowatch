/* Shared UI plumbing: toast/loading/announce, the view router and cell selection. */
import { state, cellById } from './store.js';
import { $, $$ } from './util.js';
import { riskLayer, flyToCell } from './map.js';

const TITLES = {
  overview: ['Overview', 'Hazard map'],
  space: ['Space Intelligence', 'Fire-watch satellites'],
  analytics: ['Analytics & Validation', 'Prediction vs. observation'],
  stations: ['Ground Stations', 'PyroWatch Beacons'],
  ai: ['AI Intelligence', 'Tool-grounded assistant'],
};

export function toast(msg, ms = 6000) {
  const t = $('#toast');
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, ms);
}
export const loading = (text) => { $('#loading-text').textContent = text; $('#loading').hidden = false; };
export const done = () => { $('#loading').hidden = true; };
export function announce(text) {
  const el = $('#sr-status');
  el.textContent = '';
  setTimeout(() => { el.textContent = text; }, 50);
}
export const sheetHead = (title, eyebrow = '') => `
  <header class="sheet-head">
    <div>${eyebrow ? `<div class="eyebrow">${eyebrow}</div>` : ''}<h2 tabindex="-1">${title}</h2></div>
    <button class="icon-btn" data-close aria-label="Close">×</button>
  </header>`;

// ---- router: each view registers { render(root, opts), leave?() }
const views = {};
export const register = (name, mod) => { views[name] = mod; };

export function setView(name, opts = {}) {
  if (!views[name]) name = 'overview';
  if (state.view !== name) views[state.view]?.leave?.();
  state.view = name;
  $$('.nav button').forEach((b) => {
    const on = b.dataset.view === name;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', String(on));
  });
  [$('#view-title').textContent, $('#view-sub').textContent] = TITLES[name];
  const sheet = $('#sheet');
  if (name === 'overview' && !state.selected) {
    sheet.hidden = true; sheet.innerHTML = '';
  } else {
    sheet.hidden = false;
    views[name].render(sheet, opts);
  }
}
// Re-render the current view after data changes (analytics owns its own render cycle).
export const refreshView = () => { if (state.view !== 'analytics') setView(state.view, { force: true }); };

// ---- selection
export function selectCell(id, { fly = true, focus = false } = {}) {
  const c = cellById(id);
  if (!c) return;
  state.selected = id;
  riskLayer.setSelected(id);
  if (fly) flyToCell(c);
  setView('overview', { force: true, announce: true });
  if (focus) $('#sheet h2')?.focus({ preventScroll: true });
}
export function closeCell() {
  state.selected = null;
  riskLayer.setSelected(null);
  if (state.view === 'overview') setView('overview');
}
