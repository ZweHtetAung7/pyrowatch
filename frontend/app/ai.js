/* Assistant plumbing (shared by the cell panel and the AI Intelligence view) + the AI view. */
import { state, cellById } from './store.js';
import { api } from './api.js';
import { $, $$, esc } from './util.js';
import { toast, announce, selectCell, sheetHead, register } from './ui.js';

export const PROMPTS = [
  'Is this area safe right now?',
  'A fire is across the road from me. What should I do?',
  'Should I evacuate?',
  'Which direction should I move?',
  'What should I do if the fire gets closer?',
];
export const answers = new Map(); // cell id (or 'global') -> last answer

function composeQuestion(q, cell) {
  let prefix = '';
  if (state.userLoc) {
    prefix = `[User shared location ${state.userLoc.lat.toFixed(4)}, ${state.userLoc.lon.toFixed(4)}. Use it only for proximity relative to tool-verified detections; otherwise say permission was granted but you still need fires_near results.]\n`;
  } else if (/near me|my location|how far|from me|proximity/i.test(q)) {
    prefix = '[User asked about personal proximity but has not shared geolocation. Ask them to share location or answer only for the selected map area.]\n';
  }
  if (cell) prefix += `[Answer for selected area ${cell.id} (${cell.name}). Prefer fires_near + find_area tools.]\n`;
  return `${prefix}${q}`.slice(0, 600);
}

const askApi = (question, cellId) => api('/api/ask', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ question: composeQuestion(question, cellId ? cellById(cellId) : null), cell_id: cellId, as_of: state.asOf }),
});

export function renderAnswer(el, a) {
  const trace = a.steps?.length
    ? `<ol class="trace" aria-label="Tools the agent called">${a.steps.map((s) => `<li class="${s.ok ? '' : 'bad'}">${esc(s.tool)}<span>(${esc(s.args)})</span></li>`).join('')}</ol>` : '';
  const links = (a.areas || []).filter((id) => id !== state.selected && cellById(id)).slice(0, 4);
  el.hidden = false;
  el.innerHTML = `${trace}<div class="answer-text"></div>
    ${links.length ? `<div class="row">${links.map((id) => `<button class="btn-ghost" data-area="${esc(id)}">${esc(cellById(id).name)} →</button>`).join('')}</div>` : ''}
    <div class="engine">${esc(a.engine)}${a.steps?.length ? ` · ${a.steps.length} tool call${a.steps.length === 1 ? '' : 's'}` : ''}</div>`;
  $('.answer-text', el).textContent = a.text;
  $$('[data-area]', el).forEach((b) => b.addEventListener('click', () => selectCell(b.dataset.area, { focus: true })));
}

export async function ask(el, question, cellId = null) {
  el.hidden = false;
  el.innerHTML = '<div class="thinking">Assistant is querying PyroWatch tools…</div>';
  try {
    const a = await askApi(question, cellId);
    answers.set(cellId ?? 'global', a);
    if (!el.isConnected) return;
    renderAnswer(el, a);
    announce(a.text);
  } catch (e) {
    if (el.isConnected) el.textContent = `Error: ${e.message}`;
  }
}

export function requestUserLocation(onDone) {
  if (!navigator.geolocation) { toast('Geolocation is not available in this browser.'); return; }
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      state.userLoc = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      toast('Location shared for this session. Ask again for proximity-aware guidance.');
      onDone?.();
    },
    () => toast('Location permission denied. Proximity answers will use the selected map area only.'),
    { enableHighAccuracy: false, timeout: 10000 },
  );
}

// ---- AI Intelligence view (statewide / unscoped questions)
const aiView = {
  render(root) {
    root.innerHTML = `${sheetHead('AI Intelligence', `Ask PyroWatch · ${state.config?.agent === 'gemini' ? 'Gemini agent' : 'offline router'}`)}
      <div class="sheet-scroll">
        <div class="ai-safety">Answers come from PyroWatch's own backend tools (risk, nearby fires, stations) and never invent distances or alerts. A low score is not "safe." For immediate danger call <b>911</b>.</div>
        <form class="ask-form" id="g-form"><input id="g-q" type="text" placeholder="Ask about any area or hazard…" autocomplete="off"><button class="btn-ghost">Ask</button></form>
        <div class="ai-chips">${['Where is fire risk highest right now?', 'Which areas have the strongest winds today?', ...PROMPTS.slice(0, 2)].map((p) => `<button type="button" class="ai-chip" data-q="${esc(p)}">${esc(p)}</button>`).join('')}</div>
        <div id="g-answer" class="answer" hidden></div>
      </div>`;
    const out = $('#g-answer');
    const run = (q) => { q = q.trim(); if (q) { $('#g-q').value = q; ask(out, q); } };
    $('#g-form').onsubmit = (e) => { e.preventDefault(); run($('#g-q').value); };
    $$('.ai-chip', root).forEach((b) => b.addEventListener('click', () => run(b.dataset.q)));
    if (answers.has('global')) renderAnswer(out, answers.get('global'));
  },
};
register('ai', aiView);
