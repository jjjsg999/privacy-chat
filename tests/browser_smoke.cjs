// Run with the bundled Playwright module path passed as the first argument.
const {chromium} = require(process.argv[2]);
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  try {
    for (const width of [390, 1280]) {
      const page = await browser.newPage({viewport:{width, height:844}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const meeting = {id:'telegram_ui',title:'Meeting review',channel_origin:'telegram',session_type:'meeting',updated_at:new Date().toISOString(),transcript:'Full raw transcript.',intelligence:'Action items: Alex will review the plan.',messages:[{role:'user',content:'Full raw transcript.'},{role:'assistant',content:'Action items: Alex will review the plan.'}]};
      await page.route('**/api/sessions', route => route.fulfill({json:[meeting]}));
      await page.route('**/api/sessions/telegram_ui', route => route.fulfill({json:meeting}));
      await page.goto('http://127.0.0.1:8000');
      await page.getByRole('heading', {name:'Space to think freely.'}).waitFor();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      if (width < 760) await page.getByRole('button', {name:'Toggle conversations'}).click();
      await page.locator('.session-open').filter({hasText:'Meeting review'}).click();
      await page.getByText('Full conversation history', {exact:true}).waitFor();
      await page.locator('summary').filter({hasText:'Full transcript'}).click();
      assert(await page.locator('.meeting-panel').filter({hasText:'Full transcript'}).isVisible());
      if (width < 760) await page.getByRole('button', {name:'Toggle conversations'}).click();
      await page.route('**/api/openrouter/models', route => route.fulfill({json:[{id:'example/alpha',name:'Example Alpha',context_length:32000,pricing:{prompt:'0.000001',completion:'0.000002'},architecture:{input_modalities:['text','image'],output_modalities:['text']},supported_parameters:['tools']},{id:'example/sparse',name:'<img src=x onerror=alert(1)>',context_length:'unknown',supported_parameters:'unexpected'}]}));
      let favourites = [];
      await page.route('**/api/settings/favourite', route => { const body = route.request().postDataJSON(); favourites = body.favourite ? [body.model] : []; return route.fulfill({json:{favourites}}); });
      await page.route('**/api/openrouter/test', route => route.fulfill({json:{ok:true,model:'example/alpha',message:'Connected. The saved model responded successfully.'}}));
      let selectedModel;
      await page.route('**/api/settings', async route => {
        if (route.request().method() === 'PUT') { const body = route.request().postDataJSON(); selectedModel=body.model; await route.fulfill({json:{...body,openrouter:{configured:true}}}); }
        else await route.fulfill({json:{model:'example/current',sensitive_terms:'CLIENT: Example',openrouter:{configured:true}}});
      });
      await page.getByRole('link', {name:'Settings'}).click();
      await page.getByLabel('OpenRouter model ID').waitFor();
      await page.waitForFunction(() => !document.getElementById('save').disabled);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      assert.match(await page.getByLabel('OpenRouter model ID').inputValue(), /\S+\/\S+/);
      await page.getByLabel('Search models by name or provider').fill('alpha');
      await page.getByLabel('Maximum input cost').fill('0');
      assert.equal(await page.locator('.model-card').count(), 0);
      await page.getByLabel('Maximum input cost').fill('1');
      await page.getByLabel('Maximum output cost').fill('1');
      assert.equal(await page.locator('.model-card').count(), 0);
      await page.getByLabel('Maximum output cost').fill('2');
      await page.getByLabel('Input mode', {exact:true}).selectOption('image');
      await page.getByLabel('Output mode', {exact:true}).selectOption('text');
      assert.equal(await page.locator('.model-card').count(), 1);
      await page.getByRole('button', {name:'☆ Favourite',exact:true}).click();
      await page.getByRole('button', {name:'★ Favourited',exact:true}).waitFor();
      assert.deepEqual(favourites, ['example/alpha']);
      await page.getByLabel('Favourites only', {exact:true}).check();
      assert.equal(await page.locator('.model-card').count(), 1);
      await page.getByLabel('Favourites only', {exact:true}).uncheck();
      await page.getByRole('button', {name:'Select model'}).click();
      await page.getByRole('button', {name:'Save settings'}).click();
      await page.getByText('Settings saved locally.', {exact:true}).waitFor();
      assert.equal(selectedModel, 'example/alpha');
      await page.getByRole('button', {name:'Test connection',exact:true}).click();
      await page.locator('#connection').filter({hasText:'Connected.'}).waitFor();
      await page.getByLabel('Maximum input cost').fill('');
      await page.getByLabel('Maximum output cost').fill('');
      await page.getByLabel('Input mode', {exact:true}).selectOption('');
      await page.getByLabel('Output mode', {exact:true}).selectOption('');
      await page.getByLabel('Search models by name or provider').fill('sparse');
      await page.locator('.model-card strong').filter({hasText:'<img src=x onerror=alert(1)>'}).waitFor();
      assert.equal(await page.locator('.model-card img').count(), 0);
      assert(!(await page.locator('.model-card').innerText()).includes('NaN'));
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({path:`data/settings-${width}.png`, fullPage:true});
      if (width < 760) await page.getByRole('button', {name:'Toggle navigation'}).click();
      await page.locator('nav.main-nav a[href="/"]').click();
      await page.getByRole('textbox', {name:'Message', exact:true}).fill('Hello');
      // Mock the provider-facing endpoint for UI interaction; do not make paid calls.
      await page.route('**/api/chat', route => route.fulfill({status:200, contentType:'application/json', body:JSON.stringify({id:'web_smoke',title:'Smoke conversation',messages:[{role:'user',content:'Hello'},{role:'assistant',content:'A local smoke-check response.'}]})}));
      await page.getByRole('button', {name:'Send'}).click();
      await page.getByText('A local smoke-check response.').waitFor();
      assert.deepEqual(errors, []);
      await page.screenshot({path:`data/smoke-${width}.png`, fullPage:true});
      console.log(`${width}px chat/settings and message interaction: passed`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
