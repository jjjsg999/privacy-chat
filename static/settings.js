const form = document.getElementById('settings-form');
const status = document.getElementById('status');
const save = document.getElementById('save');
const $ = id => document.getElementById(id);
let savedModel = '';
let configured = false;
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
    const data = await response.json(); showMonthly(data); form.elements.model.value = data.model; form.elements.terms.value = data.sensitive_terms; form.elements.web_instructions.value = data.web_instructions || ''; form.elements.review_extracted_text.checked = Boolean(data.review_extracted_text); form.elements.meeting_model.value = data.meeting_model || ''; form.elements.telegram_instructions.value = data.telegram_instructions || ''; form.elements.context_mode_chat.value = data.context_mode_chat || 'balanced'; form.elements.context_mode_meeting.value = data.context_mode_meeting || 'full'; save.disabled = false;
    form.elements.chat_diagnosis.checked = Boolean(data.chat_diagnosis); savedModel = data.model; $('current-model').textContent = savedModel; configured = data.openrouter.configured; connectionState(); $('test-connection').disabled = false;
    
  } catch (error) { report(error.message, true); }
}
form.onsubmit = async event => {
  event.preventDefault(); save.disabled = true; $('test-connection').disabled = true; report('Saving…');
  try {
    const response = await fetch('/api/settings', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({model:form.elements.model.value.trim(), sensitive_terms:form.elements.terms.value, openrouter_api_key:form.elements.openrouter_api_key.value, clear_api_key:form.elements.clear_api_key.checked, chat_diagnosis:form.elements.chat_diagnosis.checked, review_extracted_text:form.elements.review_extracted_text.checked, meeting_model:form.elements.meeting_model.value.trim(), web_instructions:form.elements.web_instructions.value, telegram_instructions:form.elements.telegram_instructions.value, context_mode_chat:form.elements.context_mode_chat.value, context_mode_meeting:form.elements.context_mode_meeting.value, monthly_cost_ceiling_usd:form.elements.monthly_cost_ceiling_usd.value})});
    if (!response.ok) throw new Error('Could not save. Check the model ID and try again.');
    const data = await response.json(); showMonthly(data); savedModel = data.model; $('current-model').textContent = savedModel; configured = data.openrouter.configured; connectionState();
    form.elements.openrouter_api_key.value = ''; form.elements.clear_api_key.checked = false;
    report('Settings saved locally.');
  } catch (error) { report(error.message, true); }
  finally { save.disabled = false; $('test-connection').disabled = false; }
};
load();

const picker = $('model-picker'), frame = $('model-frame'), browse = $('browse-models');
function sendCurrent() { frame.contentWindow?.postMessage({type:'privacy-llm-current', model:form.elements.model.value.trim(), meeting_model:form.elements.meeting_model.value.trim()}, location.origin); }
browse.onclick = () => {
  picker.showModal();
  if (!frame.getAttribute('src')) frame.src = '/models?embed=1'; else sendCurrent();
};
$('close-picker').onclick = () => picker.close();
picker.addEventListener('click', event => { if (event.target === picker) picker.close(); });
picker.addEventListener('close', () => browse.focus());
frame.onload = sendCurrent;
window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.source !== frame.contentWindow || event.data?.type !== 'privacy-llm-pick') return;
  const model = typeof event.data.model === 'string' ? event.data.model.trim() : '';
  if (event.data.purpose === 'general' && /^\S+\/\S+$/.test(model)) { form.elements.model.value = model; report('Model ID filled in. Save settings to apply.'); }
  else if (event.data.purpose === 'meeting' && (model === '' || /^\S+\/\S+$/.test(model))) { form.elements.meeting_model.value = model; report('Meeting model filled in. Save settings to apply.'); }
});
$('test-connection').onclick = async () => {
  $('test-connection').disabled = true; save.disabled = true; $('connection').textContent = 'Testing saved model…';
  try { const response = await fetch('/api/openrouter/test', {method:'POST'}); if (!response.ok) throw new Error('Connection test unavailable. Try again.'); const result = await response.json(); $('connection').textContent = `${result.message} (${result.model})`; }
  catch (error) { $('connection').textContent = error.message; }
  finally { $('test-connection').disabled = false; save.disabled = false; }
};
