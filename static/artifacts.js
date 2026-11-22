// Artifacts: standalone content (pages, diagrams, documents, code) that the model wraps in
//   <artifact type="html" title="..." id="..."> ... </artifact>
// This module finds those blocks in a reply and shows them in a side pane. HTML and SVG run inside a sandboxed
// iframe served from /artifact-frame (no network, no access to this app); see app.py for the policy.
import { marked } from './vendor/marked.js';

const TYPES = ['html', 'svg', 'markdown', 'mermaid', 'code'];
const LABELS = {html: 'Web page', svg: 'SVG image', markdown: 'Document', mermaid: 'Diagram', code: 'Code'};
const EXTENSIONS = {python: 'py', javascript: 'js', typescript: 'ts', json: 'json', css: 'css', html: 'html', bash: 'sh', shell: 'sh',
  sql: 'sql', java: 'java', c: 'c', cpp: 'cpp', csharp: 'cs', go: 'go', rust: 'rs', ruby: 'rb', php: 'php', yaml: 'yml', xml: 'xml', markdown: 'md'};
const OPEN = /^[ \t]*<artifact\b([^>\n]*)>[ \t]*$/m;
const CLOSE = /^[ \t]*<\/artifact>[ \t]*$/m;
const PARTIAL_TAG = /(?:^|\n)[ \t]*<artifact\b[^>\n]*$/;  // an opening tag still being streamed

const slug = text => String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);
function attributes(raw) {
  const found = {};
  for (const match of raw.matchAll(/(\w+)\s*=\s*"([^"]*)"/g)) found[match[1].toLowerCase()] = match[2];
  return found;
}
function unfence(content, type) {  // models sometimes fence an artifact despite the instructions
  if (!['html', 'svg', 'mermaid'].includes(type)) return content;
  const match = content.match(/^\s*```[\w-]*\n([\s\S]*?)\n```\s*$/);
  return match ? match[1] : content;
}

// Splits reply text into {kind:'text'} and {kind:'artifact'} parts. An artifact with no closing tag is incomplete.
export function splitArtifacts(text) {
  const parts = [];
  let rest = String(text || '');
  for (;;) {
    const open = OPEN.exec(rest);
    if (!open) break;
    if (open.index > 0) parts.push({kind: 'text', text: rest.slice(0, open.index)});
    let after = rest.slice(open.index + open[0].length);
    if (after.startsWith('\r\n')) after = after.slice(2); else if (after.startsWith('\n')) after = after.slice(1);
    const close = CLOSE.exec(after);
    const attrs = attributes(open[1]);
    const type = TYPES.includes((attrs.type || '').toLowerCase()) ? attrs.type.toLowerCase() : 'code';
    const title = (attrs.title || '').trim() || 'Untitled';
    const body = (close ? after.slice(0, close.index) : after).replace(/\r?\n$/, '');
    parts.push({kind: 'artifact', type, title, id: slug(attrs.id) || slug(title) || 'artifact', language: (attrs.language || '').toLowerCase(),
      content: unfence(body, type), complete: Boolean(close)});
    if (!close) { rest = ''; break; }
    rest = after.slice(close.index + close[0].length).replace(/^\r?\n/, '');
  }
  if (rest) parts.push({kind: 'text', text: rest});
  const last = parts[parts.length - 1];
  if (last?.kind === 'text') last.text = last.text.replace(PARTIAL_TAG, '');
  return parts.filter(part => part.kind !== 'text' || part.text.trim());
}

// id -> {id, versions: [artifact parts in message order, each with messageIndex]}. Re-using an id makes a new version.
export function collectArtifacts(messages) {
  const byId = new Map();
  (messages || []).forEach((message, index) => {
    if (message.role !== 'assistant') return;
    for (const part of splitArtifacts(message.content)) {
      if (part.kind !== 'artifact') continue;
      const entry = byId.get(part.id) || {id: part.id, versions: []};
      entry.versions.push({...part, messageIndex: index});
      byId.set(part.id, entry);
    }
  });
  return byId;
}
export const artifactLabel = type => LABELS[type] || 'Artifact';

