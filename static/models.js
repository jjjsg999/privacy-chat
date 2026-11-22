const $ = id => document.getElementById(id);
const PAGE = 100;
let catalogue = [], favourites = new Set(), currentModel = '', meetingModel = '', visible = PAGE;
let sortKey = 'input', sortDir = 1;

const COLUMNS = [
  {key: 'name', label: 'Model'}, {key: 'provider', label: 'Provider'},
  {key: 'context', label: 'Context', num: true}, {key: 'input', label: 'Input $ / 1M', num: true},
  {key: 'output', label: 'Output $ / 1M', num: true}, {key: 'spent', label: 'Spent this month', num: true},
  {key: 'modes', label: 'Modalities', sortable: false},
  {key: 'actions', label: '', sortable: false}
];

// OpenRouter reports dollars per token; show dollars per million. Missing or negative (variable-priced) means unknown.
function priceOf(model, key) {
  const raw = model.pricing?.[key];
  if (raw === undefined || raw === null || raw === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? value * 1e6 : null;
}
function formatPrice(price) {
  if (price === null) return '—';
  if (price === 0) return 'Free';
  return '$' + Number(price.toPrecision(3)).toLocaleString(undefined, {maximumFractionDigits: 6});
}
function modes(model, direction) {
  const list = model.architecture?.[`${direction}_modalities`];
  return Array.isArray(list) ? list.filter(value => typeof value === 'string') : [];
}
// Mirrors why the monthly cost guard refuses a model: no usable price, or a web-search variant.
function blockedReason(model) {
  if (model.id.endsWith(':online')) return 'Web-search variants are not supported';
  if (priceOf(model, 'prompt') === null || priceOf(model, 'completion') === null) return 'Price unknown, so the cost guard blocks it';
  return '';
}
function describe(model) {
  const context = Number(model.context_length);
  return {
    model, id: model.id, name: (model.name || model.id), provider: model.id.split('/')[0],
    context: Number.isFinite(context) && context > 0 ? context : null,
    input: priceOf(model, 'prompt'), output: priceOf(model, 'completion'), blocked: blockedReason(model)
  };
}
// Spend recorded by the monthly cost guard, keyed by model id. Requests made before per-model tracking are "untracked".
let spend = new Map();
function formatSpend(usd) {
  const value = Number(usd);
  return value > 0 ? '$' + (value < 0.01 ? value.toFixed(5) : value.toFixed(4)) : '—';
}
async function loadSpend() {
  spend = new Map();
  try {
    const response = await fetch('/api/spend/models');
    if (!response.ok) throw new Error();
    const data = await response.json();
    for (const entry of data.models || []) spend.set(entry.model, {usd: Number(entry.usd), calls: entry.calls, unsettled: entry.unsettled});
    const untracked = Number(data.untracked_usd) > 0 ? ` · ${formatSpend(data.untracked_usd)} from before per-model tracking` : '';
    $('spend-summary').textContent = data.available ? `Spent in ${data.month}: ${formatSpend(data.total_usd) === '—' ? '$0' : formatSpend(data.total_usd)}${untracked}. Requests not yet settled count at their reserved amount.` : 'Spend is unavailable: the monthly ledger could not be read.';
  } catch (_) { $('spend-summary').textContent = 'Spend by model is unavailable right now.'; }
  for (const row of rows) row.spent = spend.get(row.id)?.usd ?? 0;
}
function number(id) { const value = $(id).value; return value === '' ? null : Number(value); }

function filtered() {
  const query = $('search').value.trim().toLowerCase(), provider = $('provider').value;
  const [minIn, maxIn, minOut, maxOut] = ['min-in', 'max-in', 'min-out', 'max-out'].map(number);
  const free = $('free-only').checked, favouritesOnly = $('favourites-only').checked, usableOnly = $('usable-only').checked;
  return rows.filter(row => {
    if (query && !`${row.id} ${row.name}`.toLowerCase().includes(query)) return false;
    if (provider && row.provider !== provider) return false;
    if (favouritesOnly && !favourites.has(row.id)) return false;
    if (usableOnly && row.blocked) return false;
    if (free && !(row.input === 0 && row.output === 0)) return false;
    for (const [price, min, max] of [[row.input, minIn, maxIn], [row.output, minOut, maxOut]]) {
      if ((min !== null || max !== null) && price === null) return false;  // unknown price cannot satisfy a price filter
      if (min !== null && price < min) return false;
      if (max !== null && price > max) return false;
    }
    return true;
  });
}
function sorted(list) {
  const value = row => row[sortKey];
  return [...list].sort((a, b) => {
    const x = value(a), y = value(b);
    if (x === null && y === null) return a.id.localeCompare(b.id);
    if (x === null) return 1;   // unknown values always last
    if (y === null) return -1;
    const order = typeof x === 'string' ? x.localeCompare(y) : x - y;
    return (order * sortDir) || a.id.localeCompare(b.id);
  });
}

let rows = [];
function renderHead() {
  const sortValue = `${sortKey}:${sortDir}`; if ([...$('sort').options].some(o => o.value === sortValue)) $('sort').value = sortValue;
  $('head').replaceChildren();
  for (const column of COLUMNS) {
    const th = document.createElement('th'); th.scope = 'col'; if (column.num) th.className = 'num';
    if (column.sortable === false || !column.label) { th.textContent = column.label; }
    else {
      const button = document.createElement('button'); button.type = 'button';
      button.textContent = column.label + (sortKey === column.key ? (sortDir === 1 ? ' ▲' : ' ▼') : '');
      th.setAttribute('aria-sort', sortKey === column.key ? (sortDir === 1 ? 'ascending' : 'descending') : 'none');
      button.onclick = () => {
        if (sortKey === column.key) sortDir = -sortDir; else { sortKey = column.key; sortDir = column.key === 'context' ? -1 : 1; }
        visible = PAGE; render();
      };
      th.append(button);
    }
    $('head').append(th);
  }
}
function status(text, error = false) { $('count').textContent = text; $('count').classList.toggle('error', error); }
function showCurrent() {
  $('current-model').textContent = currentModel || 'Not set';
  $('current-meeting-model').textContent = meetingModel || (currentModel ? `${currentModel} (same as chats)` : 'Not set');
  $('reset-meeting').hidden = !meetingModel;
}
// Embedded in Settings: picking a model fills the Settings form (via the parent page) instead of saving.
const EMBED = new URLSearchParams(location.search).has('embed') && window.parent !== window;
if (EMBED) {
  document.body.classList.add('embed');
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || event.source !== window.parent || event.data?.type !== 'privacy-llm-current') return;
    currentModel = String(event.data.model || ''); meetingModel = String(event.data.meeting_model || ''); showCurrent(); render();
  });
}
// purpose: 'general' (all other chats) or 'meeting' (blank model = follow the general model).
async function choose(model, purpose, label) {
  if (EMBED) {
    window.parent.postMessage({type: 'privacy-llm-pick', model, purpose}, location.origin);
    if (purpose === 'general') currentModel = model; else meetingModel = model;
    showCurrent(); render(); status('Filled in the Settings form. Click Save settings to apply.');
    return;
  }
  const response = await fetch('/api/settings/model', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({model, purpose})});
  if (!response.ok) throw new Error('Could not save the model. Please retry.');
  const saved = await response.json();
  currentModel = saved.model; meetingModel = saved.meeting_model || ''; showCurrent(); render();
  status(label);
}

