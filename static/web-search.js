// Web search UI for the chat page: header chip, composer switch, search cards, rerun buttons, link banner,
// sidebar marker, details sections, clickable [n] citations and export lines. Plain DOM; all text set with
// textContent. Nothing here exists or runs unless features.web_search is on in Settings.

const $ = id => document.getElementById(id);
let config = null;             // GET /api/web-search/settings
let newChatSearch = false;     // the composer switch before a new chat is saved
let hooks = {};                // {api, status, getCurrent, render, isBusy, runAction}

const LABELS = {public: 'Public', generic: 'Generic', skipped: 'Skipped', named: 'Unmasked', named_hidden: 'Unmasked'};
const SOURCE_TEXT = {terms: 'your terms list', detector: 'found by the detector', amount: 'currency amount', date: 'date', unknown: 'unknown placeholder'};
const ICONS = {
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  hidden: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><path d="M4 4l16 16"/>',
  eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  person: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  rerun: '<path d="M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15.5 6.2L3 16M3 21v-5h5"/>',
};

export const enabled = () => Boolean(config?.features?.web_search);
const options = () => config?.web_search || {};
const engineName = engine => (config?.engines?.[engine]?.label) || {brave: 'Brave Search', ddgs: 'ddgs', fake: 'Fake search'}[engine] || engine;
const time = iso => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}); };
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}
function icon(name, size = 12) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('width', size); svg.setAttribute('height', size);
  svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false'); svg.classList.add('ico');
  svg.innerHTML = ICONS[name];  // fixed strings above, never data
  return svg;
}
function chip(text, kind, iconName) {
  const node = el('span', `ws-chip ws-${kind}`);
  if (iconName) node.append(icon(iconName));
  node.append(text);
  return node;
}
function domain(url) { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; } }
function sourceLink(source) {
  const link = el('a', 'ws-src', `[${source.n}] ${source.title.length > 48 ? source.title.slice(0, 47) + '…' : source.title}`);
  if (/^https?:\/\//i.test(source.url)) { link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
  link.title = domain(source.url);
  return link;
}
// The query in monospace, with each added name highlighted.
function queryBox(query, names = []) {
  const box = el('div', 'ws-query');
  const values = names.map(n => n.display).filter(Boolean).sort((a, b) => b.length - a.length);
  if (!values.length) { box.textContent = query; return box; }
  const pattern = new RegExp(values.map(v => v.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'gi');
  let cursor = 0;
  for (const match of query.matchAll(pattern)) {
    box.append(query.slice(cursor, match.index), el('mark', null, match[0]));
    cursor = match.index + match[0].length;
  }
  box.append(query.slice(cursor));
  return box;
}
const joinAnd = parts => parts.length < 2 ? (parts[0] || '') : `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}`;

export async function init(callbacks) {
  hooks = callbacks;
  try { config = await hooks.api('/api/web-search/settings'); } catch { config = null; }
  newChatSearch = Boolean(options().enabled_for_new_chats);
  const toggle = $('ws-toggle');
  if (toggle) toggle.onclick = toggleChat;
  sync(hooks.getCurrent());
  return enabled();
}

// Header chip, composer switch, link banner.
export function sync(session) {
  const on = enabled() && (!session || (session.session_type || 'chat') === 'chat');
  const chatOn = on && (session?.id ? session.web_search === true : newChatSearch);
  if ($('ws-bar')) $('ws-bar').hidden = !on;
  if ($('ws-chip')) $('ws-chip').hidden = !chatOn;
  if ($('ws-toggle')) {
    $('ws-toggle').replaceChildren(icon('globe', 13), chatOn ? `Web search: on · up to ${options().max_searches_per_message || 3} per message` : 'Web search: off');
    $('ws-toggle').setAttribute('aria-pressed', String(chatOn));
    $('ws-toggle').classList.toggle('on', chatOn);
  }
  if ($('ws-help')) $('ws-help').hidden = !chatOn;
  renderBanner(session);
}
export const newChatWebSearch = () => enabled() ? newChatSearch : null;

async function toggleChat() {
  const session = hooks.getCurrent();
  if (hooks.isBusy()) { hooks.status('Wait for the current reply to finish first.', true); return; }
  if (!session?.id) { newChatSearch = !newChatSearch; sync(session); return; }
  try {
    const updated = await hooks.api(`/api/sessions/${encodeURIComponent(session.id)}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({web_search: session.web_search !== true})});
    hooks.render(updated, {keepScroll: true});
  } catch (error) { hooks.status(error.message, true); }
}

function linkedNames(session) {
  const seen = new Map();
  for (const r of session?.revealed_names || []) if (r.token && (r.sent_to || []).includes('model') && !seen.has(r.token)) seen.set(r.token, r);
  return [...seen.values()];
}
function renderBanner(session) {
  const banner = $('ws-banner');
  if (!banner) return;
  const linked = enabled() && options().show_link_banner !== false ? linkedNames(session) : [];
  banner.hidden = !linked.length;
  if (!linked.length) { banner.replaceChildren(); return; }
  const text = el('span');
  text.append('In this chat, ');
  linked.forEach((r, i) => {
    if (i) text.append(i === linked.length - 1 ? ' and ' : ', ');
    text.append(el('span', 'mono', r.token), ' is linked to ', el('strong', null, r.display));
  });
  text.append(linked.length === 1 ? `. You reran a search with this name at ${time(linked[0].at)}.` : '. You reran searches with these names.');
  banner.replaceChildren(icon('person', 14), text);
}

// Sidebar: a small marker on chats where a name is linked.
export function sidebarMarker(item) {
  if (!item.has_linked_names || !enabled()) return null;
  const mark = el('span', 'ws-linked-mark');
  mark.setAttribute('role', 'img'); mark.setAttribute('aria-label', 'A name was revealed in this chat'); mark.title = 'A name was revealed in this chat';
  mark.append(icon('person', 13));
  return mark;
}

// ---------------------------------------------------------------- search cards

// Searches shown with the reply to message `messageId` (a user message or a rerun event).
export const searchesFor = (session, messageId) => (session?.searches || []).filter(s => messageId && (s.event_id || s.turn) === messageId);

export function cards(session, searches) {
  const wrap = el('div', 'ws-cards');
  const planned = searches.filter(s => !s.parent);  // "Search i of n" counts the searches planned for the message, not reruns
  searches.forEach(search => wrap.append(card(session, search, planned.indexOf(search), planned.length)));
  const bar = rerunBar(planned);  // one bar for the whole message: one click reruns every search unmasked
  if (bar) wrap.append(bar);
  return wrap;
}

function card(session, search, position, count) {
  const hidden = search.kind === 'named_hidden';
  const named = search.kind === 'named' || hidden;
  const box = el('div', `ws-card${named ? ' ws-card-named' : ''}`);
  box.dataset.search = search.id;
  const head = el('div', 'ws-head');
  const meta = [];
  if (search.status === 'running') {
    head.append(chip('Searching…', 'running', 'globe'));
    if (search.sent_query) { box.append(head, el('div', 'ws-label', 'Exact query sent'), queryBox(search.sent_query, named ? restored(search) : [])); return box; }
    box.append(head); return box;
  }
  if (search.status === 'budget') {
    head.append(chip('Budget', 'muted'), el('span', 'ws-explain', 'Search skipped: monthly limit reached'));
    box.append(head); return box;
  }
  if (search.status === 'failed') {
    head.append(chip('Failed', 'failed'), el('span', 'ws-explain', `Search failed (Reason: ${search.error || 'unknown'})`));
  } else if (search.kind === 'skipped') {
    head.append(chip('Skipped', 'generic', 'hidden'), el('span', 'ws-explain', 'Not run: too little left without names'));
  } else if (hidden) {
    head.append(chip('Unmasked', 'named', 'person'), chip('Hidden from AI', 'muted', 'eye'));
  } else if (search.kind === 'named') {
    head.append(chip('Unmasked', 'named', 'person'), el('span', 'ws-explain', `Sent unmasked to ${engineName(search.engine)} and the chat model`));
  } else if (search.kind === 'public') {
    head.append(chip('Public', 'public', 'globe'), el('span', 'ws-explain', 'Sent as written — no names in this query'));
  } else {
    head.append(chip('Generic', 'generic', 'hidden'), el('span', 'ws-explain', 'Names removed before sending'));
  }
  if (search.status === 'done') {
    if (count > 1 && !named) meta.push(`Search ${position + 1} of ${count}`);
    if (!named) meta.push(engineName(search.engine));
    meta.push(plural(search.result_count || 0, 'result'));
    if (search.pages_read && !hidden) meta.push(`${search.pages_read} page${search.pages_read === 1 ? '' : 's'} read`);
    head.append(el('span', 'ws-meta', meta.join(' · ')));
  }
  box.append(head);
  if (search.sent_query && search.status !== 'skipped') box.append(el('div', 'ws-label', 'Exact query sent'), queryBox(search.sent_query, named ? restored(search) : []));
  const removed = (search.removed || []);
  const foot = el('div', 'ws-foot');
  if (removed.length) {
    const list = el('div', 'ws-removed'); list.append('Removed:');
    for (const r of removed) {
      const item = el('span', 'ws-removed-item');
      if (r.source === 'amount' || r.source === 'unknown' && r.display === r.token) item.append(`${r.display} · ${SOURCE_TEXT[r.source]}`);
      else item.append(`${r.display} · `, el('span', 'mono', r.token || ''), ` · ${SOURCE_TEXT[r.source] || r.source}`);
      list.append(item);
    }
    foot.append(list);
  }
  if (named && search.status === 'done') {
    const revealed = (session.revealed_names || []).filter(r => r.search_id === search.id);
    if (!hidden) foot.append(el('span', 'ws-meta', `Rerun unmasked by you at ${time(revealed[0]?.at || search.created_at)}`));
  }
  if (!hidden && search.sources?.length) {
    const links = el('div', 'ws-links');
    for (const source of search.sources) links.append(sourceLink(source));
    foot.append(links);
  }
  if (foot.childNodes.length) box.append(foot);
  if (hidden && search.status === 'done') box.append(hiddenResults(search));
  return box;
}

// Everything a rerun restores (names, dates and currency amounts), for highlighting.
const restored = search => [...(search.rerun_names || []), ...(search.rerun_extras || [])];
const rerunnable = search => ['generic', 'skipped'].includes(search.kind) && ['done', 'skipped'].includes(search.status) && !search.rerun_by;

// One bar under a message's search cards: what a rerun would send, and one click to rerun every search unmasked.
function rerunBar(searches) {
  const ready = searches.filter(s => rerunnable(s) && s.rerun_query);
  const withheld = searches.some(s => rerunnable(s) && s.rerun_withheld);
  if (!ready.length && !withheld) return null;
  const area = el('div', 'ws-rerun ws-rerun-bar');
  if (ready.length) {
    const head = el('div', 'ws-label', ready.length > 1 ? `A rerun would send (${ready.length} searches)` : 'A rerun would send');
    const list = el('div', 'ws-would');
    for (const search of ready) list.append(queryBox(search.rerun_query, restored(search)));
    const row = el('div', 'ws-actions');
    const turn = ready[0].turn;
    const url = () => `/api/sessions/${encodeURIComponent(hooks.getCurrent().id)}/turns/${encodeURIComponent(turn)}/rerun`;
    const main = el('button', 'ws-rerun-btn'); main.type = 'button';
    main.append(icon('rerun', 13), 'Rerun unmasked');
    main.onclick = () => action(url(), {hide_from_ai: false});
    row.append(main);
    if (options().offer_hidden_rerun !== false) {
      const hide = el('button', 'ws-secondary', 'Rerun unmasked, hide from AI'); hide.type = 'button';
      hide.onclick = () => action(url(), {hide_from_ai: true});
      row.append(hide);
    }
    const names = [];
    for (const search of ready) for (const n of search.rerun_names || []) if (!names.some(x => x.token === n.token)) names.push(n);
    const consequence = el('p', 'ws-meta ws-consequence');
    consequence.append(`A rerun sends the full query, names, dates and amounts included, to ${engineName(ready[0].engine)}`);
    if (names.length) {
      consequence.append(' and links ');
      names.forEach((n, i) => { if (i) consequence.append(i === names.length - 1 ? ' and ' : ', '); consequence.append(el('span', 'mono', n.token), ` to ${n.display}`); });
      consequence.append(' in this chat.');
    } else consequence.append('.');
    area.append(head, list, row, consequence);
    const detected = [];
    for (const search of ready) for (const n of search.rerun_names || []) if (n.source === 'detector' && !detected.some(x => x.token === n.token)) detected.push(n);
    if (detected.length) {
      const publicRow = el('div', 'ws-public-links');
      for (const n of detected) {
        const link = el('button', 'ws-link', `Always treat ${n.display} as public`); link.type = 'button';
        link.onclick = () => treatAsPublic(n.display, url());
        publicRow.append(link);
      }
      area.append(publicRow);
    }
  }
  if (withheld) {
    const note = el('p', 'ws-note'); note.append('Names on your terms list are never searched by name (');
    const link = el('a', null, 'Settings'); link.href = '/settings#web-search-card'; note.append(link, ').');
    area.append(note);
  }
  return area;
}

// "Always treat <Name> as public": the name is no longer masked in any chat; then offer the rerun.
async function treatAsPublic(name, rerunUrl) {
  if (hooks.isBusy()) { hooks.status('Wait for the current reply to finish first.', true); return; }
  try {
    config = {...config, ...(await hooks.api('/api/public-names', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name})}))};
  } catch (error) { hooks.status(error.message, true); return; }
  hooks.status(`${name} is now treated as public in every chat. Your terms list still wins.`);
  if (window.confirm(`${name} will no longer be masked. Rerun this message's searches unmasked now?`)) action(rerunUrl, {hide_from_ai: false});
}

function hiddenResults(search) {
  const wrap = el('div', 'ws-hidden');
  const tokens = (search.rerun_names || []).map(n => n.token);
  const note = el('p', 'ws-note'); note.append('The chat model has not seen these results, so ');
  tokens.forEach((t, i) => { if (i) note.append(' and '); note.append(el('span', 'mono', t)); });
  note.append(tokens.length === 1 ? ' stays unlinked.' : ' stay unlinked.');
  const list = el('ol', 'ws-results');
  for (const source of search.sources || []) {
    const item = el('li'); const link = el('a', null, source.title);
    if (/^https?:\/\//i.test(source.url)) { link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
    item.append(link, el('div', 'ws-meta', [domain(source.url), source.snippet].filter(Boolean).join(' · ')));
    list.append(item);
  }
  const row = el('div', 'ws-actions');
  const send = el('button', 'ws-rerun-btn', 'Send these results to the model…'); send.type = 'button';
  const consequence = `This would link ${joinAnd((search.rerun_names || []).map(n => `${n.token} to ${n.display}`))}.`;
  send.onclick = () => {
    if (!window.confirm(`Send these results to the chat model? ${consequence}`)) return;
    action(`/api/sessions/${encodeURIComponent(hooks.getCurrent().id)}/searches/${encodeURIComponent(search.id)}/send-to-model`, null);
  };
  row.append(send, el('span', 'ws-meta', consequence));
  wrap.append(note, list, row);
  return wrap;
}

function action(url, payload) {
  if (hooks.isBusy()) { hooks.status('Wait for the current reply to finish first.', true); return; }
  hooks.runAction(url, payload);
}

// While a reply streams: cards are kept by id and redrawn as events arrive.
export function liveCards(container, session) {
  const state = new Map();
  return {
    update(search) { state.set(search.id, search); container.replaceChildren(cards(session, [...state.values()])); },
    note(text) { container.append(el('p', 'ws-note', text)); },
  };
}

export function note(text) { return el('p', 'ws-note', text); }

// Event messages (rerun / send to model) show as one line, not a chat bubble.
export function eventRow(message) {
  const row = el('div', 'ws-event');
  row.append(icon(message.event === 'rerun' ? 'rerun' : 'person', 13), message.content);
  return row;
}

// [n] in a rendered answer becomes a link to that source (new tab). Code and existing links are left alone.
export function linkCitations(element, session) {
  const urls = new Map();
  for (const s of session?.searches || []) for (const src of s.sources || []) if (s.kind !== 'named_hidden' && /^https?:\/\//i.test(src.url)) urls.set(String(src.n), src);
  if (!urls.size) return;
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {acceptNode: node =>
    node.parentElement?.closest('code, pre, a') ? NodeFilter.FILTER_REJECT : (/\[\d+\]/.test(node.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP)});
  const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const fragment = document.createDocumentFragment(); let cursor = 0;
    for (const match of node.nodeValue.matchAll(/\[(\d+)\]/g)) {
      const source = urls.get(match[1]); if (!source) continue;
      fragment.append(node.nodeValue.slice(cursor, match.index));
      const link = el('a', 'ws-cite', match[0]); link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      link.title = `${source.title} — ${domain(source.url)}`;
      fragment.append(link); cursor = match.index + match[0].length;
    }
    if (!cursor) continue;
    fragment.append(node.nodeValue.slice(cursor)); node.replaceWith(fragment);
  }
}

// Markdown export: one line per search after the reply it belongs to.
export function exportLines(session, messageId) {
  return searchesFor(session, messageId).filter(s => s.sent_query).map(s => {
    const label = s.kind === 'named_hidden' ? 'Unmasked, hidden from AI' : (LABELS[s.kind] || s.kind);
    const sources = (s.sources || []).map(src => `[${src.n}]`).join(' ');
    return `Web search (${label}): \`${s.sent_query}\`${s.status === 'done' ? (sources ? ` — sources ${sources}` : '') : ` — ${s.status}`}`;
  });
}

// ---------------------------------------------------------------- details panel

export function renderDetails(session) {
  const section = $('d-ws');
  if (!section) return;
  const show = enabled() && session && (session.session_type || 'chat') === 'chat' && (session.web_search || session.searches?.length);
  section.hidden = !show;
  for (const id of ['d-ws-names-sec', 'd-ws-masked-sec']) if ($(id)) $(id).hidden = !show;
  if (!show) return;
  const searches = (session.searches || []).filter(s => s.status !== 'running');
  $('d-ws-searches-h').textContent = `Searches · ${searches.length}`;
  $('d-ws-searches').replaceChildren(...(searches.length ? [...searches].reverse().map(s => {
    const item = el('li', 'ws-d-search');
    const top = el('div', 'ws-d-top');
    const kind = s.status === 'failed' ? 'failed' : s.status === 'budget' ? 'muted' : (s.kind === 'named_hidden' ? 'named' : s.kind === 'skipped' ? 'generic' : s.kind);
    top.append(chip(s.status === 'failed' ? 'Failed' : s.status === 'budget' ? 'Budget' : (LABELS[s.kind] || s.kind), kind));
    if (rerunnable(s) && s.rerun_query) top.append(el('span', 'ws-meta', 'Rerun available'));
    if (s.kind === 'named_hidden') top.append(el('span', 'ws-meta', 'Hidden from AI'));
    top.append(el('span', 'ws-meta ws-time', time(s.created_at)));
    item.append(top, el('div', 'ws-d-query mono', s.sent_query || '(not run)'));
    return item;
  }) : [Object.assign(el('li', 'd-empty'), {textContent: 'No searches yet.'})]));
  const spend = searches.reduce((sum, s) => sum + (Number(s.cost_usd) || 0), 0);
  $('d-ws-spend').hidden = !spend;
  $('d-ws-spend').replaceChildren('Search spend in this chat: ', el('strong', null, `$${spend.toFixed(3).replace(/0$/, '')}`), '. Counts toward your monthly limit.');

  const revealed = session.revealed_names || [];
  $('d-ws-names-h').textContent = `Names revealed · ${revealed.length}`;
  $('d-ws-names-h').classList.toggle('ws-alert', revealed.length > 0);
  const rerunAvailable = searches.some(s => rerunnable(s) && s.rerun_query);
  $('d-ws-names').replaceChildren(...(revealed.length ? revealed.map(r => {
    const linked = (r.sent_to || []).includes('model');
    const item = el('li', `ws-d-name${linked ? ' linked' : ''}`);
    const top = el('div', 'ws-d-top'); top.append(el('strong', null, r.display), ...(r.token ? [el('span', 'mono', r.token)] : []),
      el('span', `ws-badge${linked ? ' on' : ''}`, !r.token ? (linked ? 'Sent to the chat model' : 'Not sent to the model') : linked ? 'Linked in this chat' : 'Not linked'));
    const grid = el('dl', 'ws-d-grid');
    const search = (session.searches || []).find(s => s.id === r.search_id);
    const hiddenOrigin = search?.parent && !linked;
    grid.append(el('dt', null, 'When'), el('dd', null, `${time(r.at)} · ${hiddenOrigin ? 'rerun unmasked, hidden from AI' : 'rerun unmasked'}`),
      el('dt', null, 'Sent to'), el('dd', null, linked ? `${engineName(search?.engine || 'brave')}, chat model` : `${engineName(search?.engine || 'brave')} only`),
      el('dt', null, 'Source'), el('dd', null, {terms: 'Your terms list', amount: 'Currency amount', date: 'Date'}[r.source] || 'Found by the detector'));
    item.append(top, grid); return item;
  }) : [Object.assign(el('li', 'd-empty'), {textContent: rerunAvailable ? 'None. Searches run without names first; a name is sent only when you rerun with it.' : 'None. Every name in this chat is still masked.'})]));
  $('d-ws-names-foot').hidden = !revealed.length;

  const revealedTokens = new Set(revealed.map(r => r.token));
  const masked = (session.masked_items || []).filter(m => !revealedTokens.has(m.token));
  $('d-ws-masked-h').textContent = `${revealed.length ? 'Still masked' : 'Masked in this chat'} · ${masked.length}`;
  $('d-ws-masked').replaceChildren(...(masked.length ? masked.map(m => {
    const item = el('li', 'ws-d-masked'); const top = el('div', 'ws-d-top');
    top.append(el('span', null, m.display), el('span', 'mono', m.token));
    item.append(top, el('div', 'ws-meta', m.source === 'terms' ? 'From your terms list' : 'Found by the detector'));
    return item;
  }) : [Object.assign(el('li', 'd-empty'), {textContent: 'Nothing masked yet.'})]));
}
