/* Ground Stations (PyroWatch Beacons): polling + table view. */
import { state, emit } from './store.js';
import { api } from './api.js';
import { esc, ago, fmtReading } from './util.js';
import { drawStations } from './map.js';
import { sheetHead, register } from './ui.js';

export async function pollStations() {
  try {
    state.stations = (await api('/api/stations')).stations;
    drawStations(state.stations);
    emit('stations');
  } catch { /* stations are optional */ }
}

register('stations', {
  render(root) {
    const rows = state.stations;
    root.innerHTML = `${sheetHead('Ground Stations', 'PyroWatch Beacons')}
      <div class="sheet-scroll">${rows.length ? `
        <div class="table-wrap"><table><thead><tr><th>Station</th><th>Place</th><th>Latest</th><th>Age</th></tr></thead><tbody>
          ${rows.map((s) => `<tr><td><b>${esc(s.label)}</b><div class="muted small">${esc(s.station_id || s.id || '')}</div></td>
            <td>${esc(s.place || '—')}<div class="muted small">${esc(s.cell_id || '')} · ${Number(s.lat).toFixed(3)}, ${Number(s.lon).toFixed(3)}</div></td>
            <td>${esc(fmtReading(s.latest))}</td><td>${esc(ago(s.latest.time_utc))}</td></tr>`).join('')}
        </tbody></table></div>
        <p class="note">${rows.length} station${rows.length === 1 ? '' : 's'} in memory. Readings are process-local and reset if the server restarts.</p>`
    : '<p class="muted">No ground stations are reporting yet. Beacons POST sensor readings to <code>/api/stations/{id}</code> and appear here within the poll window.</p>'}
      </div>`;
  },
});
