// "What was sent": shows what the model received for one of your messages, each masked part as a chip (its
// placeholder and kind) over your original words. Fetched on demand; all text is set with textContent.
const $ = id => document.getElementById(id);
let hooks = {};
let current = null;  // {sessionId, index}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}
const kindName = kind => kind.toLowerCase().replace(/_/g, ' ');

function chip(segment) {
  const node = el('span', `sent-chip${segment.source === 'terms' ? ' terms' : ''}`);
  node.title = segment.source === 'terms' ? 'From your terms list' : 'Found by the detector';
  node.append(el('small', 'mono', `${segment.token} · ${kindName(segment.kind)}`), el('span', null, segment.text));
  return node;
}

function itemsList(items) {
  const list = el('ul', 'sent-items');
  for (const item of items) { const li = el('li'); li.append(chip(item)); list.append(li); }
  return list;
}

function report(text, error = false) { $('sent-status').textContent = text; $('sent-status').classList.toggle('error', error); }

function show(data) {
  $('sent-intro').textContent = data.sent_as_written
    ? 'Sent as written: only your terms list was applied to the typed text. Attached files were masked as usual.'
    : 'Each highlighted part was replaced by its placeholder before sending. Everything else went as typed.';
  $('sent-body').replaceChildren(...data.segments.map(s => s.masked ? chip(s) : document.createTextNode(s.text)));
  $('sent-files').replaceChildren(...data.files.map(file => {
    const details = el('details', 'sent-file');
    details.append(el('summary', null, `${file.masked_count} item${file.masked_count === 1 ? '' : 's'} masked in ${file.name}`),
      file.items.length ? itemsList(file.items) : el('p', 'hint', 'Nothing was masked in this file.'));
    return details;
  }));
  const detected = new Map();
  for (const s of [...data.segments, ...data.files.flatMap(f => f.items)]) if (s.masked && s.source === 'detector' && !detected.has(s.text.toLowerCase())) detected.set(s.text.toLowerCase(), s.text);
  $('sent-public').replaceChildren(...[...detected.values()].map(name => {
    const button = el('button', 'ws-link', `Always treat ${name} as public`); button.type = 'button';
    button.onclick = async () => {
      try {
        await hooks.api('/api/public-names', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name})});
        button.disabled = true;
        report(`${name} is now treated as public in every chat, from your next message on. Your terms list still wins.`);
      } catch (error) { report(error.message, true); }
    };
    return button;
  }));
}

export function init(callbacks) {
  hooks = callbacks;
  $('sent-close').onclick = () => $('sent-dialog').close();
  $('sent-dialog').addEventListener('click', event => { if (event.target === $('sent-dialog')) $('sent-dialog').close(); });
  $('sent-term-form').onsubmit = async event => {
    event.preventDefault();
    const term = $('sent-term').value.trim();
    if (!term) return;
    try {
      const result = await hooks.api('/api/terms', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({term})});
      report(result.message, !result.added);
      if (result.added) $('sent-term').value = '';
    } catch (error) { report(error.message, true); }
  };
}

export async function open(sessionId, index) {
  current = {sessionId, index};
  report('');
  $('sent-body').replaceChildren(); $('sent-files').replaceChildren(); $('sent-public').replaceChildren();
  $('sent-intro').textContent = 'Loading…';
  $('sent-dialog').showModal();
  try {
    show(await hooks.api(`/api/sessions/${encodeURIComponent(sessionId)}/messages/${index}/sent`));
  } catch (error) { $('sent-intro').textContent = error.message; }
}
