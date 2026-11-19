// Mocked Settings UI only; no live server, credentials or provider traffic.
const {chromium}=require(process.argv[2]);
const fs=require('node:fs'), path=require('node:path'), assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  page.setDefaultTimeout(7000); const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let ceiling='2.00', includeRole=true;
  const settings=()=>({model:'test/model',sensitive_terms:'',openrouter:{configured:false},monthly_cost_ceiling_usd:ceiling,include_meeting_role_context:includeRole,
   monthly_budget:{month:'2026-10',ceiling_usd:ceiling,used_usd:'.25',reserved_usd:'.10',remaining_usd:String(Math.max(0,Number(ceiling)-.25)),available:true}});
  await page.route('**/*',route=>{
   const url=new URL(route.request().url());if(url.hostname!=='budget.test')return route.abort();
   if(url.pathname==='/api/settings'){
    if(route.request().method()==='PUT'){const body=route.request().postDataJSON();ceiling=body.monthly_cost_ceiling_usd;includeRole=body.include_meeting_role_context;}
    return route.fulfill({json:settings()});
   }
   if(url.pathname==='/api/openrouter/models')return route.fulfill({json:[]});
   if(url.pathname==='/api/memory/status')return route.fulfill({json:{available:false,error:'Offline fixture'}});
   if(url.pathname.startsWith('/api/'))return route.fulfill({status:404,json:{detail:'Not mocked'}});
   const file=url.pathname==='/settings'?'settings.html':url.pathname.replace('/static/','');
   if(file.includes('..'))return route.abort();
   const target=path.join(process.cwd(),'static',file);
   if(!fs.existsSync(target))return route.fulfill({status:404,body:''});
   return route.fulfill({body:fs.readFileSync(target),contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':'text/html'});
  });
  await page.goto('http://budget.test/settings');
  await page.waitForFunction(()=>!document.getElementById('save').disabled);
  const field=page.getByLabel('Cost ceiling per month (USD)',{exact:true});
  assert.equal(await field.inputValue(),'2.00');
  assert.match(await page.locator('#monthly-budget-status').textContent(),/0.2500/);
  assert.equal(await page.locator('#meeting-role-context').count(),0);
  await field.fill('3.50'); await page.getByRole('button',{name:'Save settings',exact:true}).click();
  await page.getByText('Settings saved locally.',{exact:true}).waitFor();assert.equal(ceiling,'3.50');
  await page.reload();await page.waitForFunction(()=>!document.getElementById('save').disabled);assert.equal(await field.inputValue(),'3.50');
  await field.fill('0');await page.getByRole('button',{name:'Save settings',exact:true}).click();
  await page.getByText('Settings saved locally.',{exact:true}).waitFor();assert.equal(ceiling,'0');
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.deepEqual(errors,[]);console.log('PASS: default $2, save/reload, usage display, zero ceiling, mobile containment; no page errors.');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
