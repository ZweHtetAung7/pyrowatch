/* Space Intelligence: fire-watch satellite orbits (SGP4 in the browser). Timers run only while this view is open. */
import { state, cellById, emit } from './store.js';
import { api } from './api.js';
import { $, $$, esc, fmtTime, haversineKm } from './util.js';
import { layers, setLayerVisible, go } from './map.js';
import { sheetHead, register } from './ui.js';

const COLOR = '#6cb4ff';
const overCA = (p) => p.lat > 32.4 && p.lat < 42.1 && p.lon > -124.6 && p.lon < -114;

function subpoint(satrec, t) {
  const pv = satellite.propagate(satrec, t);
  if (!pv?.position) return null;
  const g = satellite.eciToGeodetic(pv.position, satellite.gstime(t));
  return { lat: satellite.degreesLat(g.latitude), lon: satellite.degreesLong(g.longitude), alt: g.height };
}
const satIcon = (name) => L.divIcon({
  className: '', iconSize: [120, 20], iconAnchor: [9, 10],
  html: `<div class="sat-icon"><svg width="18" height="18" viewBox="0 0 24 24"><g fill="${COLOR}"><rect x="9" y="9" width="6" height="6" rx="1"/><rect x="1" y="10" width="7" height="4" opacity=".75"/><rect x="16" y="10" width="7" height="4" opacity=".75"/></g></svg>${esc(name)}</div>`,
});

export async function initSatellites() {
  try {
    const s = await api('/api/satellites');
    state.satSource = s.source;
    state.sats = s.satellites.map((x) => {
      const marker = L.marker([0, 0], { pane: 'markers', icon: satIcon(x.name), keyboard: false })
        .bindTooltip(`${esc(x.name)} · ${esc(x.instrument)} · ${x.swath_km} km swath`, { className: 'tip' });
      const footprint = L.circle([0, 0], { pane: 'markers', radius: (x.swath_km / 2) * 1000, color: COLOR, weight: 1, opacity: 0.4, fillOpacity: 0.05, interactive: false });
      const track = L.polyline([], { pane: 'markers', color: COLOR, weight: 1.2, opacity: 0.5, dashArray: '3 6', interactive: false });
      [track, footprint, marker].forEach((l) => l.addTo(layers.sats));
      return { ...x, satrec: satellite.twoline2satrec(x.tle1, x.tle2), marker, footprint, track };
    });
  } catch (e) { state.satError = e.message; }
  emit('sources');
}

function updateTracks() {
  const now = Date.now();
  for (const s of state.sats) {
    const segs = [[]];
    let prev = null;
    for (let m = -50; m <= 50; m++) {
      const p = subpoint(s.satrec, new Date(now + m * 60000));
      if (!p) continue;
      if (prev && Math.abs(p.lon - prev.lon) > 180) segs.push([]); // antimeridian wrap
      segs.at(-1).push([p.lat, p.lon]);
      prev = p;
    }
    s.track.setLatLngs(segs);
  }
}
function tick() {
  const now = new Date();
  for (const s of state.sats) {
    const p = subpoint(s.satrec, now);
    if (!p) continue;
    s.pos = p; s.alt = p.alt;
    s.marker.setLatLng([p.lat, p.lon]);
    s.footprint.setLatLng([p.lat, p.lon]);
    const el = document.querySelector(`[data-pos="${CSS.escape(s.name)}"]`);
    if (el) el.textContent = `${overCA(p) ? 'over California · ' : ''}${p.lat.toFixed(2)}°, ${p.lon.toFixed(2)}° · ${Math.round(p.alt)} km`;
  }
}

