// Real-browser roundtrip. All writes go to a fresh temporary workspace.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),os=require('os'),cp=require('child_process'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..'),workspace=fs.mkdtempSync(path.join(os.tmpdir(),'story-bible-qa-'));
const python=fs.existsSync(path.join(root,'.venv-workbench/Scripts/python.exe'))?path.join(root,'.venv-workbench/Scripts/python.exe'):'python';
const env={...process.env,PYTHONIOENCODING:'utf-8',PYTHONDONTWRITEBYTECODE:'1'};
const artifacts=path.join(root,'output/playwright/story-bible');fs.mkdirSync(artifacts,{recursive:true});
function run(args){const r=cp.spawnSync(python,['-B',...args],{cwd:root,encoding:'utf8',windowsHide:true,env});assert.equal(r.status,0,r.stderr||r.stdout);return JSON.parse(r.stdout);}
const fixture=run(['tests/bible_fixture.py','--workspace',workspace]);
const cli=path.join(root,'.agents/skills/story-bible-studio/scripts/story_studio.py');
function core(op,payload){const file=path.join(workspace,'call.json');fs.writeFileSync(file,JSON.stringify(payload));return run([cli,'workbench','--workspace',workspace,'--operation',op,'--payload-file',file]);}
function ticket(file){return run([cli,'ticket','--workspace',workspace,'--file',file]);}
const server=cp.spawn(python,['-B','workbench/server.py','--workspace',workspace,'--port','0'],{cwd:root,windowsHide:true,env,stdio:['ignore','pipe','pipe']});
(async()=>{
  const origin=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('server startup timeout')),15000);server.stdout.on('data',chunk=>{text+=chunk;const match=text.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0]);}});server.on('exit',code=>{clearTimeout(timer);reject(Error('server exited '+code));});});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
  const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/'))requests.push({url:r.url(),method:r.method()});});
  const url=topic=>origin+'/bible?'+new URLSearchParams({project:fixture.project,topic});
  const lastTicket=()=>page.evaluate(()=>{const key=Object.keys(localStorage).find(k=>k.startsWith('diner:bible-ticket:'));return JSON.parse(localStorage.getItem(key));});
  try{
    await page.goto(url(fixture.topic));await page.locator('#topic-title').filter({hasText:'没有邮戳'}).waitFor();
    assert.ok(await page.locator('.map-node').count()>5);
    const narrow=await page.locator('#map-viewport').evaluate(el=>el.clientWidth);await page.locator('#close-detail').click();assert.ok(await page.locator('#map-viewport').evaluate(el=>el.clientWidth)>narrow);await page.locator(`[data-node="${fixture.topic}"] button`).first().click();
    fs.writeFileSync(path.join(artifacts,'initial-accessibility.txt'),await page.locator('body').ariaSnapshot());
    await page.screenshot({path:path.join(artifacts,'01-desktop-map.png'),fullPage:true});
    await page.locator('.history-row').first().click();await page.locator('#history-original').waitFor();
    assert.match(await page.locator('#history-original').innerText(),/给我几个可以持续展开/);
    await page.screenshot({path:path.join(artifacts,'02-discussion-history.png'),fullPage:true});
    await page.locator('#clear-history-focus').click();await page.locator('#idea-input').fill('先把钟声迟到的原因留白，比较河岸与钟楼两条线。');
    await page.locator('#save-idea').click();await page.waitForFunction(()=>document.querySelector('#topic-decision').textContent.includes('先把钟声'));
    await page.locator('#continue-idea').click();await page.locator('#bible-ticket-dialog').waitFor();
    const short=await page.locator('#bible-ticket-text').inputValue();await page.locator('#bible-ticket-format').click();assert.ok((await page.locator('#bible-ticket-text').inputValue()).length>short.length);
    await page.locator('#bible-ticket-dialog [data-close]').click();const req=await lastTicket();const prepared=ticket(req.ticket_path);assert.equal(prepared.status,'ready');assert.equal(prepared.context.user_text,req.user_text);
    const receipt=core('bible_commit',{project:fixture.project,payload:{operation_id:prepared.operation_id,status:'open',decision:'两条调查线并行，钟声迟到的原因保留。',assistant_text:'两条线都可以从眼前展开。河岸负责外部线索，钟楼保留她熟悉却开始不可靠的日常。',next_prompt:{question:'先从哪里入手？',options:{'1':'河岸渡船','2':'钟楼夹层'}}}});
    assert.equal(receipt.status,'committed');await page.waitForFunction(()=>document.querySelector('#topic-decision').textContent.includes('两条调查线并行'));
    await page.reload();await page.locator('#topic-title').filter({hasText:'没有邮戳'}).waitFor();assert.match(await page.locator('#topic-decision').innerText(),/两条调查线/);
    await page.locator('.history-row').filter({hasText:'留下三种信件来历'}).click();await page.locator('#idea-input').fill('回到北岸渡船这个旧方向，暂时只讨论。');await page.locator('#continue-idea').click();await page.locator('#bible-ticket-dialog').waitFor();await page.locator('#bible-ticket-dialog [data-close]').click();
    const oldQuestionTicket=await lastTicket(),oldQuestion=ticket(oldQuestionTicket.ticket_path);assert.equal(oldQuestion.context.prompt,null);assert.equal(oldQuestion.context.history[0].prompt.options['1'],'北岸渡船上的送信人');core('bible_commit',{project:fixture.project,payload:{operation_id:oldQuestion.operation_id,status:'parked',decision:'旧方向先保留备选。',assistant_text:'这条旧方向先保留，当前设定继续有效。'}});
    await page.goto(url(fixture.character));await page.locator('#topic-title').filter({hasText:'米拉为什么'}).waitFor();assert.equal(await page.locator('#save-idea').isDisabled(),true);
    await page.locator('#idea-input').fill('把等待一封信改成寻找失踪的送信人。');await page.locator('#revise-idea').click();await page.locator('#bible-ticket-dialog').waitFor();await page.locator('#bible-ticket-dialog [data-close]').click();
    const revision=await lastTicket(),revisionPrepared=ticket(revision.ticket_path);assert.equal(revisionPrepared.status,'ready');
    const before=fs.readFileSync(path.join(workspace,'作品',fixture.project,'StoryBible/角色/米拉.md'),'utf8');
    core('bible_commit',{project:fixture.project,payload:{operation_id:revisionPrepared.operation_id,status:'complete',decision:'她守着钟楼，也在寻找失踪的送信人。',assistant_text:'责任仍然在前，但她已经开始主动寻找。',edits:{'角色/米拉.md':before.replace('她也在等一封迟来的信。','她也在寻找失踪的送信人。')}}});
    await page.waitForFunction(()=>document.querySelector('#topic-status').textContent==='已采用');await page.locator('#show-past').check();await page.screenshot({path:path.join(artifacts,'03-revision.png'),fullPage:true});
    await page.locator('#theme-button').click();await page.screenshot({path:path.join(artifacts,'04-night.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});await page.locator('#close-detail').click();await page.locator('#outline-mode').click();await page.screenshot({path:path.join(artifacts,'05-mobile-outline.png'),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.locator('#bible-outline button').filter({hasText:'米拉为什么'}).last().click();await page.screenshot({path:path.join(artifacts,'06-mobile-detail.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/');await page.locator('#session-list button').filter({hasText:fixture.session}).click();await page.locator('#user-input').fill('我问她钟声为什么迟了。');await page.locator('#continue-button').click();await page.locator('#ticket-dialog').waitFor();assert.match(await page.locator('#ticket-text').inputValue(),/ticket --workspace/);await page.locator('#ticket-format').click();assert.match(await page.locator('#ticket-text').inputValue(),/--operation prepare/);await page.screenshot({path:path.join(artifacts,'07-session-ticket.png'),fullPage:true});
    // UI dispatch is simulated here; test_agent_runner.py tests real subprocesses and core commits.
    await page.locator('#ticket-dialog [data-close]').click();
    await page.route('**/api/agent_providers*',route=>route.fulfill({json:{providers:[{id:'codex',available:true},{id:'opencode',available:true},{id:'opencode_server',available:true,hint:'连接 http://127.0.0.1:4096，桌面端使用同一服务。'}]}}));
    let mode='commit',job=null,dispatches=[];
    await page.route('**/api/agent_start',async route=>{
      const input=route.request().postDataJSON();dispatches.push(input);
      job={id:'a'.repeat(32),status:'running',message:'Agent 正在处理这张小票。',reply:''};
      if(input.provider==='opencode_server')Object.assign(job,{server_url:'http://127.0.0.1:4096',platform_session:'ses_browsercheck',server_pending:true});
      if(mode!=='wait'){
        const prepared=ticket(input.ticket_path);
        if(mode==='prepare'){job.status='incomplete';job.message='尚未提交，可重试。';}
        else{
          if(prepared.kind==='bible')core('bible_commit',{project:fixture.project,payload:{operation_id:prepared.operation_id,status:'open',decision:'直接生成已保存，河岸先行。',assistant_text:'先调查河岸。<script>这只是文字</script>',next_prompt:{question:'带上哪件工具？',options:{'1':'怀表','2':'旧地图'}}}});
          else core('commit',{project:fixture.project,session:fixture.session,operation_id:prepared.operation_id,prose:'直接生成的回复：米拉把钟面转向窗边。',regenerate:prepared.regenerate});
          job.reply='先调查河岸。<script>这只是文字</script>';
        }
      }
      await route.fulfill({json:job});
    });
    await page.route('**/api/agent_status?*',route=>{if(mode==='commit')job={...job,status:'completed',server_pending:false,message:'已提交，工作台会自动更新。'};return route.fulfill({json:job});});
    await page.route('**/api/agent_stop',route=>{job={...job,status:'stopped',server_pending:false,message:'生成已停止，小票仍保留。'};return route.fulfill({json:job});});
    await page.goto(url(fixture.topic));await page.locator('#topic-title').waitFor();
    await page.locator('#agent-provider').selectOption('codex');await page.locator('#idea-input').fill('从河岸接着讨论。');await page.locator('#continue-idea').click();
    await page.waitForFunction(()=>document.querySelector('#topic-decision').textContent.includes('直接生成已保存'));
    assert.equal(await page.locator('#bible-ticket-dialog').isVisible(),false);
    await page.reload();await page.waitForFunction(()=>document.querySelector('#agent-status').textContent.includes('已提交'));
    assert.equal(await page.locator('#agent-provider').inputValue(),'codex');
    assert.match(await page.locator('#agent-reply').innerText(),/<script>/);assert.equal(await page.locator('#agent-reply script').count(),0);
    await page.screenshot({path:path.join(artifacts,'08-direct-construction.png'),fullPage:true});
    mode='prepare';await page.locator('#idea-input').fill('继续比较工具。');await page.locator('#continue-idea').click();await page.locator('#agent-retry').waitFor();
    const savedPath=dispatches.at(-1).ticket_path;mode='commit';await page.locator('#agent-provider').selectOption('opencode_server');await page.locator('#agent-refresh').click();assert.match(await page.locator('#agent-hint').innerText(),/同一服务/);await page.locator('#agent-retry').click();
    await page.waitForFunction(()=>document.querySelector('#agent-status').textContent.includes('已提交'));
    assert.equal(dispatches.at(-1).ticket_path,savedPath);assert.equal(dispatches.at(-1).retry,true);assert.equal(dispatches.at(-1).provider,'opencode_server');
    assert.match(await page.locator('#agent-session').innerText(),/ses_browsercheck/);assert.equal(await page.locator('#agent-copy-session').isVisible(),true);
    await page.screenshot({path:path.join(artifacts,'11-shared-backend.png'),fullPage:true});
    mode='wait';await page.locator('#idea-input').fill('测试停止时保留输入。');await page.locator('#continue-idea').click();await page.locator('#agent-stop').waitFor().catch(async error=>{await page.screenshot({path:path.join(artifacts,'dispatch-failure.png'),fullPage:true});console.error(JSON.stringify({toast:await page.locator('#toast').innerText(),status:await page.locator('#agent-status').innerText(),ticket:await lastTicket(),dispatches}));throw error;});
    job={...job,status:'unknown',message:'尚未确认后台任务停止。'};await page.waitForFunction(()=>document.querySelector('#agent-stop').textContent.includes('确认后台'));assert.equal(await page.locator('#agent-retry').isVisible(),false);await page.locator('#agent-stop').click();
    await page.waitForFunction(()=>document.querySelector('#agent-status').textContent.includes('已停止'));assert.equal(await page.locator('#idea-input').inputValue(),'测试停止时保留输入。');
    await page.goto(url(fixture.character));await page.locator('#topic-title').waitFor();assert.equal(await page.locator('#agent-status').innerText(),'');
    await page.setViewportSize({width:390,height:844});await page.goto(url(fixture.topic));await page.locator('#outline-mode').click();await page.locator('#bible-outline button').filter({hasText:'没有邮戳的信'}).first().click();await page.locator('#agent-provider').waitFor();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.equal(await page.locator('#agent-copy-session').isVisible(),true);await page.screenshot({path:path.join(artifacts,'09-mobile-direct.png'),fullPage:true});
    mode='commit';await page.setViewportSize({width:1440,height:1000});await page.goto(origin+'/');await page.locator('#session-list button').filter({hasText:fixture.session}).click();
    await page.locator('#agent-provider').selectOption('opencode');await page.locator('#user-input').fill('我让她看看窗边的光。');await page.locator('#continue-button').click();
    await page.waitForFunction(()=>document.querySelector('#agent-status').textContent.includes('已提交'));
    if(await page.locator('#new-content').isVisible())await page.locator('#new-content').click();
    await page.waitForFunction(()=>document.querySelector('#story').textContent.includes('直接生成的回复'));
    assert.equal(await page.locator('#ticket-dialog').isVisible(),false);
    await page.screenshot({path:path.join(artifacts,'10-direct-session.png'),fullPage:true});
    assert.equal(errors.length,0,errors.join('\n'));
    assert.equal(requests.filter(r=>/\/api\/(?:bible_prepare|bible_commit|prepare|commit)(?:\?|$)/.test(r.url)).length,0);
    const result={status:'passed',workspace,checks:['desktop map','history original','local draft','short/full ticket','CLI prepare and commit','reload','revision retains source','night theme','mobile outline/detail','session ticket regression','browser never calls generation endpoints','direct construction (simulated provider)','direct RP (simulated provider)','retry original ticket through shared backend','connection refresh and session metadata','unknown remote job stop and retry guard','stop preserves input','job isolation and reload','escaped agent reply','mobile direct generation'],browserErrors:errors};
    fs.writeFileSync(path.join(artifacts,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
  }finally{await browser.close();server.kill();}
})().catch(error=>{server.kill();console.error(error);process.exitCode=1;});
