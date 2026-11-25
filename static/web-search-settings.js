// Settings: the Web search section. Each change is saved at once through PUT /api/web-search/settings.
// The feature switch always shows; everything else only when it is on. The Brave key is never shown, only its status.
(() => {
  const $ = id => document.getElementById(id);
  if (!$('web-search-card')) return;
  let data = null;

  const report = (text, error = false) => { $('ws-status').textContent = text; $('ws-status').classList.toggle('error', error); };

  async function save(change, done = 'Web search settings saved.') {
    try {
      const response = await fetch('/api/web-search/settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(change)});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not save. Try again.');
      data = result; show(); report(done);
    } catch (error) { report(error.message, true); show(); }
  }

  function show() {
    const on = Boolean(data.features?.web_search), options = data.web_search;
    $('ws-feature').checked = on;
    $('ws-settings').hidden = !on;
    $('ws-default').value = String(Boolean(options.enabled_for_new_chats));
    $('ws-engine').replaceChildren(...['brave', 'ddgs'].map(id => {
      const engine = data.engines[id] || {label: id, available: false, detail: ''};
      const option = new Option(`${id === 'brave' ? 'Brave Search API' : engine.label} · ${engine.detail}`, id);
      option.disabled = !engine.available && options.engine !== id;
      return option;
    }));
    $('ws-engine').value = options.engine;
    $('ws-max').value = String(options.max_searches_per_message);
    $('ws-pages').value = String(options.pages_per_search);
    $('ws-cost').textContent = `A Brave search costs about $${options.cost_per_search_usd} and counts toward your monthly limit. Each message reserves the cost of its maximum number of searches before it runs.`;
    $('ws-places-note').textContent = `Follows your place-name setting (${data.place_names === 'mask_all' ? 'mask all' : 'common public'})`;
    $('ws-dates-note').textContent = data.date_names === 'mask' ? 'Dates are masked in chats, so they are removed from queries (Dates setting under Privacy vocabulary)'
      : 'Dates are not masked (Dates setting under Privacy vocabulary)';
    showVocabulary();
    $('ws-terms').value = options.terms_list_names;
    $('ws-inst-mode').value = data.institution_names;
    $('ws-hidden-rerun').checked = Boolean(options.offer_hidden_rerun);
    $('ws-banner-setting').checked = Boolean(options.show_link_banner);
    $('ws-inst').replaceChildren(...data.institutions.map(name => {
      const item = document.createElement('li'); item.append(name);
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×';
      remove.setAttribute('aria-label', `Remove ${name}`);
      remove.onclick = () => save({institutions: data.institutions.filter(n => n !== name)}, `${name} removed. It will be masked again where the detector finds it.`);
      item.append(remove); return item;
    }));
    const evaluation = data.privacy_eval;
    $('ws-eval').hidden = !evaluation;
    if (evaluation) {
      $('ws-eval').replaceChildren(`Privacy evaluation, last run ${evaluation.date}: recall `, Object.assign(document.createElement('strong'), {textContent: `${evaluation.recall_pct}%`}),
        ' · precision ', Object.assign(document.createElement('strong'), {textContent: `${evaluation.precision_pct}%`}),
        '. Run it again after changing this list: ', Object.assign(document.createElement('code'), {textContent: 'python -m eval.run_privacy_eval'}));
    }
  }

  // Privacy vocabulary: dates, the topic glossary and your public names (always shown; they apply to every chat).
  const vocabReport = (text, error = false) => { $('pv-status').textContent = text; $('pv-status').classList.toggle('error', error); };
  async function saveVocabulary(change, done) {
    try {
      const response = await fetch('/api/web-search/settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(change)});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not save. Try again.');
      data = result; show(); vocabReport(done);
    } catch (error) { vocabReport(error.message, true); show(); }
  }
  function chips(list, key, removedText) {
    return data[key].map(name => {
      const item = document.createElement('li'); item.append(name);
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×';
      remove.setAttribute('aria-label', `Remove ${name}`);
      remove.onclick = () => saveVocabulary({[key]: data[key].filter(n => n !== name)}, `${name} ${removedText}`);
      item.append(remove); return item;
    });
  }
  function showVocabulary() {
    $('pv-dates').value = data.date_names;
    $('pv-topic-mode').value = data.topic_names;
    $('pv-topics-summary').textContent = `Show the glossary (${data.topics.length} terms)`;
    $('pv-topics').replaceChildren(...chips(data.topics, 'topics', 'removed from the glossary.'));
    $('pv-names').replaceChildren(...(data.public_names.length ? chips(data.public_names, 'public_names', 'will be masked again where the detector finds it.')
      : [Object.assign(document.createElement('li'), {className: 'hint', textContent: 'None yet.'})]));
  }
  $('pv-dates').onchange = () => saveVocabulary({date_names: $('pv-dates').value}, $('pv-dates').value === 'pass' ? 'Dates are no longer masked.' : 'Dates are masked again.');
  $('pv-topic-mode').onchange = () => saveVocabulary({topic_names: $('pv-topic-mode').value}, 'Saved.');
  const addTo = (input, key) => () => {
    const name = $(input).value.trim().replace(/\s+/g, ' ');
    if (!name) return;
    if (data[key].some(n => n.toLowerCase() === name.toLowerCase())) { vocabReport(`${name} is already on the list.`, true); return; }
    $(input).value = '';
    saveVocabulary({[key]: [...data[key], name]}, `${name} added.`);
  };
  for (const [input, button, key] of [['pv-topic-new', 'pv-topic-add', 'topics'], ['pv-name-new', 'pv-name-add', 'public_names']]) {
    const add = addTo(input, key);
    $(button).onclick = add;
    $(input).onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); add(); } };
  }

  const bind = (id, key, convert = value => value) => { $(id).onchange = () => save({[key]: convert($(id).value)}); };
  bind('ws-default', 'enabled_for_new_chats', value => value === 'true');
  bind('ws-engine', 'engine');
  bind('ws-max', 'max_searches_per_message', Number);
  bind('ws-pages', 'pages_per_search', Number);
  bind('ws-terms', 'terms_list_names');
  bind('ws-inst-mode', 'institution_names');
  $('ws-feature').onchange = () => save({feature_enabled: $('ws-feature').checked},
    $('ws-feature').checked ? 'Web search is on. Switch it on in a chat with the button under the message box.' : 'Web search is off.');
  $('ws-hidden-rerun').onchange = () => save({offer_hidden_rerun: $('ws-hidden-rerun').checked});
  $('ws-banner-setting').onchange = () => save({show_link_banner: $('ws-banner-setting').checked});
  const add = () => {
    const name = $('ws-inst-new').value.trim().replace(/\s+/g, ' ');
    if (!name) return;
    if (data.institutions.some(n => n.toLowerCase() === name.toLowerCase())) { report(`${name} is already on the list.`, true); return; }
    $('ws-inst-new').value = '';
    save({institutions: [...data.institutions, name]}, `${name} added.`);
  };
  $('ws-inst-add').onclick = add;
  $('ws-inst-new').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); add(); } };

  fetch('/api/web-search/settings').then(response => response.ok ? response.json() : Promise.reject())
    .then(result => { data = result; show(); })
    .catch(() => report('Could not load the web search settings. Reload to try again.', true));
})();
