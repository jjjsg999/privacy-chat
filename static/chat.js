import { marked } from './vendor/marked.js';
import { splitArtifacts, collectArtifacts, createPane, artifactLabel, artifactsToMarkdown, COMPACT_REQUEST } from './artifacts.js';

function renderMarkdown(element, text) {
  element.classList.add('markdown');
  element.replaceChildren(DOMPurify.sanitize(marked.parse(String(text || ''), {gfm:true, breaks:true}), {
    ALLOWED_TAGS: ['p','br','h1','h2','h3','h4','h5','h6','strong','em','del','ul','ol','li','blockquote','pre','code','hr','a','table','thead','tbody','tr','th','td'],
    ALLOWED_ATTR: ['href','title','start'],
    RETURN_DOM_FRAGMENT: true
  }));
  for (const link of element.querySelectorAll('a')) {
    const href = link.getAttribute('href') || '';
    if (!/^(https?:\/\/|mailto:)/i.test(href)) link.removeAttribute('href');
    link.rel = 'noreferrer noopener';
  }
  for (const table of element.querySelectorAll('table')) {
    const wrapper = document.createElement('div');
    wrapper.className = 'table-scroll'; wrapper.tabIndex = 0;
    wrapper.setAttribute('role', 'region'); wrapper.setAttribute('aria-label', 'Scrollable table');
    table.replaceWith(wrapper); wrapper.append(table);
  }
  for (const pre of element.querySelectorAll('pre')) {  // one Copy button per code block
    const wrapper = document.createElement('div'); wrapper.className = 'code-block';
    const button = document.createElement('button'); button.type = 'button'; button.className = 'copy-code';
    button.textContent = 'Copy'; button.setAttribute('aria-label', 'Copy code');
    button.onclick = () => copyText((pre.querySelector('code') || pre).textContent, button);
    pre.replaceWith(wrapper); wrapper.append(button, pre);
  }
}

// Copies text; briefly relabels the button (or reports through the status line when there is none).
async function copyText(text, button) {
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; }
  catch (_) {
    const area = document.createElement('textarea'); area.value = text; area.className = 'sr-only';
    document.body.append(area); area.select();
    try { ok = document.execCommand('copy'); } catch (_) { /* no clipboard access */ }
    area.remove();
  }
  if (button) {
    button.dataset.label = button.dataset.label || button.textContent;
    button.textContent = ok ? 'Copied' : 'Copy failed';
    setTimeout(() => { button.textContent = button.dataset.label; }, 1500);
  } else status(ok ? 'Copied to the clipboard.' : 'Could not copy. Select the text and copy it manually.', !ok);
  return ok;
}

const $ = id => document.getElementById(id);
let current = null;
let busy = false;
const welcome = $('messages').firstElementChild.cloneNode(true);
const pane = createPane({getMessages: () => current?.messages || [], copyText, status,
  askFix: text => {
    if (busy) { status('Wait for the current reply to finish first.', true); return; }
    $('message').value = text; $('composer').requestSubmit();
  }});
