import { marked } from './vendor/marked.js';
const $ = id => document.getElementById(id);
let library = {revision: 0, personas: []}, current = null, busy = false, editing = null, editRevision = 0;
const persona = () => library.personas.find(p => p.id === $('persona-select').value);
const node = (tag, text) => { const el = document.createElement(tag); el.textContent = text; return el; };
async function api(path, method = 'GET', body) {
  const response = await fetch(path, {method, ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})});
  const result = await response.json(); if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Request failed. Check the input and reload if it changed.'); return result;
}
function markdown(element, value) {
  element.className = 'markdown'; element.replaceChildren(DOMPurify.sanitize(marked.parse(value, {gfm: true, breaks: true}), {RETURN_DOM_FRAGMENT: true, ALLOWED_TAGS: ['p','br','strong','em','ul','ol','li','h1','h2','h3','h4','blockquote','pre','code','a','table','thead','tbody','tr','th','td'], ALLOWED_ATTR: ['href','title']}));
  for (const link of element.querySelectorAll('a')) { if (!/^(https?:\/\/|\/\?session=)/i.test(link.getAttribute('href') || '')) link.removeAttribute('href'); link.rel = 'noreferrer noopener'; }
}
function render() {
  $('adviser-title').textContent = current?.title || 'New conversation'; $('adviser-messages').replaceChildren();
  for (const message of current?.messages || []) {
    const article = document.createElement('article'); article.className = 'adviser-message'; article.append(node('h3', message.role === 'user' ? 'You' : 'Adviser recommendation'));
    const body = document.createElement('div'); markdown(body, message.content); article.append(body);
    if (message.sources?.length) {
      const details = document.createElement('details'); details.append(node('summary', 'Supporting sources (historical references, not independent verification)'));
      for (const id of new Set(message.sources.map(s => s.meeting_id))) { const link = node('a', id); link.href = `/?session=${encodeURIComponent(id)}`; details.append(link, document.createElement('br')); } article.append(details);
    }
    if (message.persona_revision) article.append(node('p', `Persona revision ${message.persona_revision}`));
    $('adviser-messages').append(article);
  }
  if (!current) $('adviser-messages').append(node('p', 'Select a persona and ask a question. No conversation is saved until a reply succeeds.'));
  $('adviser-send').disabled = busy || !persona();
}
function selected() { $('persona-description').textContent = persona()?.description || 'Archived or unavailable persona. Select another for a new conversation.'; $('edit-persona').disabled = !persona() || busy; render(); }
async function sessions() {
  const rows = await api('/api/adviser/sessions'); $('adviser-sessions').replaceChildren();
  rows.forEach(row => { const button = node('button', `${row.persona_name} · ${row.title}`); button.disabled = busy;
    button.onclick = async () => { if (busy) return; try {
      current = await api(`/api/adviser/sessions/${encodeURIComponent(row.id)}`);
      if (!library.personas.some(p => p.id === current.persona_id) && ![...$('persona-select').options].some(o => o.value === current.persona_id)) $('persona-select').add(new Option(`${current.persona_name} (archived)`, current.persona_id));
      $('persona-select').value = current.persona_id; selected();
    } catch (error) { $('adviser-status').textContent = error.message; } };
    $('adviser-sessions').append(button);
  });
}
async function loadLibrary(preferred) {
  const prior = preferred || $('persona-select').value; library = await api('/api/personas'); $('persona-select').replaceChildren();
  library.personas.forEach(p => $('persona-select').add(new Option(p.name, p.id)));
  if (library.personas.some(p => p.id === prior)) $('persona-select').value = prior;
  if (current && current.persona_id !== $('persona-select').value) current = null;
  selected(); await sessions();
}
$('persona-select').onchange = () => { if (!busy) { current = null; selected(); } };
$('new-adviser-chat').onclick = () => { if (!busy) { current = null; $('adviser-status').textContent = ''; render(); $('adviser-message').focus(); } };
$('adviser-composer').onsubmit = async event => {
  event.preventDefault(); const message = $('adviser-message').value.trim(); if (busy || !message || !persona()) return;
  busy = true; $('persona-select').disabled = true; $('adviser-send').disabled = true; $('adviser-status').textContent = 'Protecting the request…';
  try { current = await api('/api/adviser/chat', 'POST', {persona_id: persona().id, session_id: current?.id || null, expected_revision: current?.revision || 0, message}); $('adviser-message').value = ''; $('adviser-status').textContent = ''; render(); }
  catch (error) { $('adviser-status').textContent = error.message; }
  finally { busy = false; $('persona-select').disabled = false; render(); await sessions().catch(error => $('adviser-status').textContent = error.message); }
};
function openDefinition(value = null) {
  if (busy) return; editing = value?.id || null; editRevision = library.revision; const form = $('persona-form'); form.reset();
  for (const field of ['name','description','filename','text']) form.elements[field].value = value?.[field] || '';
  $('persona-edit-status').textContent = ''; $('persona-archive').hidden = !editing; $('persona-dialog').showModal(); form.elements.name.focus();
}
$('edit-persona').onclick = () => openDefinition(persona()); $('create-persona').onclick = () => openDefinition();
$('persona-upload').onchange = async event => {
  const file = event.target.files[0]; if (!file || busy) return;
  try { if (file.size > 20000 || !/\.(md|txt)$/i.test(file.name)) throw new Error('Choose a .md/.txt file of at most 20,000 bytes.');
    const text = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer()); openDefinition({name: file.name.replace(/\.(md|txt)$/i, ''), filename: file.name, text});
  } catch (error) { $('library-status').textContent = error.message; } finally { event.target.value = ''; }
};
$('persona-form').onsubmit = async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; const body = {expected_revision: editRevision};
  for (const field of ['name','description','filename','text']) body[field] = event.target.elements[field].value;
  try { const result = await api(`/api/personas${editing ? '/' + encodeURIComponent(editing) : ''}`, editing ? 'PUT' : 'POST', body); $('persona-dialog').close(); await loadLibrary(result.persona.id); }
  catch (error) { $('persona-edit-status').textContent = error.message; } finally { button.disabled = false; }
};
$('persona-cancel').onclick = () => $('persona-dialog').close();
$('persona-archive').onclick = async () => {
  if (!editing || !confirm('Archive this persona? Existing conversations remain readable.')) return;
  try { await api(`/api/personas/${encodeURIComponent(editing)}?expected_revision=${editRevision}`, 'DELETE'); $('persona-dialog').close(); await loadLibrary(); }
  catch (error) { $('persona-edit-status').textContent = error.message; }
};
loadLibrary().catch(error => $('library-status').textContent = error.message);
