/* Leaflet setup and every map layer.
   The risk grid is a single canvas that draws only the visible cells and hit-tests in O(1),
   so it stays fast as the grid grows to the whole US. */
import { state } from './store.js';
import { riskColor } from './palette.js';
import { esc, fmtUtc, fmtReading, ago, rafThrottle, reduceMotion } from './util.js';
import { HAZARDS } from '../hazards.js';

const ID_RE = /^r(\d+)c(\d+)$/;
const key = (r, c) => r * 10000 + c;
const TILES = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_';

export const map = L.map('map', { zoomControl: false, minZoom: 3, keyboard: false, preferCanvas: true })
  .setView([39.5, -98.35], 4);

for (const [name, z, interactive] of [['risk', 450, false], ['labels', 610, false], ['validation', 615, true], ['fires', 620, true], ['markers', 630, true]]) {
  const pane = map.createPane(name);
  pane.style.zIndex = z;
  if (!interactive) pane.style.pointerEvents = 'none';
}
L.tileLayer(`${TILES}Base/MapServer/tile/{z}/{y}/{x}`, {
  maxZoom: 16,
  attribution: 'Tiles &copy; Esri · Fire data: NASA FIRMS · Weather: Open-Meteo · Orbits: CelesTrak',
}).addTo(map);
L.tileLayer(`${TILES}Reference/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 16, pane: 'labels' }).addTo(map);

// ------------------------------------------------------------------ risk grid
const RiskLayer = L.Layer.extend({
  initialize() {
    this._items = new Map(); this._byId = new Map();
    this._grid = null; this._hover = null; this._sel = null;
    this._rows = [0, -1]; this._cols = [0, -1];
  },
  getEvents() { return { moveend: this._reset, zoomend: this._reset, resize: this._reset, zoomstart: this._hide }; },
  onAdd(m) {
    this._canvas = L.DomUtil.create('canvas', 'risk-canvas');
    m.getPane('risk').appendChild(this._canvas);
    this._reset();
  },
  onRemove() { L.DomUtil.remove(this._canvas); },
  _hide() { this._canvas.style.opacity = 0; },
  _reset() {
    const m = this._map, s = m.getSize(), c = this._canvas, d = window.devicePixelRatio || 1;
    this._w = s.x * 2; this._h = s.y * 2; this._dpr = d;   // canvas covers the viewport plus a half-screen margin
    this._origin = m.containerPointToLayerPoint([-s.x / 2, -s.y / 2]);
    if (c.width !== this._w * d || c.height !== this._h * d) {
      c.width = this._w * d; c.height = this._h * d;
      c.style.width = `${this._w}px`; c.style.height = `${this._h}px`;
    }
    L.DomUtil.setPosition(c, this._origin);
    c.style.opacity = 1;
    this.draw();
  },
  setData(grid, cells, hazardOf) {
    this._grid = grid; this._items.clear(); this._byId.clear();
    let r0 = Infinity, r1 = -Infinity, c0 = Infinity, c1 = -Infinity;
    for (const cell of cells) {
      const m = ID_RE.exec(cell.id);
      if (!m) continue;
      const r = +m[1], c = +m[2], { score, category } = hazardOf(cell);
      const it = {
        id: cell.id, r, c, score, category,
        fill: score == null ? '#424b55' : riskColor(score),
        alpha: score == null ? 0.45 : 0.5 + 0.35 * (score / 100),
      };
      this._items.set(key(r, c), it); this._byId.set(cell.id, it);
      r0 = Math.min(r0, r); r1 = Math.max(r1, r); c0 = Math.min(c0, c); c1 = Math.max(c1, c);
    }
    this._rows = [r0, r1]; this._cols = [c0, c1];
    this.draw();
  },
  setSelected(id) { this._sel = id; this.draw(); },
  setHover(id) { if (id !== this._hover) { this._hover = id; this.draw(); } },
  at(latlng) {
    const g = this._grid;
    if (!g) return null;
    return this._items.get(key(Math.floor((latlng.lat - g.south) / g.step), Math.floor((latlng.lng - g.west) / g.step))) || null;
  },
  _rect(it) {
    const g = this._grid, m = this._map, o = this._origin;
    const sw = m.latLngToLayerPoint([g.south + it.r * g.step, g.west + it.c * g.step]).subtract(o);
    const ne = m.latLngToLayerPoint([g.south + (it.r + 1) * g.step, g.west + (it.c + 1) * g.step]).subtract(o);
    return [sw.x, ne.y, ne.x - sw.x, sw.y - ne.y];
  },
  draw() {
    const m = this._map, g = this._grid;
    if (!m || !this._canvas || !this._origin) return;
    const ctx = this._canvas.getContext('2d'), o = this._origin, d = this._dpr;
    ctx.setTransform(d, 0, 0, d, 0, 0);
    ctx.clearRect(0, 0, this._w, this._h);
    if (!g || this._rows[1] < this._rows[0]) return;

    const b = L.latLngBounds(m.layerPointToLatLng(o), m.layerPointToLatLng(o.add([this._w, this._h])));
    const rA = Math.max(this._rows[0], Math.floor((b.getSouth() - g.south) / g.step));
    const rB = Math.min(this._rows[1], Math.floor((b.getNorth() - g.south) / g.step));
    const cA = Math.max(this._cols[0], Math.floor((b.getWest() - g.west) / g.step));
    const cB = Math.min(this._cols[1], Math.floor((b.getEast() - g.west) / g.step));
    if (rB < rA || cB < cA) return;

    // Web Mercator x depends only on longitude and y only on latitude, so project grid lines once.
    const xs = [], ys = [];
    for (let c = cA; c <= cB + 1; c++) xs.push(m.latLngToLayerPoint([g.south, g.west + c * g.step]).x - o.x);
    for (let r = rA; r <= rB + 1; r++) ys.push(m.latLngToLayerPoint([g.south + r * g.step, g.west]).y - o.y);

    for (let r = rA; r <= rB; r++) {
      for (let c = cA; c <= cB; c++) {
        const it = this._items.get(key(r, c));
        if (!it) continue;
        const x = xs[c - cA], y = ys[r - rA + 1];
        ctx.globalAlpha = it.alpha;
        ctx.fillStyle = it.fill;
        ctx.fillRect(x + 0.25, y + 0.25, xs[c - cA + 1] - x - 0.5, ys[r - rA] - y - 0.5);
      }
    }
    ctx.globalAlpha = 1;
    for (const [id, color, width] of [[this._hover, 'rgba(255,255,255,.6)', 1.5], [this._sel, '#fff', 2.5]]) {
      const it = id && this._byId.get(id);
      if (!it) continue;
      const [x, y, w, h] = this._rect(it);
      ctx.strokeStyle = color; ctx.lineWidth = width;
      ctx.strokeRect(x, y, w, h);
    }
  },
});
export const riskLayer = new RiskLayer().addTo(map);

// ------------------------------------------------------------------ overlays
export const layers = {
  fires: L.layerGroup().addTo(map),
  stations: L.layerGroup().addTo(map),
  validation: L.layerGroup().addTo(map),
  sats: L.layerGroup(),
};

export function setLayerVisible(name, on) {
  state.show[name] = on;
  const layer = name === 'risk' ? riskLayer : layers[name];
  if (on) layer.addTo(map); else map.removeLayer(layer);
}

export const go = (latlng, zoom) => (reduceMotion ? map.setView(latlng, zoom) : map.flyTo(latlng, zoom, { duration: 0.8 }));
export function flyToCell(c) {
  if (map.getZoom() < 6) go([c.lat, c.lon], 6);
  else if (!map.getBounds().pad(-0.15).contains([c.lat, c.lon])) go([c.lat, c.lon], map.getZoom());
}
export function fitToCells(cells) {
  if (cells.length) map.fitBounds(L.latLngBounds(cells.map((c) => [c.lat, c.lon])).pad(0.1));
}

// ---- fires
const popup = L.popup({ className: 'tip-popup' });
function firePopup(d) {
  const sat = state.sats.find((s) => s.name === d.satellite);
  const alt = sat?.alt ? `${Math.round(sat.alt)} km` : d.instrument === 'MODIS' ? '~705 km' : '~830 km';
  return `<b>Active fire detection</b><br>Seen by <b>${esc(d.satellite)}</b> (${esc(d.instrument)}) from ${alt} up<br>
    ${esc(fmtUtc(d.time_utc))} · ${d.daynight === 'N' ? 'night pass' : 'day pass'}<br>
    Fire radiative power: <b>${d.frp.toFixed(1)} MW</b> · confidence ${esc(d.confidence)}<br>
    <span class="muted small">${d.lat.toFixed(3)}, ${d.lon.toFixed(3)} · ${esc(d.source)}</span>`;
}
function fireMarker(d, latest) {
  const recent = (latest - Date.parse(d.time_utc)) / 3.6e6 < 12;
  return L.circleMarker([d.lat, d.lon], {
    pane: 'fires', radius: 2.5 + Math.min(6, Math.log1p(d.frp)), weight: 1.2, color: '#fff', opacity: 0.9,
    fillColor: recent ? '#ff1f1f' : '#ff6a1f', fillOpacity: 0.95, bubblingMouseEvents: false,
  }).on('click', () => popup.setLatLng([d.lat, d.lon]).setContent(firePopup(d)).openOn(map));
}
let fireJob = 0;
export function drawFires(detections, animate = false) {
  layers.fires.clearLayers();
  const job = ++fireJob;
  const sorted = [...detections].sort((a, b) => a.time_utc.localeCompare(b.time_utc));
  if (!sorted.length) return;
  const latest = Date.parse(sorted.at(-1).time_utc);
  const per = animate && !reduceMotion ? Math.ceil(sorted.length / 60) : sorted.length;
  let i = 0;
  (function step() {
    if (job !== fireJob) return;
    for (const end = Math.min(sorted.length, i + per); i < end; i++) layers.fires.addLayer(fireMarker(sorted[i], latest));
    if (i < sorted.length) requestAnimationFrame(step);
  }());
}

// ---- ground stations
const stationIcon = (label) => L.divIcon({
  className: '', iconSize: [140, 22], iconAnchor: [10, 11],
  html: `<div class="station-icon"><svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 13v9M8 22h8" stroke="#5eead4" stroke-width="2" fill="none"/><circle cx="12" cy="11" r="2.4" fill="#5eead4"/><path d="M7.5 6.5a6.4 6.4 0 0 0 0 9M16.5 6.5a6.4 6.4 0 0 1 0 9" stroke="#5eead4" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>${esc(label)}</div>`,
});
export function drawStations(stations) {
  layers.stations.clearLayers();
  for (const s of stations) {
    L.marker([s.lat, s.lon], { pane: 'markers', icon: stationIcon(s.label), keyboard: false })
      .bindPopup(`<b>Ground station · ${esc(s.label)}</b><br>${esc(fmtReading(s.latest))}<br><span class="muted small">${esc(s.place)} · ${esc(ago(s.latest.time_utc))}</span>`)
      .addTo(layers.stations);
  }
}

