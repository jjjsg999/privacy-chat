const form = document.getElementById('settings-form');
const status = document.getElementById('status');
const save = document.getElementById('save');
const $ = id => document.getElementById(id);
let catalogue = [];
let savedModel = '';
let configured = false;
let favourites = new Set();
let settingsLoaded = false;
let visibleCount = 60;
function priceOf(model, key) {
  const value = model.pricing?.[key];
  return value != null && value !== '' && Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) * 1e6 : null;
}
function modesOf(model, direction) {
  const values = model.architecture?.[`${direction}_modalities`];
  if (Array.isArray(values)) return values.filter(v => typeof v === 'string');
  const parts = typeof model.architecture?.modality === 'string' ? model.architecture.modality.split('->') : [];
  return parts.length === 2 ? parts[direction === 'input' ? 0 : 1].split('+').map(v => v.trim()).filter(Boolean) : [];
}
function connectionState() { $('connection').textContent = configured ? 'Key configured · Connection not tested for this model' : 'Not connected · No API key configured on the server'; }
function report(message, error = false) { status.textContent = message; status.classList.toggle('error', error); }
function showMonthly(data) {
  form.elements.monthly_cost_ceiling_usd.value = data.monthly_cost_ceiling_usd ?? '2.00';
  const budget = data.monthly_budget;
  $('monthly-budget-status').textContent = !budget ? 'Monthly usage unavailable.' : !budget.available ? budget.error :
    `${budget.month}: US$${Number(budget.used_usd).toFixed(4)} used or reserved � US$${Number(budget.remaining_usd).toFixed(4)} remaining. Includes US$${Number(budget.reserved_usd).toFixed(4)} awaiting confirmed cost.`;
}
async function load() {
  save.disabled = true;
  try {
    const response = await fetch('/api/settings'); if (!response.ok) throw new Error('Could not load settings. Reload to try again.');
    const data = await response.json(); showMonthly(data); form.elements.model.value = data.model; form.elements.terms.value = data.sensitive_terms; save.disabled = false;
    form.elements.chat_diagnosis.checked = Boolean(data.chat_diagnosis); savedModel = data.model; $('current-model').textContent = savedModel; configured = data.openrouter.configured; connectionState(); $('test-connection').disabled = false;
    favourites = new Set(data.favourites || []); settingsLoaded = true; if (catalogue.length) showModels();
  } catch (error) { report(error.message, true); }
}
form.onsubmit = async event => {
  event.preventDefault(); save.disabled = true; $('test-connection').disabled = true; report('Saving…');
  try {
    const response = await fetch('/api/settings', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({model:form.elements.model.value.trim(), sensitive_terms:form.elements.terms.value, openrouter_api_key:form.elements.openrouter_api_key.value, clear_api_key:form.elements.clear_api_key.checked, chat_diagnosis:form.elements.chat_diagnosis.checked, monthly_cost_ceiling_usd:form.elements.monthly_cost_ceiling_usd.value})});
    if (!response.ok) throw new Error('Could not save. Check the model ID and try again.');
    const data = await response.json(); showMonthly(data); savedModel = data.model; $('current-model').textContent = savedModel; configured = data.openrouter.configured; connectionState();
    form.elements.openrouter_api_key.value = ''; form.elements.clear_api_key.checked = false;
    report('Settings saved locally.');
  } catch (error) { report(error.message, true); }
  finally { save.disabled = false; $('test-connection').disabled = false; }
};
load();

