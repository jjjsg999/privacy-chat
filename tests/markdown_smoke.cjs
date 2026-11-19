// Run with the Playwright module path as the first argument.
const {chromium}=require(process.argv[2]);
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
for(const width of [390,1280]){
 const page=await browser.newPage({viewport:{width,height:844}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const content='# Executive review\n\n**Important decision** and *context*.\n\n| Owner | Action | Status | Deadline |\n| --- | --- | --- | --- |\n| Team | Review plan | Pending | Friday |\n\n- First item\n- Second item\n\n```python\nprint("hello")\n```\n\n<script>alert(1)</script><img src="https://example.com/tracker" onerror="alert(1)">\n\n[Unsafe](javascript:alert(1))';
 const session={id:'test',title:'Markdown test',channel_origin:'web',session_type:'meeting',intelligence:content,transcript:'# Raw transcript',messages:[{role:'user',content:'# Raw transcript'},{role:'assistant',content}]};
 await page.route('**/api/sessions',r=>r.fulfill({json:[session]}));
 await page.route('**/api/sessions/test',r=>r.fulfill({json:session}));
 await page.goto('http://127.0.0.1:8000');
 if(width<760)await page.locator('#toggle-sidebar').click();
 await page.locator('.session-open').filter({hasText:'Markdown test'}).click();
 await page.locator('.assistant h1').waitFor();
 assert.equal(await page.locator('.assistant strong').textContent(),'Important decision');
 assert.equal(await page.locator('.assistant tbody tr').count(),1);
 assert.equal(await page.locator('.assistant li').count(),2);
 assert.equal(await page.locator('.markdown script,.markdown img,.markdown [onerror],.markdown a[href^="javascript:"]').count(),0);
 assert.equal(await page.locator('.meeting-panel').count(),1);
 assert.equal(await page.locator('.meeting-panel').evaluate(el=>el.open),false);
 assert.equal(await page.locator('.assistant h1').count(),1);
 assert.equal(await page.locator('.user .body').isVisible(),false);
 await page.locator('.meeting-panel summary').click();
 assert.equal(await page.locator('.user .body').isVisible(),true);
 session.chat_diagnosis=true;
 session.meeting_api_request={model:'test/model',captured_at:new Date().toISOString(),messages:[{role:'system',content:'Preserve tokens.'},{role:'user',content:'CLIENT__1 approved <script>literal</script>'}]};
 await page.reload();
 if(width<760)await page.locator('#toggle-sidebar').click();
 await page.locator('.session-open').filter({hasText:'Markdown test'}).click();
 await page.locator('.meeting-panel summary').click();
 assert.equal(await page.locator('.api-text').count(),2);
 assert.equal(await page.locator('.api-text').last().textContent(),'CLIENT__1 approved <script>literal</script>');
 assert.equal(await page.locator('.api-text script').count(),0);

 await page.locator('.meeting-panel summary').click();
 assert.equal(await page.locator('.user .body').textContent(),'# Raw transcript');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert.deepEqual(errors,[]);
 await page.screenshot({path:`data/markdown-${width}.png`});
 console.log(`PASS Markdown, sanitization and layout at ${width}px`);
 if(width<760)await page.locator('#toggle-sidebar').click();
 let deleted=false;
 await page.route('**/api/sessions/test',r=>{if(r.request().method()==='DELETE'){deleted=true;return r.fulfill({json:{deleted:'test'}})}return r.fulfill({json:session})});
 await page.route('**/api/sessions',r=>r.fulfill({json:deleted?[]:[session]}));
 page.once('dialog',dialog=>dialog.dismiss());
 await page.getByRole('button',{name:'Delete conversation: Markdown test',exact:true}).click();
 assert.equal(deleted,false);
 page.once('dialog',dialog=>dialog.accept());
 await page.getByRole('button',{name:'Delete conversation: Markdown test',exact:true}).click();
 await page.getByRole('heading',{name:'Space to think freely.'}).waitFor();
 await page.waitForFunction(()=>document.querySelectorAll('.session-row').length===0);
 assert.equal(deleted,true);
 console.log(`PASS delete confirmation and selected-chat reset at ${width}px`);
 await page.close();
}
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