function render() {
  renderHead();
  const matches = sorted(filtered());
  $('rows').replaceChildren();
  status(`${matches.length.toLocaleString()} of ${rows.length.toLocaleString()} models match · showing ${Math.min(visible, matches.length)}`);
  $('more').hidden = matches.length <= visible;
  for (const row of matches.slice(0, visible)) {
    const tr = document.createElement('tr'); tr.classList.toggle('in-use', row.id === currentModel);
    const first = document.createElement('td');
    const name = document.createElement('strong'); name.textContent = row.name; first.append(name);
    for (const [text, cond] of [['In use', row.id === currentModel], ['Meetings', row.id === meetingModel], ['Unavailable', Boolean(row.blocked)]]) {
      if (!cond) continue; const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = text;
      if (text === 'Unavailable') badge.title = row.blocked; first.append(badge);
    }
    const id = document.createElement('span'); id.className = 'model-id'; id.textContent = row.id; first.append(id);
    const provider = document.createElement('td'); provider.textContent = row.provider;
    const context = document.createElement('td'); context.className = 'num'; context.textContent = row.context ? row.context.toLocaleString() : '—';
    const input = document.createElement('td'); input.className = 'num'; input.textContent = formatPrice(row.input);
    const output = document.createElement('td'); output.className = 'num'; output.textContent = formatPrice(row.output);
    context.dataset.label = 'Context'; input.dataset.label = 'Input $ / 1M'; output.dataset.label = 'Output $ / 1M';
    const spentCell = document.createElement('td'); spentCell.className = 'num'; spentCell.dataset.label = 'Spent this month';
    const used = spend.get(row.id);
    spentCell.textContent = used ? formatSpend(used.usd) : '—';
    if (used) spentCell.title = `${used.calls} request${used.calls === 1 ? '' : 's'}${used.unsettled ? `, ${used.unsettled} not yet settled (counted at the reserved amount)` : ''}`;
    const kinds = document.createElement('td');
    const inModes = modes(row.model, 'input'), outModes = modes(row.model, 'output');
    kinds.textContent = inModes.length || outModes.length ? `${inModes.join(', ') || '?'} → ${outModes.join(', ') || '?'}` : '—';
    const actions = document.createElement('td'); actions.className = 'actions';
    const use = document.createElement('button'); use.type = 'button';
    use.textContent = row.id === currentModel ? 'In use' : 'Use'; use.disabled = row.id === currentModel || Boolean(row.blocked);
    if (row.blocked) use.title = row.blocked;
    use.setAttribute('aria-label', `Use model ${row.name}`);
    use.onclick = async () => {
      use.disabled = true;
      try { await choose(row.id, 'general', `Chats and Adviser now use ${row.name}. New messages use this model.`); }
      catch (error) { status(error.message, true); use.disabled = false; }
    };
    const meeting = document.createElement('button'); meeting.type = 'button';
    meeting.textContent = row.id === meetingModel ? 'For meetings' : 'Meetings'; meeting.disabled = row.id === meetingModel || Boolean(row.blocked);
    meeting.title = row.blocked || 'Use this model for meeting analysis and meeting questions (web and Telegram)';
    meeting.setAttribute('aria-label', `Use model ${row.name} for meetings`);
    meeting.onclick = async () => {
      meeting.disabled = true;
      try { await choose(row.id, 'meeting', `Meeting analysis and questions now use ${row.name}, on the web and Telegram.`); }
      catch (error) { status(error.message, true); meeting.disabled = false; }
    };
    const star = document.createElement('button'); star.type = 'button';
    star.textContent = favourites.has(row.id) ? '★' : '☆'; star.setAttribute('aria-pressed', String(favourites.has(row.id)));
    star.setAttribute('aria-label', `${favourites.has(row.id) ? 'Remove favourite' : 'Favourite'}: ${row.name}`);
    star.onclick = async () => {
      star.disabled = true;
      try {
        const response = await fetch('/api/settings/favourite', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({model: row.id, favourite: !favourites.has(row.id)})});
        if (!response.ok) throw new Error('Could not save the favourite. Please retry.');
        favourites = new Set((await response.json()).favourites); render();
      } catch (error) { status(error.message, true); star.disabled = false; }
    };
    actions.append(use, ' ', meeting, ' ', star);
    tr.append(first, provider, context, input, output, spentCell, kinds, actions); $('rows').append(tr);
  }
}