// An assistant reply: Markdown text, with each <artifact> block shown as a card that opens the side pane.
function renderMessageBody(body, text, registry, messageIndex) {
  const parts = splitArtifacts(text);
  if (!parts.some(part => part.kind === 'artifact')) { renderMarkdown(body, text); return; }
  body.classList.add('markdown'); body.replaceChildren();
  for (const part of parts) {
    if (part.kind === 'text') { const block = document.createElement('div'); renderMarkdown(block, part.text); body.append(block); continue; }
    const versions = registry?.get(part.id)?.versions || [];
    const index = versions.findIndex(v => v.messageIndex === messageIndex && v.content === part.content);
    const card = document.createElement('button'); card.type = 'button'; card.className = 'artifact-card';
    const title = document.createElement('strong'); title.textContent = part.title;
    const meta = document.createElement('small');
    meta.textContent = [artifactLabel(part.type), versions.length > 1 && index >= 0 ? `version ${index + 1}` : '',
      registry ? (part.complete ? 'click to open' : 'incomplete') : (part.complete ? 'finishing…' : 'writing…')].filter(Boolean).join(' · ');
    if (index >= 0) card.onclick = () => pane.open(part.id, index, current?.id);
    else card.disabled = true;  // still streaming: it opens once the reply is saved
    card.append(title, meta); body.append(card);
  }
}
async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check your input and retry.');
  return data;
}
function status(text, error = false) { $('status').textContent = text; $('status').classList.toggle('error', error); }
function render(session) {
  current = session;
  $('chat-title').textContent = session?.title || 'New conversation';
  $('messages').replaceChildren();
  if (!session) $('messages').append(welcome.cloneNode(true));
  if (session?.session_type === 'meeting') {
    const heading = document.createElement('h2'); heading.className = 'history-heading'; heading.textContent = 'Full conversation history'; $('messages').append(heading);
  }
  const registry = session && session.session_type !== 'meeting' ? collectArtifacts(session.messages) : new Map();
  for (const [index, message] of (session?.messages || []).entries()) {
    const article = document.createElement('article');
    article.className = `message ${message.role}`;
    const label = document.createElement('span'); label.className = 'speaker';
    label.textContent = message.role === 'user' ? 'You' : 'Assistant';
    const body = document.createElement('div'); body.className = 'body'; body.textContent = message.content;
    if (message.role === 'assistant') renderMessageBody(body, message.content, registry, index);
    article.append(label);
    if (message.role === 'assistant' && (message.model || message.stopped)) {
      const note = document.createElement('small'); note.className = 'model-label';
      note.textContent = [message.model, message.stopped ? 'stopped early' : ''].filter(Boolean).join(' · ');
      body.append(note);
    }
    if (session.session_type === 'meeting' && index === 0 && message.role === 'user') {
      const transcript = document.createElement('details'); transcript.className = 'meeting-panel';
      const summary = document.createElement('summary'); summary.textContent = 'Full transcript';
      transcript.append(summary);
      if (session.chat_diagnosis) {
        const original = document.createElement('h3'); original.textContent = 'Original transcript (sensitive terms restored)';
        transcript.append(original, body);
        const sent = document.createElement('h3'); sent.textContent = 'Text sent to LLM API — initial meeting analysis';
        transcript.append(sent);
        const request = session.meeting_api_request;
        if (request) {
          const metadata = document.createElement('p'); metadata.textContent = `Model: ${request.model} · Captured: ${new Date(request.captured_at).toLocaleString()}`;
          transcript.append(metadata);
          for (const message of request.messages) {
            const label = document.createElement('h4'); label.textContent = message.role;
            const text = document.createElement('div'); text.className = 'api-text'; text.textContent = message.content;
            transcript.append(label, text);
          }
        } else {
          const notice = document.createElement('p'); notice.textContent = 'No API snapshot was recorded for this meeting. Enable diagnosis before processing a new meeting. Historical requests cannot be reconstructed exactly.';
          transcript.append(notice);
        }
      } else transcript.append(body);
      article.append(transcript);
    } else {
      article.append(body);
    }
    if (message.role === 'user' && message.attachments?.length) {
      const typed = message.typed || '';
      body.textContent = typed; body.hidden = !typed;
      const details = document.createElement('details'); details.className = 'meeting-panel attachment-text';
      const summary = document.createElement('summary');
      summary.textContent = `Attached: ${message.attachments.map(file => file.name).join(', ')} (view extracted text)`;
      const text = document.createElement('div'); text.className = 'api-text'; text.textContent = message.content.slice(typed.length).trim();
      details.append(summary, text); article.append(details);
    }
    if (message.role === 'assistant' && message.truncated) {  // the provider stopped at the output limit
      const note = document.createElement('p'); note.className = 'truncated-note';
      note.append('This reply hit the length limit and was cut off. ');
      if (session.id && index === session.messages.length - 1 && session.session_type !== 'meeting') {
        const again = document.createElement('button'); again.type = 'button'; again.textContent = 'Regenerate compactly';
        again.onclick = () => {
          if (busy) { status('Wait for the current reply to finish first.', true); return; }
          $('message').value = COMPACT_REQUEST; $('composer').requestSubmit();
        };
        note.append(again);
      }
      article.append(note);
    }
    const actions = messageActions(session, message, index, article);
    if (actions) article.append(actions);
    $('messages').append(article);
  }
  $('messages').scrollTop = $('messages').scrollHeight;
  $('export').hidden = !session?.messages?.length;
  pane.sync(session?.id);
}
// Copy on every message; Edit on your messages and Branch on replies, for normal saved chats only.
function messageActions(session, message, index, article) {
  if (!message.content || (session.session_type === 'meeting' && index < 2)) return null;
  const bar = document.createElement('div'); bar.className = 'message-actions';
  const add = (label, title, handler) => {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.title = title;
    button.setAttribute('aria-label', `${title}`); button.onclick = () => handler(button); bar.append(button);
  };
  const plain = message.role === 'user' && message.attachments?.length ? (message.typed || message.content) : message.content;
  add('Copy', 'Copy this message', button => copyText(plain, button));
  const chat = session.id && (session.session_type || 'chat') === 'chat';
  if (chat && message.role === 'user') add('Edit', 'Edit this message and resend', () => startEdit(article, message, index));
  if (chat && message.role === 'assistant') add('Branch', 'Branch into a new conversation from this reply', () => branchFrom(index));
  return bar;
}
function startEdit(article, message, index) {
  if (busy) { status('Wait for the current reply to finish before editing.', true); return; }
  const body = article.querySelector('.body'), bar = article.querySelector('.message-actions');
  const editor = document.createElement('div'); editor.className = 'editor';
  const area = document.createElement('textarea'); area.value = message.attachments?.length ? (message.typed || '') : message.content;
  area.setAttribute('aria-label', 'Edit your message'); area.maxLength = 50000;
  const row = document.createElement('div');
  const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel';
  const save = document.createElement('button'); save.type = 'button'; save.className = 'primary'; save.textContent = 'Save and resend';
  const close = () => { editor.remove(); body.hidden = !body.textContent && !!message.attachments?.length; if (bar) bar.hidden = false; };
  cancel.onclick = close;
  save.onclick = () => resendEdit(index, area.value);
  area.onkeydown = event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    else if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); save.click(); }
  };
  row.append(cancel, save); editor.append(area, row);
  body.hidden = true; if (bar) bar.hidden = true;
  article.insertBefore(editor, body.nextSibling || null);
  area.focus(); area.setSelectionRange(area.value.length, area.value.length);
}
function setBusy(on) {
  busy = on;
  for (const id of ['send', 'attach', 'new-chat', 'message']) $(id).disabled = on;
}
async function resendEdit(index, text) {
  if (busy || !current?.id) return;
  text = text.trim();
  if (!text && !current.messages[index]?.attachments?.length) { status('Please enter a message.', true); return; }
  setBusy(true);
  status('Protecting your message and waiting for the model…');
  try { await streamTurn(text, [], index); }
  catch (error) { status(error.message, true); }
  finally { setBusy(false); await refreshSessions().catch(error => status(error.message, true)); $('message').focus(); }
}
async function branchFrom(index) {
  if (busy) { status('Wait for the current reply to finish before branching.', true); return; }
  if (!current?.id) return;
  try {
    const branch = await api(`/api/sessions/${encodeURIComponent(current.id)}/branch`,
      {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message_index: index})});
    render(branch); closeSidebar();
    status('Branched into a new conversation. The original is unchanged.');
    await refreshSessions(); $('message').focus();
  } catch (error) { status(error.message, true); }
}
// Plain Markdown built from the loaded session. It contains real (unmasked) text, never token maps.
function exportMarkdown() {
  if (!current?.messages?.length) return;
  const lines = [`# ${current.title || 'Conversation'}`, '',
    `Exported ${new Date().toLocaleString()} · ${current.session_type === 'meeting' ? 'Meeting' : 'Chat'} · ${current.channel_origin === 'telegram' ? 'Telegram' : 'Web'}`, ''];
  for (const message of current.messages) {
    lines.push(`## ${message.role === 'user' ? 'You' : 'Assistant'}`, '', message.role === 'assistant' ? artifactsToMarkdown(message.content) : message.content, '');
    if (message.role === 'assistant' && (message.model || message.stopped)) lines.push(`*${[message.model, message.stopped ? 'stopped early' : ''].filter(Boolean).join(' · ')}*`, '');
  }
  const slug = (current.title || 'conversation').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'conversation';
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([lines.join('\n')], {type: 'text/markdown;charset=utf-8'}));
  link.download = `${slug}.md`; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