const escapeHtml = text => String(text).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const NO_SCRIPTS = '<meta http-equiv="Content-Security-Policy" content="script-src \'none\'">';
function intoHead(html, meta) {  // the policy must come before any script, and must not push a doctype into quirks mode
  if (/<head[^>]*>/i.test(html)) return html.replace(/<head[^>]*>/i, match => match + meta);
  if (/<html[^>]*>/i.test(html)) return html.replace(/<html[^>]*>/i, match => match + '<head>' + meta + '</head>');
  if (/^\s*<!doctype[^>]*>/i.test(html)) return html.replace(/^\s*<!doctype[^>]*>/i, match => match + meta);
  return meta + html;
}
const PAGE_HEAD = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">';

// The complete document written into the sandboxed frame.
function frameDocument(artifact, scripts) {
  const guard = scripts ? '' : NO_SCRIPTS;
  if (artifact.type === 'html') {
    const full = /<html[\s>]|^\s*<!doctype/i.test(artifact.content);
    return guard ? intoHead(full ? artifact.content : PAGE_HEAD + artifact.content, guard) : (full ? artifact.content : PAGE_HEAD + artifact.content);
  }
  if (artifact.type === 'svg') {
    return PAGE_HEAD + guard + '<style>html,body{margin:0;background:#fff}svg{display:block;max-width:100%;max-height:100vh;margin:auto}</style>' + artifact.content;
  }
  if (artifact.type === 'markdown') {
    const html = window.DOMPurify.sanitize(marked.parse(artifact.content, {gfm: true, breaks: false}), {FORBID_TAGS: ['style', 'form', 'input', 'button']});
    return PAGE_HEAD + NO_SCRIPTS + '<style>body{font:15px/1.65 system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 18px;color:#243d38;background:#fff}' +
      'pre,code{font-family:ui-monospace,Consolas,monospace;background:#f1f2ec;border-radius:4px}pre{padding:12px;overflow:auto}code{padding:1px 4px}pre code{padding:0}' +
      'table{border-collapse:collapse}td,th{border:1px solid #dedfd7;padding:6px 10px}blockquote{border-left:3px solid #9cbfad;margin-left:0;padding-left:14px;color:#6d7770}img{max-width:100%}</style>' + html;
  }
  if (artifact.type === 'mermaid') {
    const head = PAGE_HEAD + '<style>body{margin:0;padding:16px;background:#fff}pre.mermaid{margin:0;text-align:center}</style>';
    if (!mermaidSource) return head + '<p>The Mermaid library is not installed (static/vendor/mermaid.min.js).</p>';
    // The library is inlined: the sandboxed frame has no network and its policy allows no external scripts.
    return head + `<pre class="mermaid">${escapeHtml(artifact.content)}</pre><script>${mermaidSource}</script>` +
      '<script>if(window.mermaid){mermaid.initialize({startOnLoad:false,securityLevel:"strict"});mermaid.run({querySelector:".mermaid"}).catch(function(e){' +
      'document.body.insertAdjacentHTML("beforeend","<p style=\\"color:#a13c2c\\">Diagram error: "+String((e&&e.message)||e).replace(/</g,"&lt;")+"</p>")})}</script>';
  }
  return '';
}

// Mermaid's source text, fetched once from this app and placed inside the frame document. '' means it is not installed.
let mermaidSource = null;
async function loadMermaid() {
  if (mermaidSource !== null) return;
  try {
    const response = await fetch('/static/vendor/mermaid.min.js');
    if (!response.ok) throw new Error('missing');
    mermaidSource = (await response.text()).replace(/<\/script/gi, '<\\/script');  // keep the inlined text from closing its own tag
  } catch (_) { mermaidSource = ''; }
}

