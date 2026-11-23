// Settings > Spaces: list, add, rename and delete spaces, and choose where Telegram chats go.
// Each action saves immediately through /api/spaces (it is not part of the Save settings button).
// Space names are user text: everything is set with textContent.
(() => {
  const $ = id => document.getElementById(id);
  const json = {'Content-Type': 'application/json'};

  async function call(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save. Please retry.');
    return data;
  }
  function say(text, error = false) { $('space-status').textContent = text; $('space-status').classList.toggle('error', error); }

  function render(summary) {
    const list = $('space-list');
    list.replaceChildren();
    for (const space of summary.spaces) {
      const row = document.createElement('li'); row.className = 'space-row';
      const name = document.createElement('span'); name.className = 'space-name'; name.textContent = space.name;
      const count = document.createElement('span'); count.className = 'space-count';
      count.textContent = `${space.chats} chat${space.chats === 1 ? '' : 's'}`;
      const rename = document.createElement('button'); rename.type = 'button'; rename.textContent = 'Rename';
      rename.setAttribute('aria-label', `Rename ${space.name}`);
      rename.onclick = async () => {
        const value = window.prompt('Rename space (1 to 40 characters)', space.name);
        if (value === null) return;
        try { render(await call(`/api/spaces/${encodeURIComponent(space.id)}`, {method: 'PATCH', headers: json, body: JSON.stringify({name: value})})); say(`Renamed to ${value.trim()}.`); }
        catch (error) { say(error.message, true); }
      };
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Delete';
      remove.setAttribute('aria-label', `Delete ${space.name}`);
      remove.disabled = Boolean(space.delete_blocked);
      remove.onclick = async () => {
        if (!window.confirm(`Delete the space "${space.name}"? It has no chats.`)) return;
        try { render(await call(`/api/spaces/${encodeURIComponent(space.id)}`, {method: 'DELETE'})); say('Space deleted.'); }
        catch (error) { say(error.message, true); }
      };
      row.append(name, count, rename, remove);
      if (space.delete_blocked) {  // say why Delete is off, in words, not only by greying the button
        const reason = document.createElement('span'); reason.className = 'space-reason'; reason.textContent = `Cannot delete: ${space.delete_blocked}`;
        row.append(reason);
      }
      list.append(row);
    }
    const select = $('telegram-space');
    select.replaceChildren(...summary.spaces.map(space => new Option(space.name, space.id)));
    select.value = summary.telegram_space;
  }

  async function add() {
    const input = $('space-new');
    try { render(await call('/api/spaces', {method: 'POST', headers: json, body: JSON.stringify({name: input.value})})); say(`Added ${input.value.trim()}.`); input.value = ''; }
    catch (error) { say(error.message, true); }
  }
  $('space-add').onclick = add;
  $('space-new').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); add(); } };  // Enter must not submit the whole settings form
  $('telegram-space').onchange = async () => {
    try { render(await call('/api/spaces/telegram', {method: 'PUT', headers: json, body: JSON.stringify({space: $('telegram-space').value})})); say('Telegram chats will go to this space.'); }
    catch (error) { say(error.message, true); }
  };
  call('/api/spaces').then(render).catch(error => say(error.message, true));
})();