$('export').onclick = exportMarkdown;
let archivedOpen = false;
function closeRowMenus() { for (const menu of document.querySelectorAll('.row-menu')) menu.remove(); }
document.addEventListener('click', closeRowMenus);
async function refreshSessions() {
  const query = $('session-search').value.trim();
  const sessions = await api(query ? `/api/sessions?q=${encodeURIComponent(query)}` : '/api/sessions');
  $('sessions').replaceChildren();
  const makeRow = session => {
    const row = document.createElement('div'); row.className = 'session-row';
    const button = document.createElement('button'); button.textContent = session.title;
    button.className = 'session-open';
    button.classList.toggle('active', session.id === current?.id);
    const badge = document.createElement('small'); badge.textContent = `${session.pinned ? 'Pinned · ' : ''}${session.channel_origin === 'telegram' ? 'Telegram' : 'Web'} · ${new Date(session.updated_at).toLocaleString()}`;
    button.append(badge); button.disabled = busy;
    button.onclick = async () => {
      if (busy) return;
      try { render(await api(`/api/sessions/${encodeURIComponent(session.id)}`)); closeSidebar(); status(''); await refreshSessions(); }
      catch (error) { status(error.message, true); }
    };
    const rename = document.createElement('button'); rename.className = 'session-rename';
    rename.textContent = '✎'; rename.title = 'Rename conversation';
    rename.setAttribute('aria-label', `Rename conversation: ${session.title}`); rename.disabled = busy;
    rename.onclick = async () => {
      if (busy) return;
      const title = window.prompt('Rename conversation', session.title);
      if (title === null || !title.trim()) return;
      try {
        const updated = await api(`/api/sessions/${encodeURIComponent(session.id)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title: title.trim()})});
        if (current?.id === session.id) { current.title = updated.title; $('chat-title').textContent = updated.title; }
        status('');
      } catch (error) { status(error.message, true); }
      await refreshSessions().catch(error => status(error.message, true));
    };
    const remove = document.createElement('button'); remove.className = 'session-delete';
    remove.textContent = '×'; remove.title = 'Delete conversation';
    remove.setAttribute('aria-label', `Delete conversation: ${session.title}`); remove.disabled = busy;
    remove.onclick = async () => {
      if (busy || !window.confirm(`Delete "${session.title}"? This permanently removes the saved conversation, transcript and privacy token map from this app. Telegram messages are not deleted.`)) return;
      busy = true; $('send').disabled = true; $('new-chat').disabled = true;
      remove.disabled = true;
      try {
        await api(`/api/sessions/${encodeURIComponent(session.id)}`, {method:'DELETE'});
        if (current?.id === session.id) { render(null); $('message').value = ''; }
        status('Conversation deleted.');
      } catch (error) { status(error.message, true); }
      finally {
        busy = false; $('send').disabled = false; $('new-chat').disabled = false;
        await refreshSessions().catch(error => status(error.message, true));
      }
    };
    const menuButton = document.createElement('button'); menuButton.className = 'session-menu'; menuButton.type = 'button';
    menuButton.textContent = '⋯'; menuButton.title = 'Pin or archive';
    menuButton.setAttribute('aria-label', `Pin or archive: ${session.title}`); menuButton.setAttribute('aria-haspopup', 'true');
    menuButton.onclick = event => {
      event.stopPropagation();
      const existing = row.querySelector('.row-menu');
      closeRowMenus();
      if (existing) return;
      const menu = document.createElement('div'); menu.className = 'row-menu'; menu.setAttribute('role', 'menu');
      const item = (label, change) => {
        const entry = document.createElement('button'); entry.type = 'button'; entry.textContent = label; entry.setAttribute('role', 'menuitem');
        entry.onclick = async () => {
          closeRowMenus();
          try { await api(`/api/sessions/${encodeURIComponent(session.id)}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(change)}); status(''); }
          catch (error) { status(error.message, true); }
          await refreshSessions().catch(error => status(error.message, true));
        };
        menu.append(entry);
      };
      if (!session.archived) item(session.pinned ? 'Unpin' : 'Pin to top', {pinned: !session.pinned});
      item(session.archived ? 'Unarchive' : 'Archive', {archived: !session.archived});
      row.append(menu);
    };
    row.append(button, rename, menuButton, remove);
    return row;
  };
  // Pinned first, then everything else, then the archive (collapsed unless you are searching).
  const group = (list, label) => {
    if (!list.length) return;
    if (label) { const heading = document.createElement('p'); heading.className = 'group-label'; heading.textContent = label; $('sessions').append(heading); }
    for (const session of list) $('sessions').append(makeRow(session));
  };
  const live = sessions.filter(session => !session.archived), archived = sessions.filter(session => session.archived);
  const pinned = live.filter(session => session.pinned);
  group(pinned, 'Pinned');
  group(live.filter(session => !session.pinned), pinned.length ? 'Recent' : '');
  if (archived.length) {
    const details = document.createElement('details'); details.className = 'archived-group';
    details.open = archivedOpen || Boolean(query);
    details.ontoggle = () => { if (!query) archivedOpen = details.open; };
    const summary = document.createElement('summary'); summary.textContent = `Archived (${archived.length})`;
    details.append(summary); for (const session of archived) details.append(makeRow(session));
    $('sessions').append(details);
  }
  if (!sessions.length && query) { const empty = document.createElement('p'); empty.className = 'hint'; empty.textContent = 'No matching conversations.'; $('sessions').append(empty); }
}
let searchTimer = 0;
$('session-search').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => refreshSessions().catch(error => status(error.message, true)), 250); };
function closeSidebar() { $('sidebar').classList.remove('open'); $('toggle-sidebar').setAttribute('aria-expanded', 'false'); }
$('toggle-sidebar').onclick = () => { const open = $('sidebar').classList.toggle('open'); $('toggle-sidebar').setAttribute('aria-expanded', String(open)); };
$('new-chat').onclick = () => { if (busy) return; render(null); $('message').value = ''; attachments = []; renderAttachments(); status(''); closeSidebar(); refreshSessions().catch(error => status(error.message, true)); $('message').focus(); };
let controller = null;
$('stop').onclick = () => controller?.abort();
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
// Streams one turn (NDJSON events). Returns {saved, error}; the textarea is cleared only when the server kept the message.
async function streamTurn(message, files = [], editIndex = null) {
  const previous = current;
  controller = new AbortController();
  // An edit replaces one user message and drops everything after it; the file text of that message is kept.
  const replaced = editIndex === null ? null : previous.messages[editIndex];
  const kept = replaced?.attachments?.length ? replaced.content.slice((replaced.typed || '').length).trim() : '';
  const userMessage = replaced
    ? {role: 'user', content: [message, kept].filter(Boolean).join('\n\n'), ...(replaced.attachments?.length ? {typed: message, attachments: replaced.attachments} : {})}
    : {role: 'user', content: [message, ...files.map(f => `[Attached file: ${f.name}]\n${f.text.trim()}\n[End of attached file]`)].filter(Boolean).join('\n\n'),
       typed: message, attachments: files.map(f => ({name: f.name, kind: f.kind, chars: f.text.length}))};
  render({...(previous || {title: 'New conversation', messages: []}),
    messages: [...(previous?.messages || []).slice(0, editIndex === null ? undefined : editIndex), userMessage, {role: 'assistant', content: ''}]});
  current = previous;
  const body = $('messages').lastElementChild.querySelector('.body');
  let text = '', frame = 0, sessionId = previous?.id || null, final = null, failure = null, aborted = false;
  const paint = () => {
    frame = 0; renderMessageBody(body, text, null, -1);
    const writing = splitArtifacts(text).filter(part => part.kind === 'artifact').pop();
    if (writing) pane.live(writing);  // show the artifact's source in the pane while it is written
 $('messages').scrollTop = $('messages').scrollHeight; };
  $('send').hidden = true; $('stop').hidden = false;
  try {
    const response = await fetch('/api/chat/stream', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: previous?.id || null, message, attachments: files, ...(editIndex === null ? {} : {edit_index: editIndex})}),
      signal: controller.signal});
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check your input and retry.');
    }
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '';
    const handle = event => {
      if (event.type === 'start') sessionId = event.session_id;
      else if (event.type === 'delta') { if (!text) status(''); text += event.text; if (!frame) frame = requestAnimationFrame(paint); }
      else if (event.type === 'done') final = event.session;
      else if (event.type === 'title' && final) final.title = event.title;
      else if (event.type === 'error') failure = event.message;
    };
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end).trim(); buffer = buffer.slice(end + 1);
        if (line) handle(JSON.parse(line));
      }
    }
  } catch (error) {
    if (error.name === 'AbortError') aborted = true; else failure = failure || error.message;
  } finally {
    if (frame) cancelAnimationFrame(frame);
    $('stop').hidden = true; $('send').hidden = false; controller = null;
    pane.endLive();  // the streaming source view gives way to the saved result (or restores the earlier pane)
  }
  if (final) {
    render(final); status('');
    // A reply that produced an artifact opens it in the pane (the latest one if there are several).
    const made = splitArtifacts(final.messages[final.messages.length - 1]?.content).filter(part => part.kind === 'artifact').pop();
    if (made) pane.open(made.id, -1, final.id);
    return {saved: true};
  }
  // Aborted or failed: the server saves any partial reply, which can land just after the disconnect.
  let saved = null;
  if (sessionId && text.trim()) {
    for (let attempt = 0; attempt < 8 && !saved; attempt++) {
      try {
        const session = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
        // Saving always refreshes updated_at; a brand-new conversation counts once it has messages.
        if (previous ? session.updated_at !== previous.updated_at : session.messages.length > 0) saved = session;
      } catch (_) {}
      if (!saved) await sleep(300);
    }
  }
  if (saved) render(saved); else render(previous);
  status(failure ? failure : (saved ? 'Stopped. The partial reply was saved.' : 'Stopped.'), !!failure);
  return {saved: !!saved};
}
let attachments = [];
let uploadLimits = {max_attachments: 5};
function renderAttachments() {
  $('attachments').replaceChildren();
  for (const [index, item] of attachments.entries()) {
    const card = document.createElement('div'); card.className = 'attachment';
    const head = document.createElement('div'); head.className = 'attachment-head';
    const name = document.createElement('strong'); name.textContent = item.name;
    const meta = document.createElement('small');
    const count = () => `${item.text.length.toLocaleString()} characters${item.kind === 'ocr' ? ' · read with OCR' : ''}${item.pages > 1 ? ` · ${item.pages} pages` : ''}`;
    meta.textContent = count();
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×';
    remove.title = 'Remove attachment'; remove.setAttribute('aria-label', `Remove attachment: ${item.name}`);
    remove.onclick = () => { attachments.splice(index, 1); renderAttachments(); };
    head.append(name, meta, remove); card.append(head);
    for (const warning of item.warnings || []) { const note = document.createElement('p'); note.className = 'hint'; note.textContent = warning; card.append(note); }
    if (item.review) {
      const details = document.createElement('details'); details.open = true;
      const summary = document.createElement('summary'); summary.textContent = 'Review extracted text (editable, this is what will be sent)';
      const area = document.createElement('textarea'); area.rows = 6; area.value = item.text; area.spellcheck = false;
      area.setAttribute('aria-label', `Extracted text from ${item.name}`);
      area.oninput = () => { item.text = area.value; meta.textContent = count(); };
      details.append(summary, area); card.append(details);
    }
    $('attachments').append(card);
  }
}
$('attach').onclick = () => { if (!busy) $('file-input').click(); };
$('file-input').onchange = () => { const picked = [...$('file-input').files]; $('file-input').value = ''; return addFiles(picked); };
// Paste (e.g. a screenshot) and drag-and-drop feed the same extraction path as the Attach button.
$('message').addEventListener('paste', event => {
  const files = [...(event.clipboardData?.files || [])];
  if (!files.length) return;  // plain text pastes are left alone
  event.preventDefault();
  const stamp = new Date().toISOString().slice(0, 19).replace(/\D/g, '');
  addFiles(files.map((file, index) => file.name && file.name !== 'image.png' ? file
    : new File([file], `pasted-${stamp}${files.length > 1 ? `-${index + 1}` : ''}.${(file.type.split('/')[1] || 'png').replace('jpeg', 'jpg')}`, {type: file.type})));
});
const hasFiles = event => [...(event.dataTransfer?.types || [])].includes('Files');
let dragDepth = 0;
document.addEventListener('dragenter', event => { if (hasFiles(event)) { dragDepth += 1; $('composer').classList.add('drop-target'); } });
document.addEventListener('dragleave', event => { if (hasFiles(event) && --dragDepth <= 0) { dragDepth = 0; $('composer').classList.remove('drop-target'); } });
document.addEventListener('dragover', event => { if (hasFiles(event)) event.preventDefault(); });  // required for drop to fire
document.addEventListener('drop', event => {
  if (!hasFiles(event)) return;
  event.preventDefault(); dragDepth = 0; $('composer').classList.remove('drop-target');
  addFiles([...event.dataTransfer.files]);
});
async function addFiles(picked) {
  if (!picked.length) return;
  if (busy) { status('Wait for the current reply to finish before attaching files.', true); return; }
  if (attachments.length + picked.length > uploadLimits.max_attachments) { status(`You can attach up to ${uploadLimits.max_attachments} files per message.`, true); return; }
  busy = true; $('send').disabled = true; $('attach').disabled = true; $('new-chat').disabled = true;
  status('Reading files on this computer. Scans can take a few seconds per page…');
  try {
    const form = new FormData(); for (const file of picked) form.append('files', file);
    const data = await api('/api/upload', {method: 'POST', body: form});
    uploadLimits = {...uploadLimits, ...data.limits}; attachments.push(...data.files); renderAttachments();
    status(data.files.some(file => file.review) ? 'Review the extracted text below, then press Send.' : '');
  } catch (error) { status(error.message, true); }
  finally { busy = false; $('send').disabled = false; $('attach').disabled = false; $('new-chat').disabled = false; }
}
$('composer').onsubmit = async event => {
  event.preventDefault();
  const message = $('message').value.trim();
  const files = attachments.filter(item => item.text.trim()).map(({name, kind, text}) => ({name, kind, text}));
  if ((!message && !files.length) || busy) return;
  busy = true; $('send').disabled = true; $('attach').disabled = true; $('new-chat').disabled = true; $('message').disabled = true;
  status('Protecting your message and waiting for the model…');
  try {
    const result = await streamTurn(message, files);
    if (result.saved) { $('message').value = ''; attachments = []; renderAttachments(); }
  } catch (error) { status(error.message, true); }
  finally {
    busy = false; $('send').disabled = false; $('attach').disabled = false; $('new-chat').disabled = false; $('message').disabled = false;
    await refreshSessions().catch(error => status(error.message, true)); $('message').focus();
  }
};
$('message').onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && window.matchMedia('(min-width:760px)').matches) { event.preventDefault(); $('composer').requestSubmit(); } };
refreshSessions().catch(error => status(error.message, true));
const linkedSession = new URLSearchParams(location.search).get('session');
if (linkedSession) api(`/api/sessions/${encodeURIComponent(linkedSession)}`).then(session => {
  render(session); const transcript = document.querySelector('.meeting-panel');
  if (transcript) { transcript.open = true; transcript.scrollIntoView(); }
}).catch(error => status(error.message, true));
// Keyboard shortcuts (Enter / Shift+Enter in the message box are handled above). Listed in the ⌨ dialog.
$('show-shortcuts').onclick = () => $('shortcuts').showModal();
document.addEventListener('keydown', event => {
  if ($('shortcuts').open || event.defaultPrevented) return;
  const target = event.target, typing = target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
  const mod = event.ctrlKey || event.metaKey;
  if (event.key === 'Escape' && controller) { event.preventDefault(); controller.abort(); }
  else if (event.key === 'Escape' && pane.isOpen()) { event.preventDefault(); pane.close(); }
  else if (mod && event.shiftKey && event.code === 'KeyO') { event.preventDefault(); $('new-chat').click(); }
  else if (mod && !event.shiftKey && event.code === 'KeyK') {
    event.preventDefault(); $('sidebar').classList.add('open'); $('toggle-sidebar').setAttribute('aria-expanded', 'true');
    $('session-search').focus(); $('session-search').select();
  } else if (event.altKey && !mod && event.code === 'KeyC') {
    const last = [...(current?.messages || [])].reverse().find(message => message.role === 'assistant' && message.content);
    if (last) { event.preventDefault(); copyText(last.content, null); }
  } else if (!typing && !mod && !event.altKey && event.key === '/') { event.preventDefault(); $('message').focus(); }
  else if (!typing && !mod && event.key === '?') { event.preventDefault(); $('shortcuts').showModal(); }
});
setInterval(() => { if (!busy && !document.hidden) refreshSessions().catch(() => {}); }, 10000);
window.addEventListener('focus', () => { if (!busy) refreshSessions().catch(() => {}); });