// Markdown for the conversation export: artifacts become labelled fenced blocks instead of raw tags.
export function artifactsToMarkdown(text) {
  return splitArtifacts(text).map(part => {
    if (part.kind === 'text') return part.text;
    const lang = part.type === 'code' ? part.language : {html: 'html', svg: 'svg', markdown: 'markdown', mermaid: 'mermaid'}[part.type];
    return `\n**Artifact: ${part.title}** (${artifactLabel(part.type)}${part.complete ? '' : ', incomplete'})\n\n\`\`\`\`${lang || ''}\n${part.content}\n\`\`\`\`\n`;
  }).join('\n');
}

const SCRIPTS_KEY = 'privacy-llm-artifact-scripts';
const readScripts = () => { try { return localStorage.getItem(SCRIPTS_KEY) !== 'off'; } catch (_) { return true; } };

export function createPane({getMessages, copyText, status}) {
  const $ = id => document.getElementById(id);
  const state = {id: null, version: 0, tab: 'preview', scripts: readScripts(), sessionId: null, shown: '', live: false, liveArtifact: null, saved: null};
  let pending = '';

  const find = () => {
    const entry = state.id ? collectArtifacts(getMessages()).get(state.id) : null;
    if (!entry) return null;
    state.version = Math.min(Math.max(state.version, 0), entry.versions.length - 1);
    return {entry, artifact: entry.versions[state.version]};
  };
  const filename = artifact => {
    const extension = artifact.type === 'code' ? (EXTENSIONS[artifact.language] || 'txt') : {html: 'html', svg: 'svg', markdown: 'md', mermaid: 'mmd'}[artifact.type];
    return `${slug(artifact.title) || 'artifact'}.${extension}`;
  };

  // While a reply is still streaming, the pane shows the artifact's source as it is written. The rendered preview
  // opens once the reply is saved (see open()), because half-written HTML is a poor thing to run.
  function showLive() {
    const artifact = state.liveArtifact;
    $('artifact-pane').hidden = false;
    $('artifact-title').textContent = artifact.title;
    $('artifact-version').hidden = true; $('tab-preview').hidden = true; $('artifact-scripts-label').hidden = true;
    $('tab-code').setAttribute('aria-selected', 'true');
    $('artifact-note').hidden = false;
    $('artifact-note').textContent = artifact.complete ? 'Artifact written. Finishing the reply…' : `Writing ${artifactLabel(artifact.type).toLowerCase()}…`;
    $('artifact-frame').hidden = true;
    const code = $('artifact-code'); code.hidden = false;
    const atBottom = code.scrollTop + code.clientHeight >= code.scrollHeight - 40;
    code.textContent = artifact.content;
    if (atBottom) code.scrollTop = code.scrollHeight;
  }

  function show() {
    if (state.live) { showLive(); return; }
    const found = find();
    if (!found) { close(); return; }
    const {entry, artifact} = found;
    const previewable = artifact.type !== 'code';
    if (!previewable) state.tab = 'code';
    $('artifact-pane').hidden = false;
    $('artifact-title').textContent = artifact.title;
    $('artifact-title').title = `${artifactLabel(artifact.type)} · ${artifact.title}`;
    const select = $('artifact-version');
    select.replaceChildren(...entry.versions.map((_, index) => new Option(`Version ${index + 1}`, String(index))));
    select.value = String(state.version); select.hidden = entry.versions.length < 2;
    $('tab-preview').hidden = !previewable;
    for (const [id, tab] of [['tab-preview', 'preview'], ['tab-code', 'code']]) $(id).setAttribute('aria-selected', String(state.tab === tab));
    $('artifact-scripts-label').hidden = !(artifact.type === 'html' || artifact.type === 'svg');
    $('artifact-scripts').checked = state.scripts;
    $('artifact-note').hidden = artifact.complete;
    if (!artifact.complete) $('artifact-note').textContent = 'This artifact looks cut off: the reply ended before its closing tag.';
    const code = state.tab === 'code';
    $('artifact-frame').hidden = code; $('artifact-code').hidden = !code;
    const key = [state.id, state.version, state.tab, state.scripts, artifact.content].join('\u0000');
    if (key === state.shown) return;  // nothing visible changed, so do not reload the frame
    state.shown = key;
    if (code) $('artifact-code').textContent = artifact.content;
    else {
      if (artifact.type === 'mermaid' && mermaidSource === null) {  // fetch the library first, then draw
        state.shown = '';
        loadMermaid().then(() => { if (state.id === artifact.id) show(); });
        return;
      }
      pending = frameDocument(artifact, state.scripts);
      $('artifact-frame').src = '/artifact-frame?load=' + Date.now();  // the frame replies "ready", then gets the document
    }
  }
  function close() {
    if (state.live) state.dismissed = true;  // closed while streaming: do not pop back open on the next chunk
    state.live = false; state.liveArtifact = null; state.saved = null;
    state.id = null; state.shown = ''; pending = '';
    $('artifact-pane').hidden = true;
    $('artifact-frame').src = 'about:blank';
  }
  window.addEventListener('message', event => {
    const frame = $('artifact-frame');
    if (event.source !== frame.contentWindow || event.data?.type !== 'artifact-ready' || !pending) return;
    frame.contentWindow.postMessage({type: 'artifact', html: pending}, '*');  // '*' because the sandboxed frame has an opaque origin
  });

  $('artifact-close').onclick = close;
  $('tab-preview').onclick = () => { state.tab = 'preview'; show(); };
  $('tab-code').onclick = () => { state.tab = 'code'; show(); };
  $('artifact-version').onchange = () => { state.version = Number($('artifact-version').value); show(); };
  $('artifact-scripts').onchange = () => {
    state.scripts = $('artifact-scripts').checked;
    try { localStorage.setItem(SCRIPTS_KEY, state.scripts ? 'on' : 'off'); } catch (_) { /* the choice lasts until reload */ }
    show();
  };
  $('artifact-copy').onclick = () => { const found = find(); if (found) copyText(found.artifact.content, $('artifact-copy')); };
  $('artifact-download').onclick = () => {
    const found = find(); if (!found) return;
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([found.artifact.content], {type: 'text/plain;charset=utf-8'}));
    link.download = filename(found.artifact); document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    status('Downloaded. The file contains your real, unmasked text.');
  };

  return {
    // version -1 means the latest
    open(id, version, sessionId) {
      state.live = false; state.liveArtifact = null; state.saved = null; state.dismissed = false;
      state.id = id; state.sessionId = sessionId ?? state.sessionId; state.tab = 'preview'; state.shown = '';
      const entry = collectArtifacts(getMessages()).get(id);
      state.version = version >= 0 ? version : (entry ? entry.versions.length - 1 : 0);
      show();
    },
    // Called after every render: closes on another conversation, otherwise refreshes if the content changed.
    sync(sessionId) {
      if (state.live || !state.id) return;
      if (sessionId !== state.sessionId) close(); else show();
    },
    // Streaming preview: shows the artifact being written. endLive() puts back whatever was open before.
    live(part) {
      if (state.dismissed) return;
      if (!state.live) state.saved = state.id ? {id: state.id, version: state.version, tab: state.tab, sessionId: state.sessionId} : null;
      state.live = true; state.liveArtifact = part;
      showLive();
    },
    endLive() {
      state.dismissed = false;
      if (!state.live) return;
      const previous = state.saved;
      state.live = false; state.liveArtifact = null; state.saved = null;
      if (previous) { Object.assign(state, previous, {shown: ''}); show(); } else close();
    },
    close,
    isOpen: () => Boolean(state.id) || state.live
  };
}