function fillProviders() {
  const select = $('provider'), keep = select.value;
  select.replaceChildren(new Option('All providers', ''));
  for (const name of [...new Set(rows.map(row => row.provider))].sort()) select.append(new Option(name, name));
  select.value = [...select.options].some(option => option.value === keep) ? keep : '';
}
async function load() {
  $('refresh').disabled = true; status('Loading the live catalogue…');
  try {
    const [catalogueResponse, settingsResponse] = await Promise.all([fetch('/api/openrouter/models'), fetch('/api/settings')]);
    if (settingsResponse.ok) {
      const settings = await settingsResponse.json();
      if (!EMBED) { currentModel = settings.model || ''; meetingModel = settings.meeting_model || ''; }
      favourites = new Set(settings.favourites || []);
    }
    showCurrent();
    if (!catalogueResponse.ok) throw new Error('The model catalogue is unavailable. Retry in a moment.');
    catalogue = await catalogueResponse.json();
    rows = catalogue.filter(model => model && typeof model.id === 'string').map(describe);
    await loadSpend();
    fillProviders(); visible = PAGE; render();
  } catch (error) { rows = []; render(); status(error.message || 'The model catalogue is unavailable.', true); }
  finally { $('refresh').disabled = false; }
}
for (const id of ['search', 'provider', 'min-in', 'max-in', 'min-out', 'max-out', 'free-only', 'favourites-only', 'usable-only']) {
  $(id).oninput = () => { visible = PAGE; render(); };
}
$('sort').onchange = () => { const [key, dir] = $('sort').value.split(':'); sortKey = key; sortDir = Number(dir); visible = PAGE; render(); };
$('more').onclick = () => { visible += PAGE; render(); };
$('refresh').onclick = load;
$('reset-meeting').onclick = async () => {
  try { await choose('', 'meeting', 'Meeting analysis and questions now use the same model as chats.'); }
  catch (error) { status(error.message, true); }
};
$('clear').onclick = () => {
  for (const id of ['search', 'provider', 'min-in', 'max-in', 'min-out', 'max-out']) $(id).value = '';
  $('free-only').checked = false; $('favourites-only').checked = false; $('usable-only').checked = true;
  visible = PAGE; render();
};
load();
