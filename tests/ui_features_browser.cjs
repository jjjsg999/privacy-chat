// Browser check for streaming, Stop, rename, search and export against a local MOCK server.
// No Python app, no OpenRouter and no paid calls. Run: node tests/ui_features_browser.cjs <playwright-module-path>
const {chromium} = require(process.argv[2]);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const types = {'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html; charset=utf-8', '.json': 'application/json'};
const sessions = new Map();
let counter = 0, aborted = 0, lastAttachments = [];
let chosen = 'a/cheap', meetingChosen = ''; const starred = new Set();
const model = (id, name, prompt, completion, context = 32000, extra = {}) => ({id, name, context_length: context,
  pricing: {prompt, completion}, architecture: {input_modalities: ['text'], output_modalities: ['text']}, ...extra});
const catalogue = [model('b/pricey', 'Pricey Pro', '0.000015', '0.000075', 200000), model('a/cheap', 'Cheap Mini', '0.0000001', '0.0000004', 8000),
  model('b/mid', 'Mid Model', '0.000003', '0.000015', 200000), model('a/free', 'Free One', '0', '0', null),
  model('c/variable', 'Router Auto', '-1', '-1'), model('c/search:online', 'Search Online', '0.000001', '0.000002')];
const rows = query => [...sessions.values()].filter(s => !query || s.title.toLowerCase().includes(query.toLowerCase()) ||
  s.messages.some(m => m.content.toLowerCase().includes(query.toLowerCase())))
  .map(({id, title, updated_at, channel_origin}) => ({id, title, updated_at, channel_origin}));
