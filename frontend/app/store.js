import { HAZARDS, hazardFor } from '../hazards.js';
import { readPref } from './util.js';

export const state = {
  config: null,
  asOf: null,            // replay date (YYYY-MM-DD) or null for live
  risk: null,
  fires: null,
  firesRevealed: true,   // in replay, fires stay hidden until "Reveal what happened"
  validation: null,
  cellMap: new Map(),
  selected: null,
  view: 'overview',
  tab: 'summary',
  hazard: readPref('pw-hazard', (v) => v in HAZARDS, 'wildfire'),
  userLoc: null,         // only set after the user grants geolocation
  stations: [],
  sats: [],
  satSource: null,
  satError: null,
  show: { risk: true, fires: true, stations: true, sats: false },
};

export const cellById = (id) => state.cellMap.get(id);
export const activeHazard = (cell) => hazardFor(cell, state.hazard);

// Tiny pub/sub so modules don't need to import each other to react to changes.
const bus = new EventTarget();
export const emit = (type, detail) => bus.dispatchEvent(new CustomEvent(type, { detail }));
export const on = (type, fn) => bus.addEventListener(type, (e) => fn(e.detail));
