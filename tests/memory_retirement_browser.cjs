const {chromium}=require(process.argv[2]);
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
for(const width of [390,1280]){
 const page=await browser.newPage({viewport:{width,height:844}});const errors=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>{
  const url=new URL(route.request().url());if(url.hostname!=='retirement.test')return route.abort();
  requests.push(url.pathname);
  if(url.pathname==='/api/settings')return route.fulfill({json:{model:'test/model',sensitive_terms:'',favourites:[],monthly_cost_ceiling_usd:'2.00',openrouter:{configured:false},monthly_budget:{available:true,month:'2026-10',used_usd:0,remaining_usd:2,reserved_usd:0}}});
  if(url.pathname==='/api/openrouter/models'||url.pathname==='/api/sessions'||url.pathname==='/api/adviser/sessions')return route.fulfill({json:[]});
  if(url.pathname==='/api/personas')return route.fulfill({json:{revision:0,personas:[]}});
  if(url.pathname.startsWith('/api/'))return route.fulfill({status:501,json:{detail:{code:'NOT_IMPLEMENTED'}}});
  const pages={'/':'index.html','/memory':'memory.html','/settings':'settings.html','/adviser':'adviser.html'};
  const file=pages[url.pathname]||url.pathname.replace('/static/','');if(file.includes('..'))return route.abort();
  const target=path.join(process.cwd(),'static',file);if(!fs.existsSync(target))return route.fulfill({status:404,body:''});
  return route.fulfill({body:fs.readFileSync(target),contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':'text/html'});
 });
 await page.goto('http://retirement.test/memory');
 assert.equal(await page.getByText('Not yet implemented',{exact:true}).count(),3);
 for(const title of ['Action Register','Project Register','My Role'])assert(await page.getByRole('heading',{name:title,exact:true}).isVisible());
 assert.equal(await page.locator('input,textarea,select,form').count(),0);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 if(width===390){await page.getByRole('button',{name:'Toggle navigation'}).click();assert.equal(await page.locator('#toggle-sidebar').getAttribute('aria-expanded'),'true');await page.getByRole('button',{name:'Toggle navigation'}).click();}
 await page.screenshot({path:`MEMORY_RETIREMENT_UI_${width}.png`,fullPage:true});
 for(const route of ['/','/adviser','/settings']){await page.goto('http://retirement.test'+route);assert(await page.locator('nav.main-nav a[href="/memory"]').count());}
 assert.equal(await page.locator('#memory-schedule-form,#meeting-role-context').count(),0);
 assert(!requests.some(x=>x.startsWith('/api/memory')));assert.deepEqual(errors,[]);
 console.log(`PASS ${width}px: Memory placeholders, navigation, no memory requests/controls, accepted pages load, no JS errors`);await page.close();
}
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