function nextPasses(c, hours = 12) {
  const out = [], now = Date.now();
  for (const s of state.sats) {
    const reach = (s.swath_km / 2) * 0.7; // keep to the better-resolved middle of the swath
    let best = null;
    for (let m = 0; m <= hours * 60; m++) {
      const p = subpoint(s.satrec, new Date(now + m * 60000));
      if (!p) continue;
      const d = haversineKm(c.lat, c.lon, p.lat, p.lon);
      if (d <= reach && (!best || d < best.d)) best = { d, t: now + m * 60000 };
      else if (best && d > reach) break;
    }
    if (best) out.push({ sat: s, ...best });
  }
  return out.sort((a, b) => a.t - b.t);
}
function passesHtml(c) {
  const passes = nextPasses(c);
  if (!passes.length) return '<div class="muted small">No passes in the next 12 hours.</div>';
  return passes.map((p) => {
    const mins = Math.round((p.t - Date.now()) / 60000);
    const when = mins < 1 ? 'overhead now' : mins < 90 ? `in ${mins} min` : `in ${(mins / 60).toFixed(1)} h`;
    return `<li><span><b>${esc(p.sat.name)}</b> · ${esc(p.sat.instrument)}<div class="meta">closest ${Math.round(p.d)} km off-track</div></span><span class="dist">${when}<br>${fmtTime(new Date(p.t))}</span></li>`;
  }).join('');
}

let timers = [];
const stop = () => { timers.forEach(clearInterval); timers = []; };

const space = {
  render(root) {
    stop();
    const orbit = state.satSource, demo = orbit?.mode === 'demo';
    const sel = state.selected ? cellById(state.selected) : null;
    root.innerHTML = `${sheetHead('Space Intelligence', 'Orbits · fire-watch satellites')}
      <div class="sheet-scroll">
        <div class="card">
          <b>Orbits</b> <span class="pill ${esc(orbit?.mode || 'off')}">${demo ? 'synthetic' : esc(orbit?.mode || 'off')}</span><br>
          <span class="muted small">${esc(orbit?.source || 'CelesTrak TLE')} · TLE refresh ~4 h${orbit?.note ? ` · ${esc(orbit.note)}` : ''}</span>
          <p class="note">Tracks show detection platforms (VIIRS/MODIS). They do <b>not</b> feed the hazard index; fire detections on the map come from NASA FIRMS.</p>
        </div>
        ${state.satError ? `<p class="muted">Satellites unavailable: ${esc(state.satError)}</p>` : ''}
        <section style="margin-top:16px">
          <div class="row" style="justify-content:space-between"><h3 style="margin:0">Satellites</h3>
            <button class="btn-ghost" id="toggle-tracks">${state.show.sats ? 'Hide tracks' : 'Show tracks'}</button></div>
          <div class="table-wrap"><table><thead><tr><th>Satellite</th><th>Instrument</th><th>Position</th></tr></thead><tbody>
            ${state.sats.map((s) => `<tr class="click" data-name="${esc(s.name)}"><td><b>${esc(s.name)}</b><div class="muted small">NORAD ${esc(s.norad || s.norad_id || '—')}</div></td>
              <td>${esc(s.instrument)}<div class="muted small">${s.swath_km} km swath</div></td>
              <td class="muted small" data-pos="${esc(s.name)}">propagating…</td></tr>`).join('') || '<tr><td colspan="3" class="muted">Orbit catalog unavailable.</td></tr>'}
          </tbody></table></div>
        </section>
        <section>
          <h3>${sel ? `Estimated passes over ${esc(sel.name)}` : 'Overhead passes'}</h3>
          ${sel ? `<ul class="near-list">${passesHtml(sel)}</ul><p class="note">Browser SGP4 from ${demo ? '<b>synthetic</b> TLEs' : 'CelesTrak TLEs'}. Not risk inputs.</p>`
    : '<p class="muted small">Select an area on the Overview map to estimate overhead passes for it.</p>'}
        </section>
      </div>`;
    $('#toggle-tracks').onclick = (e) => { setLayerVisible('sats', !state.show.sats); e.target.textContent = state.show.sats ? 'Hide tracks' : 'Show tracks'; };
    $$('tbody tr[data-name]', root).forEach((tr) => tr.addEventListener('click', () => {
      const s = state.sats.find((x) => x.name === tr.dataset.name);
      if (s?.pos) go([s.pos.lat, s.pos.lon], 4);
    }));
    setLayerVisible('sats', true);
    updateTracks(); tick();
    timers = [setInterval(tick, 1000), setInterval(updateTracks, 60000)];
  },
  leave() { stop(); setLayerVisible('sats', false); },
};
register('space', space);