// ---- hover tooltip + click-to-select
export function bindMapEvents({ onSelect }) {
  const tip = L.tooltip({ className: 'tip', direction: 'top', offset: [0, -6] });
  let hover = null;
  const clear = () => { if (hover) { hover = null; riskLayer.setHover(null); tip.remove(); } map.getContainer().style.cursor = ''; };
  const move = rafThrottle((e) => {
    const it = map.hasLayer(riskLayer) ? riskLayer.at(e.latlng) : null;
    if (!it) { clear(); return; }
    map.getContainer().style.cursor = 'pointer';
    if (hover !== it.id) {
      hover = it.id; riskLayer.setHover(it.id);
      const name = state.cellMap.get(it.id)?.name ?? it.id;
      tip.setContent(`<b>${esc(name)}</b><br>${esc(HAZARDS[state.hazard].label)} ${it.score == null ? 'unavailable' : `${it.score.toFixed(0)} · ${esc(it.category || '')}`}`);
    }
    tip.setLatLng(e.latlng);
    if (!map.hasLayer(tip)) tip.addTo(map);
  });
  map.on('mousemove', move);
  map.on('mouseout', clear);
  map.on('click', (e) => { const it = map.hasLayer(riskLayer) ? riskLayer.at(e.latlng) : null; if (it) onSelect(it.id); });
}
