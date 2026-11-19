import { marked } from './vendor/marked.js';

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
}

const $ = id => document.getElementById(id);
let current = null;
let busy = false;
const welcome = $('messages').firstElementChild.cloneNode(true);
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
  for (const [index, message] of (session?.messages || []).entries()) {
    const article = document.createElement('article');
    article.className = `message ${message.role}`;
    const label = document.createElement('span'); label.className = 'speaker';
    label.textContent = message.role === 'user' ? 'You' : 'Assistant';
    const body = document.createElement('div'); body.className = 'body'; body.textContent = message.content;
    if (message.role === 'assistant') renderMarkdown(body, message.content);
    article.append(label);
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
    $('messages').append(article);
  }
  $('messages').scrollTop = $('messages').scrollHeight;
}
async function refreshSessions() {
  const sessions = await api('/api/sessions');
  $('sessions').replaceChildren();
  for (const session of sessions) {
    const row = document.createElement('div'); row.className = 'session-row';
    const button = document.createElement('button'); button.textContent = session.title;
    button.className = 'session-open';
    button.classList.toggle('active', session.id === current?.id);
    const badge = document.createElement('small'); badge.textContent = `${session.channel_origin === 'telegram' ? 'Telegram' : 'Web'} · ${new Date(session.updated_at).toLocaleString()}`;
    button.append(badge); button.disabled = busy;
    button.onclick = async () => {
      if (busy) return;
      try { render(await api(`/api/sessions/${encodeURIComponent(session.id)}`)); closeSidebar(); status(''); await refreshSessions(); }
      catch (error) { status(error.message, true); }
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
    row.append(button, remove); $('sessions').append(row);
  }
}
function closeSidebar() { $('sidebar').classList.remove('open'); $('toggle-sidebar').setAttribute('aria-expanded', 'false'); }
$('toggle-sidebar').onclick = () => { const open = $('sidebar').classList.toggle('open'); $('toggle-sidebar').setAttribute('aria-expanded', String(open)); };
$('new-chat').onclick = () => { if (busy) return; render(null); $('message').value = ''; status(''); closeSidebar(); refreshSessions().catch(error => status(error.message, true)); $('message').focus(); };
$('composer').onsubmit = async event => {
  event.preventDefault();
  const message = $('message').value.trim(); if (!message || busy) return;
  busy = true; $('send').disabled = true; $('new-chat').disabled = true; $('message').disabled = true;
  status('Protecting your message and waiting for the model…');
  try {
    const session = await api('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({session_id:current?.id || null, message})});
    render(session); $('message').value = ''; status('');
  } catch (error) { status(error.message, true); }
  finally {
    busy = false; $('send').disabled = false; $('new-chat').disabled = false; $('message').disabled = false;
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
setInterval(() => { if (!busy && !document.hidden) refreshSessions().catch(() => {}); }, 10000);
window.addEventListener('focus', () => { if (!busy) refreshSessions().catch(() => {}); });
