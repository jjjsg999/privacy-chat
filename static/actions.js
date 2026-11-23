// Action tracker: actions from every meeting, filterable, with a local status checkbox.
// Task, owner and meeting titles come from model output about untrusted transcripts, so everything is set with textContent.
const $ = id => document.getElementById(id);
const json = {'Content-Type': 'application/json'};

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Something went wrong. Please retry.');
  return data;
}
function say(text, error = false) { $('count').textContent = text; $('count').classList.toggle('error', error); }
function fill(select, items, current) {
  const first = select.options[0];
  select.replaceChildren(first, ...items.map(([value, label]) => new Option(label, value)));
  select.value = items.some(([value]) => value === current) ? current : '';
}
function row(action) {
  const wrap = document.createElement('div'); wrap.className = 'action-row' + (action.status === 'done' ? ' done' : '');
  const box = document.createElement('input'); box.type = 'checkbox'; box.checked = action.status === 'done';
  box.setAttribute('aria-label', `Mark done: ${action.task}`);
  box.onchange = async () => {
    const done = box.checked;
    try {
      await api(`/api/actions/${encodeURIComponent(action.session_id)}/${encodeURIComponent(action.action_id)}`, {method: 'PATCH', headers: json, body: JSON.stringify({status: done ? 'done' : 'open'})});
      await load();
    } catch (error) { box.checked = !done; say(error.message, true); }
  };
  const task = document.createElement('div'); task.className = 'action-task'; task.textContent = action.task;
  const sub = document.createElement('div'); sub.className = 'action-sub';
  sub.append(`Owner: ${action.owner || 'not stated'} · Due: ${action.due || 'not stated'} · `);
  const link = document.createElement('a'); link.href = `/?session=${encodeURIComponent(action.session_id)}`; link.textContent = action.meeting_title;
  sub.append(link, ` (${action.meeting_date ? new Date(action.meeting_date).toLocaleDateString() : 'no date'})`);
  for (const n of action.lines || []) {
    const chip = document.createElement('a'); chip.className = 'line-chip'; chip.textContent = `L${n}`;
    chip.href = `/?session=${encodeURIComponent(action.session_id)}&line=${n}`; chip.title = 'Open this line in the meeting';
    sub.append(' ', chip);
  }
  if (action.verified === false) { const flag = document.createElement('span'); flag.className = 'record-flag'; flag.textContent = ` ⚠ Unverified: ${action.reason || 'could not be checked'}`; sub.append(flag); }
  wrap.append(box, task, sub);
  return wrap;
}
async function load() {
  const query = new URLSearchParams({status: $('f-status').value, include_archived: $('f-archived').checked});
  if ($('f-owner').value) query.set('owner', $('f-owner').value);
  if ($('f-meeting').value) query.set('meeting', $('f-meeting').value);
  const data = await api(`/api/actions?${query}`);
  fill($('f-owner'), data.owners.map(name => [name, name]), $('f-owner').value);
  fill($('f-meeting'), data.meetings.map(m => [m.id, m.title]), $('f-meeting').value);
  $('list').replaceChildren(...data.actions.map(row));
  say(data.actions.length ? `${data.actions.length} action${data.actions.length === 1 ? '' : 's'}.` : 'No actions match these filters.');
}
for (const id of ['f-status', 'f-owner', 'f-meeting', 'f-archived']) $(id).onchange = () => load().catch(error => say(error.message, true));
load().catch(error => say(error.message, true));
