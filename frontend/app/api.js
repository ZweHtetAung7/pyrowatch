import { state } from './store.js';

export async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { msg = (await r.json()).detail || msg; } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

/** Appends ?as_of=… (when replaying) plus any extra params. */
export function withAsOf(path, params = {}) {
  const p = new URLSearchParams();
  if (state.asOf) p.set('as_of', state.asOf);
  for (const [k, v] of Object.entries(params)) p.set(k, v);
  const s = p.toString();
  return s ? `${path}?${s}` : path;
}