function showModels() {
  const query = $('model-search').value.trim().toLowerCase();
  const matches = catalogue.filter(m => {
    if (!`${m.id} ${m.name || ''}`.toLowerCase().includes(query)) return false;
    if ($('favourites-only').checked && !favourites.has(m.id)) return false;
    for (const [direction, key] of [['input', 'prompt'], ['output', 'completion']]) {
      const limit = $(`${direction}-cost`).value;
      const price = priceOf(m, key);
      if (limit !== '' && (price === null || price > Number(limit))) return false;
      const mode = $(`${direction}-mode`).value;
      if (mode && !modesOf(m, direction).includes(mode)) return false;
    }
    return true;
  });
  $('model-results').replaceChildren();
  $('catalogue-status').textContent = `${matches.length} matching models · ${catalogue.length} in catalogue · showing ${Math.min(visibleCount, matches.length)}`;
  $('show-more').hidden = matches.length <= visibleCount;
  for (const model of matches.slice(0, visibleCount)) {
    const card = document.createElement('article'); card.className = 'model-card';
    const name = document.createElement('strong'); name.textContent = model.name || model.id;
    const id = document.createElement('p'); id.textContent = model.id;
    const details = document.createElement('p'); details.className = 'hint';
    const info = [`Provider / author: ${model.id.split('/')[0]}`];
    if (Number.isFinite(Number(model.context_length)) && Number(model.context_length) > 0) info.push(`Context: ${Number(model.context_length).toLocaleString()} tokens`);
    for (const [key, label] of [['prompt', 'Input'], ['completion', 'Output']]) {
      const price = priceOf(model, key);
      info.push(price === null ? `${label}: price unavailable` : `${label}: $${price.toLocaleString(undefined, {maximumFractionDigits:6})} / 1M tokens`);
    }
    for (const direction of ['input', 'output']) { const modes = modesOf(model, direction); if (modes.length) info.push(`${direction} modes: ${modes.join(', ')}`); }
    if (Array.isArray(model.supported_parameters) && model.supported_parameters.length) info.push(`Capabilities: ${model.supported_parameters.filter(value => typeof value === 'string').join(', ')}`);
    details.textContent = info.join(' · ');
    const select = document.createElement('button'); select.type = 'button'; select.textContent = 'Select model';
    select.onclick = () => { form.elements.model.value = model.id; report('Model selected. Save settings to apply.'); form.elements.model.focus(); };
    const star = document.createElement('button'); star.type = 'button'; star.className = 'favourite-button';
    star.textContent = favourites.has(model.id) ? '★ Favourited' : '☆ Favourite'; star.setAttribute('aria-pressed', String(favourites.has(model.id))); star.disabled = !settingsLoaded;
    star.onclick = async () => {
      star.disabled = true;
      try {
        const response = await fetch('/api/settings/favourite', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({model:model.id, favourite:!favourites.has(model.id)})});
        if (!response.ok) throw new Error('Could not save favourite. Please retry.');
        favourites = new Set((await response.json()).favourites); showModels(); report('Favourites saved.');
      } catch (error) { report(error.message, true); star.disabled = false; }
    };
    card.append(name, id, details, select, star); $('model-results').append(card);
  }
}
for (const id of ['model-search', 'input-cost', 'output-cost', 'input-mode', 'output-mode', 'favourites-only']) $(id).oninput = () => { visibleCount = 60; showModels(); };
$('show-more').onclick = () => { visibleCount += 60; showModels(); };
$('model-search').onfocus = () => { if (!catalogue.length && !$('refresh-models').disabled) $('refresh-models').click(); };
$('refresh-models').onclick = async () => {
  $('refresh-models').disabled = true; $('catalogue-status').textContent = 'Loading live catalogue…';
  try {
    const response = await fetch('/api/openrouter/models'); if (!response.ok) throw new Error('Catalogue unavailable. Retry or enter a model ID manually.'); catalogue = await response.json();
    for (const direction of ['input', 'output']) {
      const select = $(`${direction}-mode`); const current = select.value;
      select.replaceChildren(new Option(`Any ${direction} mode`, ''));
      for (const mode of [...new Set(catalogue.flatMap(m => modesOf(m, direction)))].sort()) select.add(new Option(mode, mode));
      if ([...select.options].some(option => option.value === current)) select.value = current;
    }
    visibleCount = 60; showModels();
  }
  catch (error) { $('catalogue-status').textContent = error.message; }
  finally { $('refresh-models').disabled = false; }
};
$('refresh-models').click();
$('test-connection').onclick = async () => {
  $('test-connection').disabled = true; save.disabled = true; $('connection').textContent = 'Testing saved model…';
  try { const response = await fetch('/api/openrouter/test', {method:'POST'}); if (!response.ok) throw new Error('Connection test unavailable. Try again.'); const result = await response.json(); $('connection').textContent = `${result.message} (${result.model})`; }
  catch (error) { $('connection').textContent = error.message; }
  finally { $('test-connection').disabled = false; save.disabled = false; }
};
