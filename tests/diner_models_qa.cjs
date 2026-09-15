// Browser smoke test of the shared construction/RP panel; no live generation or story data.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const http=require('http'),fs=require('fs'),path=require('path'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..');
const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><link rel="stylesheet" href="/style.css">
<body style="padding:24px;max-width:520px"><h2>生成设置</h2><div id="agent-panel" class="agent-panel"></div>
<button id="send">发送测试小票</button><script type="module">
import {AgentPanel} from '/agent-panel.js';
const workspace=new URL(location.href).searchParams.get('workspace')||'workspace-a';
window.requests=[];
const api=async(op,data={})=>{
  if(op==='agent_providers')return {providers:['codex','opencode','opencode_server'].map(id=>({id,available:true}))};
  if(op==='agent_models'){
    if(window.holdModels)await new Promise(resolve=>window.releaseModels=resolve);
    if(window.failModels)throw Error('模拟列表断线');
    if(window.oldBackend&&data.provider==='codex')return {models:[],hint:'可使用平台默认模型，或手动填写本机 Codex 支持的模型 ID。'};
    return {supports_reasoning_effort:data.provider==='codex',models:data.provider==='codex'?[
      {id:'codex-a',name:'Codex A',reasoning_efforts:window.dropHigh?['low']:['low','high'],default_reasoning_effort:'low'},
      {id:'codex-b',name:'Codex B',reasoning_efforts:['low','medium'],default_reasoning_effort:'medium'},
      {id:'codex-none',name:'Codex without reasoning',reasoning_efforts:[]}
    ]:[{id:'vendor/model-a',name:'Model A · vendor/model-a'},{id:'vendor/model-b',name:'Model B · vendor/model-b'}]};
  }
  if(op==='agent_start'){window.requests.push(data);return {id:'fake',status:'incomplete',model:data.model,reasoning_effort:data.reasoning_effort,helper_policy:data.provider==='codex'?'session':'main_agent_only',message:'测试请求已接收'};}
};
window.panel=new AgentPanel(api,()=>[workspace,'bible','test','topic']);
await panel.init();document.querySelector('#send').onclick=()=>panel.action(()=>panel.send({ticket_path:'saved-ticket.json'}));
window.ready=true;
</script></body></html>`;
const server=http.createServer((req,res)=>{
  const name=req.url.split('?')[0];
  if(name==='/agent-panel.js'||name==='/style.css'){
    res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':'text/css');
    res.end(fs.readFileSync(path.join(root,'workbench/static',name.slice(1))));
  }else{res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);}
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const url='http://127.0.0.1:'+server.address().port;
  let browser;
  try{
    browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
    const page=await browser.newPage({viewport:{width:540,height:940}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    const ready=()=>page.waitForFunction(()=>window.ready);
    const selected=value=>page.waitForFunction(value=>document.querySelector('#agent-model').value===value,value);
    await page.goto(url);await ready();assert.equal(await page.locator('#agent-model-fields').isVisible(),false);
    await page.locator('#agent-provider').selectOption('opencode');
    await page.locator('#agent-model option[value="vendor/model-b"]').waitFor({state:'attached'});
    await page.locator('#agent-model').selectOption('vendor/model-b');await page.locator('#send').click();
    assert.equal(await page.evaluate(()=>requests[0].model),'vendor/model-b');
    assert.match(await page.locator('#agent-job-model').textContent(),/vendor\/model-b.*主 Agent/);
    await page.reload();await ready();await selected('vendor/model-b');
    await page.locator('#agent-provider').selectOption('codex');await selected('');
    await page.locator('#agent-model').selectOption('custom');await page.locator('#agent-model-custom').fill('codex-model');
    await page.locator('#agent-provider').selectOption('opencode_server');await selected('');
    await page.locator('#agent-model').selectOption('vendor/model-a');
    await page.locator('#agent-retry').click();
    assert.deepEqual(await page.evaluate(()=>requests[0]),{ticket_path:'saved-ticket.json',provider:'opencode_server',retry:true,model:'vendor/model-a'});
    await page.locator('#agent-provider').selectOption('opencode');await selected('vendor/model-b');
    await page.evaluate(()=>window.failModels=true);await page.locator('#agent-refresh').click();
    await page.waitForFunction(()=>document.querySelector('#agent-model-hint').textContent.includes('模拟列表断线'));
    assert.equal(await page.locator('#agent-model-custom').inputValue(),'vendor/model-b');
    await page.locator('#send').click();assert.equal(await page.evaluate(()=>requests.at(-1).model),'vendor/model-b');
    await page.locator('#agent-model-custom').fill('');await page.locator('#send').click();
    assert.match(await page.locator('#agent-status').textContent(),/请填写模型 ID/);
    await page.evaluate(()=>{window.failModels=false;window.holdModels=true;});
    await page.locator('#agent-provider').selectOption('opencode_server');
    await page.waitForFunction(()=>!!window.releaseModels);
    await page.evaluate(()=>window.holdModels=false);
    await page.locator('#agent-provider').selectOption('codex');await selected('custom');
    await page.evaluate(()=>window.releaseModels());
    assert.equal(await page.locator('#agent-model-custom').inputValue(),'codex-model');
    await page.locator('#agent-provider').selectOption('opencode_server');await selected('vendor/model-a');
    await page.goto(url+'?workspace=workspace-b');await ready();await selected('');
    await page.goto(url);await ready();await selected('vendor/model-a');
    assert.equal(await page.locator('#agent-reasoning-field').isVisible(),false);
    await page.locator('#agent-provider').selectOption('codex');
    await page.locator('#agent-model option[value="codex-a"]').waitFor({state:'attached'});
    await page.locator('#agent-model').selectOption('codex-a');
    assert.deepEqual(await page.locator('#agent-reasoning option').evaluateAll(rows=>rows.map(r=>r.value)),['','low','high']);
    await page.locator('#agent-reasoning').selectOption('high');await page.locator('#send').click();
    assert.equal(await page.evaluate(()=>requests.at(-1).reasoning_effort),'high');
    assert.match(await page.locator('#agent-job-model').textContent(),/codex-a.*推理强度：high/);
    await page.locator('#agent-model').selectOption('codex-b');
    assert.equal(await page.locator('#agent-reasoning').inputValue(),'');
    assert.deepEqual(await page.locator('#agent-reasoning option').evaluateAll(rows=>rows.map(r=>r.value)),['','low','medium']);
    await page.locator('#agent-reasoning').selectOption('medium');
    await page.locator('#agent-model').selectOption('codex-a');
    assert.equal(await page.locator('#agent-reasoning').inputValue(),'high');
    await page.reload();await ready();await selected('codex-a');
    assert.equal(await page.locator('#agent-reasoning').inputValue(),'high');
    await page.evaluate(()=>window.dropHigh=true);await page.locator('#agent-refresh').click();
    await page.waitForFunction(()=>document.querySelector('#agent-reasoning-hint').textContent.includes('不再支持'));
    await page.locator('#send').click();assert.equal(await page.evaluate(()=>requests.length),0);
    await page.locator('#agent-reasoning').selectOption('low');await page.locator('#agent-retry').click();
    assert.deepEqual(await page.evaluate(()=>requests.at(-1)),{ticket_path:'saved-ticket.json',provider:'codex',retry:true,model:'codex-a',reasoning_effort:'low'});
    await page.locator('#agent-provider').selectOption('manual');await page.locator('#agent-retry').click();
    assert.equal(await page.evaluate(()=>requests.at(-1).reasoning_effort),'low');
    await page.locator('#agent-provider').selectOption('codex');await selected('codex-a');
    await page.locator('#agent-model').selectOption('codex-none');
    assert.equal(await page.locator('#agent-reasoning option').count(),1);
    await page.locator('#agent-model').selectOption('codex-a');
    await page.evaluate(()=>window.failModels=true);await page.locator('#agent-refresh').click();
    await page.waitForFunction(()=>document.querySelector('#agent-model-hint').textContent.includes('模拟列表断线'));
    assert.equal(await page.locator('#agent-reasoning').inputValue(),'low');
    await page.locator('#send').click();assert.equal(await page.evaluate(()=>requests.at(-1).reasoning_effort),'low');
    await page.evaluate(()=>window.failModels=false);await page.locator('#agent-refresh').click();await selected('codex-a');
    await page.goto(url+'?workspace=workspace-b');await ready();await selected('');
    assert.equal(await page.locator('#agent-reasoning').inputValue(),'');
    await page.goto(url);await ready();await selected('codex-a');
    assert.equal(await page.locator('#agent-reasoning').inputValue(),'low');
    await page.setViewportSize({width:390,height:940});
    await page.evaluate(()=>window.oldBackend=true);await page.locator('#agent-refresh').click();
    await page.waitForFunction(()=>document.querySelector('#agent-model-hint').textContent.includes('后台仍是旧版本'));
    assert.equal(await page.locator('#agent-reasoning').isDisabled(),true);
    const beforeOld=await page.evaluate(()=>requests.length);
    await page.locator('#send').click();assert.equal(await page.evaluate(()=>requests.length),beforeOld);
    await page.evaluate(()=>window.oldBackend=false);await page.locator('#agent-refresh').click();
    await selected('codex-a');assert.equal(await page.locator('#agent-reasoning').isDisabled(),false);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    const artifacts=process.env.QA_OUTPUT||path.join(root,'output/playwright/model-selection');fs.mkdirSync(artifacts,{recursive:true});
    await page.screenshot({path:path.join(artifacts,'panel.png'),fullPage:true});
    assert.deepEqual(errors,[]);console.log('PASS: model and effort selection, per-model persistence, provider/workspace isolation, retry, offline fallback, unsupported effort, empty input, stale response, mobile layout');
  }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
