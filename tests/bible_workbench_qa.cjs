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
    assert.equal(errors.length,0,errors.join('\n'));
    assert.equal(requests.filter(r=>/\/api\/(?:bible_prepare|bible_commit|prepare|commit)(?:\?|$)/.test(r.url)).length,0);
    const result={status:'passed',workspace,checks:['desktop map','history original','local draft','short/full ticket','CLI prepare and commit','reload','revision retains source','night theme','mobile outline/detail','session ticket regression','browser never calls generation endpoints'],browserErrors:errors};
    fs.writeFileSync(path.join(artifacts,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
  }finally{await browser.close();server.kill();}
})().catch(error=>{server.kill();console.error(error);process.exitCode=1;});
