export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

export function rafThrottle(fn) {
  let queued = false, last;
  return (...args) => {
    last = args;
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; fn(...last); });
  };
}

export const readPref = (key, valid, fallback) => {
  try { const v = localStorage.getItem(key); return valid(v) ? v : fallback; } catch { return fallback; }
};
export const savePref = (key, value) => { try { localStorage.setItem(key, value); } catch { /* storage blocked */ } };

export const fmtDate = (iso, opts = { weekday: 'short', month: 'short', day: 'numeric' }) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, opts);
export const fmtLong = (iso) => fmtDate(iso, { month: 'short', day: 'numeric', year: 'numeric' });
export const fmtTime = (d) => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
export const fmtUtc = (iso) => new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
export const fmtNum = (v, digits = 0, unit = '') => (v == null || Number.isNaN(v) ? '—' : `${Number(v).toFixed(digits)}${unit}`);

export const ago = (iso) => {
  const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
  return m < 1 ? 'just now' : m < 90 ? `${m} min ago` : `${(m / 60).toFixed(1)} h ago`;
};
export const fmtReading = (r) => [
  r.temp_c != null ? `${r.temp_c.toFixed(1)} °C` : null,
  r.rh_pct != null ? `${r.rh_pct.toFixed(0)}% RH` : null,
  r.smoke_raw != null ? `smoke ${Math.round(r.smoke_raw)}` : null,
].filter(Boolean).join(' · ') || 'no sensor data';

export function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371, r = Math.PI / 180;
  const x = Math.sin(((lat2 - lat1) * r) / 2) ** 2
    + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lon2 - lon1) * r) / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