const json = (res, value, status = 200) => { res.writeHead(status, {'Content-Type': 'application/json'}); res.end(JSON.stringify(value)); };
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  if (req.method === 'GET' && url.pathname === '/') { res.writeHead(200, {'Content-Type': types['.html']}); return res.end(fs.readFileSync(path.join(root, 'static/index.html'))); }
  if (req.method === 'GET' && url.pathname.startsWith('/static/')) {
    const file = path.join(root, url.pathname);
    if (!file.startsWith(path.join(root, 'static')) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.writeHead(200, {'Content-Type': types[path.extname(file)] || 'application/octet-stream'}); return res.end(fs.readFileSync(file));
  }
  if (req.method === 'GET' && url.pathname === '/models') { res.writeHead(200, {'Content-Type': types['.html']}); return res.end(fs.readFileSync(path.join(root, 'static/models.html'))); }
  if (req.method === 'GET' && url.pathname === '/api/openrouter/models') return json(res, catalogue);
  if (req.method === 'GET' && url.pathname === '/api/settings') return json(res, {model: chosen, meeting_model: meetingChosen, favourites: [...starred]});
  if (req.method === 'PUT' && url.pathname === '/api/settings/model') {
    let body = ''; for await (const chunk of req) body += chunk;
    const {model, purpose = 'general'} = JSON.parse(body);
    if (purpose === 'meeting') meetingChosen = model; else chosen = model;
    return json(res, {model: chosen, meeting_model: meetingChosen});
  }
  if (req.method === 'PUT' && url.pathname === '/api/settings/favourite') {
    let body = ''; for await (const chunk of req) body += chunk;
    const {model, favourite} = JSON.parse(body); favourite ? starred.add(model) : starred.delete(model); return json(res, {favourites: [...starred]});
  }
  if (req.method === 'GET' && url.pathname === '/api/sessions') return json(res, rows(url.searchParams.get('q')));
  const match = url.pathname.match(/^\/api\/sessions\/([\w-]+)$/);
  if (match) {
    const session = sessions.get(match[1]);
    if (!session) return json(res, {detail: 'Conversation not found.'}, 404);
    if (req.method === 'GET') return json(res, session);
    if (req.method === 'PATCH') {
      let body = ''; for await (const chunk of req) body += chunk;
      session.title = JSON.parse(body).title; return json(res, session);
    }
  }
  if (req.method === 'POST' && url.pathname === '/api/chat/stream') {
    let body = ''; for await (const chunk of req) body += chunk;
    const {session_id, message, attachments = []} = JSON.parse(body);
    lastAttachments = attachments;
    const session = sessions.get(session_id) || {id: `web_${++counter}`, title: 'New conversation', channel_origin: 'web', session_type: 'chat', messages: [], updated_at: new Date().toISOString()};
    sessions.set(session.id, session);
    res.writeHead(200, {'Content-Type': 'application/x-ndjson'});
    const send = event => res.write(JSON.stringify(event) + '\n');
    let text = '', finished = false;
    const composed = [message, ...attachments.map(a => `[Attached file: ${a.name}]\n${a.text.trim()}\n[End of attached file]`)].filter(Boolean).join('\n\n');
    const user = attachments.length ? {role: 'user', content: composed, typed: message, attachments: attachments.map(a => ({name: a.name, kind: a.kind, chars: a.text.length}))} : {role: 'user', content: message};
    const save = stopped => { session.messages.push(user, {role: 'assistant', content: text, model: 'test/model', ...(stopped ? {stopped: true} : {})}); };
    res.on('close', () => { if (!finished) { aborted++; if (text.trim()) save(true); } });
    send({type: 'start', session_id: session.id});
    const pieces = message.includes('slow') ? Array.from({length: 60}, (_, i) => `chunk${i + 1} `) : message.includes('fail') ? ['Partial before error '] : ['Hello ', 'world ', 'from the mock.'];
    for (const piece of pieces) {
      if (res.destroyed) return;
      text += piece; send({type: 'delta', text: piece}); await wait(message.includes('slow') ? 150 : 30);
    }
    finished = true;
    if (message.includes('fail')) { save(true); send({type: 'error', message: 'Provider hiccup', saved: true}); return res.end(); }
    save(false); session.updated_at = new Date().toISOString();
    send({type: 'done', session}); return res.end();
  }
  res.writeHead(404); res.end();
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, acceptDownloads: true});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    await page.getByRole('heading', {name: 'Space to think freely.'}).waitFor();

    // 1. Normal streamed reply, model label, export button, Send/Stop toggling.
    await page.getByRole('textbox', {name: 'Message', exact: true}).fill('Hello there');
    await page.getByRole('button', {name: 'Send'}).click();
    await page.getByText('Hello world from the mock.').waitFor();
    await page.locator('.model-label').filter({hasText: 'test/model'}).waitFor();
    assert(await page.getByRole('button', {name: 'Send'}).isVisible());
    assert(await page.locator('#stop').isHidden());
    assert(await page.locator('#export').isVisible());

    // 2. Stop mid-stream keeps the partial reply and labels it.
    await page.getByRole('textbox', {name: 'Message', exact: true}).fill('slow reply please');
    await page.getByRole('button', {name: 'Send'}).click();
    await page.getByText('chunk3').waitFor();
    assert(await page.locator('#stop').isVisible());
    assert(await page.getByRole('button', {name: 'Send'}).isHidden());
    await page.locator('#stop').click();
    await page.locator('#status').filter({hasText: 'partial reply was saved'}).waitFor();
    await page.locator('.model-label').filter({hasText: 'stopped early'}).waitFor();
    assert(aborted >= 1);
    assert.equal(await page.getByRole('textbox', {name: 'Message', exact: true}).inputValue(), '');
    assert(!(await page.locator('.message.assistant').last().innerText()).includes('chunk40'));

    // 3. Mid-stream provider error: partial text kept, error shown.
    await page.getByRole('textbox', {name: 'Message', exact: true}).fill('please fail');
    await page.getByRole('button', {name: 'Send'}).click();
    await page.locator('#status.error').filter({hasText: 'Provider hiccup'}).waitFor();
    await page.getByText('Partial before error').waitFor();

    // 4. Rename (stable manual title) and 5. search.
    page.once('dialog', dialog => dialog.accept('Quarterly planning'));
    await page.getByRole('button', {name: /Rename conversation/}).first().click();
    await page.locator('.session-open').filter({hasText: 'Quarterly planning'}).waitFor();
    assert.equal(await page.locator('#chat-title').innerText(), 'Quarterly planning');
    await page.getByLabel('Search conversations').fill('quarterly');
    await page.locator('.session-open').filter({hasText: 'Quarterly planning'}).waitFor();
    await page.getByLabel('Search conversations').fill('chunk2');
    await page.locator('.session-open').filter({hasText: 'Quarterly planning'}).waitFor();
    await page.getByLabel('Search conversations').fill('nothing-matches-this');
    await page.getByText('No matching conversations.').waitFor();
    await page.getByLabel('Search conversations').fill('');
    await page.locator('.session-open').first().waitFor();

    // 6. Markdown export with real text and a slugged filename.
    const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#export').click()]);
    assert.equal(download.suggestedFilename(), 'quarterly-planning.md');
    const exported = fs.readFileSync(await download.path(), 'utf8');
    assert(exported.startsWith('# Quarterly planning'));
    assert(exported.includes('## You') && exported.includes('Hello world from the mock.'));
    // 7. Attachments with preview ON (PDF/image text is editable before sending), then OFF.
    let review = true, uploads = 0;
    await page.route('**/api/upload', route => { uploads++; return route.fulfill({json: {limits: {max_attachments: 5}, files: [
      {name: 'scan.pdf', kind: 'ocr', source: 'pdf', pages: 2, chars: 21, text: 'Invoice 4021 for Acme', review,
       warnings: ['OCR confidence is low (70%). Check the text for mistakes.']},
      {name: 'notes.txt', kind: 'exact', source: 'text', pages: 1, chars: 11, text: 'Plain notes', review: false, warnings: []}]}}); });
    const files = [{name: 'scan.pdf', mimeType: 'application/pdf', buffer: Buffer.from('x')}, {name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('x')}];
    await page.locator('#file-input').setInputFiles(files);
    await page.locator('.attachment').nth(1).waitFor();
    await page.getByText('OCR confidence is low').waitFor();
    await page.locator('#status').filter({hasText: 'Review the extracted text'}).waitFor();
    assert.equal(await page.locator('.attachment textarea').count(), 1);  // only the PDF is previewed
    await page.getByLabel('Extracted text from scan.pdf').fill('Invoice 4021 for Acme Corp (corrected)');
    await page.locator('.attachment').first().getByText('38 characters').waitFor();
    await page.getByRole('button', {name: 'Send'}).click();  // empty typed message is allowed with attachments
    await page.waitForFunction(() => !document.querySelector('.attachment'));  // cleared after a successful send
    await page.locator('.message.user').last().locator('summary').filter({hasText: 'Attached: scan.pdf, notes.txt'}).waitFor();
    assert.equal(lastAttachments[0].text, 'Invoice 4021 for Acme Corp (corrected)');
    assert.equal(lastAttachments.length, 2);
    await page.locator('.message.user').last().locator('summary').click();
    assert((await page.locator('.message.user').last().innerText()).includes('(corrected)'));
    review = false;
    await page.locator('#file-input').setInputFiles(files);
    await page.locator('.attachment').nth(1).waitFor();
    assert.equal(await page.locator('.attachment textarea').count(), 0);  // default: no preview
    await page.getByRole('button', {name: 'Remove attachment: notes.txt'}).click();
    assert.equal(await page.locator('.attachment').count(), 1);
    await page.getByRole('textbox', {name: 'Message', exact: true}).fill('Summarise this');
    await page.getByRole('button', {name: 'Send'}).click();
    await page.waitForFunction(() => !document.querySelector('.attachment'));
    await page.locator('.message.user').last().getByText('Summarise this').waitFor();
    assert.equal(lastAttachments.length, 1);
    // 8. Resizable sidebar: drag, persistence across reload, keyboard, double-click reset, limits.
    const width = () => page.evaluate(() => document.getElementById('sidebar').getBoundingClientRect().width);
    assert.equal(Math.round(await width()), 270);
    const handle = page.locator('#sidebar-resizer');
    const box = await handle.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + 200);
    await page.mouse.down(); await page.mouse.move(box.x + 150, box.y + 200, {steps: 6}); await page.mouse.up();
    assert(Math.abs((await width()) - 420) <= 6, `width after drag ${await width()}`);
    await page.reload(); await page.locator('#sidebar-resizer').waitFor({state: 'attached'});
    assert(Math.abs((await width()) - 420) <= 6, 'width persists across reloads');
    await handle.focus(); await page.keyboard.press('ArrowLeft'); await page.keyboard.press('Shift+ArrowLeft');
    assert(Math.abs((await width()) - (420 - 16 - 48)) <= 6, 'arrow keys resize by 16px, Shift by 48px');
    await page.mouse.move(0, 0); await handle.dblclick(); assert.equal(Math.round(await width()), 270);
    await handle.focus(); for (let i = 0; i < 30; i++) await page.keyboard.press('Shift+ArrowLeft');
    assert.equal(Math.round(await width()), 200);  // minimum
    for (let i = 0; i < 30; i++) await page.keyboard.press('Shift+ArrowRight');
    assert.equal(Math.round(await width()), 560);  // maximum
    await handle.dblclick();

    // 9. Models page: price table, sort, price-range filters, hidden unusable models, use and favourite.
    await page.getByRole('link', {name: 'Models'}).click();
    await page.getByText('Free One').first().waitFor();
    assert.equal(Math.round(await width()), 270);  // sidebar width is shared by every page
    const names = async () => page.locator('#rows tr td:first-child strong').allInnerTexts();
    assert.deepEqual(await names(), ['Free One', 'Cheap Mini', 'Mid Model', 'Pricey Pro']);  // default: input price ascending
    if (process.env.SCREENSHOT_DIR) {
      await page.setViewportSize({width: 1280, height: 1250});
      await page.screenshot({path: path.join(process.env.SCREENSHOT_DIR, 'models.png')});
      await page.setViewportSize({width: 1280, height: 800});
    }
    assert.equal(await page.locator('#current-model').innerText(), 'a/cheap');
    assert((await page.locator('#rows tr').nth(1).innerText()).includes('$0.1'));
    assert((await page.locator('#rows tr').nth(0).innerText()).includes('Free'));
    await page.getByRole('button', {name: /Input \$ \/ 1M/}).click();  // toggle to descending
    assert.deepEqual(await names(), ['Pricey Pro', 'Mid Model', 'Cheap Mini', 'Free One']);
    await page.getByRole('button', {name: /^Context/}).click();
    assert.equal((await names())[3], 'Free One');  // context sort puts unknown values last
    await page.getByLabel('Input price up to').fill('5');
    assert.deepEqual((await names()).sort(), ['Cheap Mini', 'Free One', 'Mid Model']);
    await page.getByLabel('Output price from').fill('10');
    assert.deepEqual(await names(), ['Mid Model']);
    await page.getByRole('button', {name: 'Clear filters'}).click();
    await page.getByLabel('Free models only').check();
    assert.deepEqual(await names(), ['Free One']);
    await page.getByLabel('Free models only').uncheck();
    await page.getByLabel('Hide models this app cannot use').uncheck();
    assert.equal((await names()).length, 6);
    assert.equal(await page.locator('.badge', {hasText: 'Unavailable'}).count(), 2);
    assert(await page.getByRole('button', {name: 'Use model Router Auto', exact: true}).isDisabled());
    assert(await page.getByRole('button', {name: 'Use model Router Auto for meetings', exact: true}).isDisabled());
    await page.getByLabel('Hide models this app cannot use').check();
    await page.getByRole('button', {name: 'Use model Mid Model', exact: true}).click();
    await page.locator('#count').filter({hasText: 'Chats and Adviser now use Mid Model'}).waitFor();
    assert.equal(chosen, 'b/mid');
    assert.equal(await page.locator('#current-model').innerText(), 'b/mid');
    await page.locator('tr.in-use').getByText('In use').first().waitFor();
    // Separate meeting model: independent of the chat model, follows it when unset, resettable.
    assert.equal(await page.locator('#current-meeting-model').innerText(), 'b/mid (same as chats)');
    assert(await page.locator('#reset-meeting').isHidden());
    await page.getByRole('button', {name: 'Use model Pricey Pro for meetings'}).click();
    await page.locator('#count').filter({hasText: 'Meeting analysis and questions now use Pricey Pro'}).waitFor();
    assert.equal(meetingChosen, 'b/pricey'); assert.equal(chosen, 'b/mid');
    assert.equal(await page.locator('#current-meeting-model').innerText(), 'b/pricey');
    assert.equal(await page.locator('#current-model').innerText(), 'b/mid');
    await page.locator('tr', {hasText: 'Pricey Pro'}).locator('.badge', {hasText: 'Meetings'}).waitFor();
    assert(await page.getByRole('button', {name: 'Use model Router Auto for meetings'}).count() === 0);  // hidden unusable model
    await page.locator('#reset-meeting').click();
    await page.locator('#count').filter({hasText: 'same model as chats'}).waitFor();
    assert.equal(meetingChosen, '');
    assert.equal(await page.locator('#current-meeting-model').innerText(), 'b/mid (same as chats)');
    await page.getByRole('button', {name: 'Favourite: Cheap Mini'}).click();
    await page.getByRole('button', {name: 'Remove favourite: Cheap Mini'}).waitFor();
    await page.getByLabel('Favourites only').check();
    assert.deepEqual(await names(), ['Cheap Mini']);
    await page.getByLabel('Provider').selectOption('b');
    assert.equal((await names()).length, 0);
    assert.deepEqual(errors, []);
    console.log('streaming, stop, error, rename, search, export, attachments, preview, sidebar resize, models page: passed');
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); process.exit(1); });
