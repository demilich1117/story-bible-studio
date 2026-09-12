// Real browser smoke test against a separately created neutral demo workspace.
// PLAYWRIGHT_MODULE may point to the bundled Playwright directory; no npm install required.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),cp=require('child_process'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..'),workspace=path.join(root,'.workbench/requirements-recovery-qa');
const artifacts=path.join(root,'output/playwright/requirements-recovery');fs.mkdirSync(artifacts,{recursive:true});
const project='雨夜公路 · Route 66',session='创作恢复验证-'+Date.now(),target={project,session};
const cli=path.join(root,'.agents/skills/story-bible-studio/scripts/story_studio.py');
function core(operation,payload){
  const file=path.join(workspace,'qa-call.json');fs.writeFileSync(file,JSON.stringify(payload));
  const result=cp.spawnSync(path.join(root,'.venv-workbench/Scripts/python.exe'),['-B',cli,'workbench','--workspace',workspace,'--operation',operation,'--payload-file',file],{encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);
}
(async()=>{
  core('new_session',{project,session_id:session,expected_version:core('project',{project}).version});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  try{
    await page.goto(process.argv[2]);await page.getByRole('button',{name:new RegExp('^'+session)}).click();
    await page.locator('#add-banned').click();await page.getByRole('textbox',{name:'禁词或短语',exact:true}).fill('不容置疑');await page.locator('#save-requirements').click();
    await page.getByText('1 条已保存',{exact:true}).waitFor();
    await page.locator('#add-guidance').click();await page.getByRole('textbox',{name:'自然语言创作要求',exact:true}).fill('保留她的克制，不把自信不断写成控制欲。');await page.locator('#save-requirements').click();
    await page.getByText('2 条已保存',{exact:true}).waitFor();
    await page.reload();await page.getByText('2 条已保存',{exact:true}).waitFor();
    let req=core('requirements',{...target,action:'show'});assert.equal(req.items.length,2);
    // Keep edits made while an earlier save response is still in flight.
    let releaseResponse,requestArrived;
    const arrived=new Promise(resolve=>requestArrived=resolve);
    await page.route('**/api/requirements',async route=>{
      const response=await route.fetch();await new Promise(resolve=>{releaseResponse=resolve;requestArrived();});await route.fulfill({response});
    });
    await page.getByRole('textbox',{name:'自然语言创作要求',exact:true}).fill('本次提交的要求。');
    await page.locator('#save-requirements').click();await arrived;
    await page.getByRole('textbox',{name:'自然语言创作要求',exact:true}).fill('保存期间继续编辑的要求。');releaseResponse();
    await page.getByText('已保存提交的要求；保存期间的新编辑仍未保存。',{exact:true}).waitFor();
    assert.equal(await page.getByRole('textbox',{name:'自然语言创作要求',exact:true}).inputValue(),'保存期间继续编辑的要求。');
    await page.unroute('**/api/requirements');await page.locator('#save-requirements').click();await page.locator('#requirements-hint').getByText('已保存；下次准备回复自动生效。',{exact:true}).waitFor();
    req=core('requirements',{...target,action:'show'});
    // Unsaved local text survives a concurrent author edit, then merge explicitly.
    await page.getByRole('textbox',{name:'自然语言创作要求',exact:true}).fill('保留我的未保存要求。');
    core('requirements',{...target,action:'save',expected_version:req.version,items:[...req.items,{id:'remote',type:'guidance',text:'另一端新增的要求。'}]});
    await page.locator('#reload-requirements').waitFor({state:'visible'});
    assert.equal(await page.getByRole('textbox',{name:'自然语言创作要求',exact:true}).inputValue(),'保留我的未保存要求。');
    await page.locator('#reload-requirements').click();await page.locator('#merge-guidance').fill('保留我的未保存要求。\n\n另一端新增的要求。');await page.locator('#accept-merge').click();await page.locator('#save-requirements').click();await page.getByText('3 条已保存',{exact:true}).waitFor();
    const ready=core('prepare',{...target,user_text:'原始续写输入。',expected_version:core('snapshot',target).version,override:{interaction_preset:'short-rp'}});
    assert.equal(ready.status,'ready');
    fs.writeFileSync(path.join(workspace,'作品',project,'会话',session,'.runtime/current/response.md'),'六月把菜单推到窗边。');
    await page.getByRole('button',{name:'保留草稿并重新开始',exact:true}).waitFor();
    assert.equal(await page.locator('#continue-button').isDisabled(),true);
    await page.getByRole('textbox',{name:'禁词或短语',exact:true}).fill('彻底');await page.locator('#save-requirements').click();
    await page.locator('#requirements-hint').getByText(/正在生成的回复沿用原要求/).waitFor();
    assert.equal(core('operation',{...target,operation_id:ready.operation_id}).requirements.items[0].text,'不容置疑');
    await page.screenshot({path:path.join(artifacts,'desktop-ready.png'),fullPage:true});
    await page.getByRole('button',{name:'复制恢复指令',exact:true}).click();await page.locator('#ticket-dialog').waitFor({state:'visible'});
    assert.ok((await page.locator('#ticket-text').inputValue()).includes(ready.operation_id));await page.locator('#ticket-dialog [data-close]').click();
    await page.getByRole('button',{name:'保留草稿并重新开始',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#user-input').value==='原始续写输入。');
    await page.waitForFunction(()=>!document.querySelector('#continue-button').disabled);
    assert.equal(core('snapshot',target).pending,false);
    assert.equal(core('snapshot',target).last_restart.override.interaction_preset,'short-rp');
    await page.reload();await page.locator('#restore-restart').waitFor({state:'visible'});await page.locator('#restore-restart').click();
    assert.equal(await page.locator('#user-input').inputValue(),'原始续写输入。');
    // Keyboard and narrow layout, then dark theme on the same actual page.
    await page.setViewportSize({width:390,height:844});await page.locator('#settings-button').click();
    await page.getByRole('textbox',{name:'禁词或短语',exact:true}).focus();await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'删除这条要求');
    await page.screenshot({path:path.join(artifacts,'mobile-light.png'),fullPage:true});
    await page.keyboard.press('Escape');await page.locator('#reading-button').click();await page.locator('#theme').selectOption('dark');await page.locator('#reading-dialog [data-close]').click();
    await page.locator('#settings-button').click();await page.screenshot({path:path.join(artifacts,'mobile-dark.png'),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.keyboard.press('Escape');await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:path.join(artifacts,'desktop-dark.png'),fullPage:true});
    const next=core('prepare',{...target,user_text:'原始续写输入。',expected_version:core('snapshot',target).version});
    core('commit',{...target,operation_id:next.operation_id,prose:'六月把菜单推到窗边。'});
    const regen=core('prepare',{...target,user_text:'少用情绪总结。',regenerate:true,expected_version:core('snapshot',target).version});
    const restartRegen=page.locator(`[data-operation-id="${regen.operation_id}"]`).getByRole('button',{name:'保留草稿并重新开始',exact:true});await restartRegen.waitFor();
    page.once('dialog',dialog=>dialog.accept());
    await restartRegen.click();
    await page.waitForFunction(()=>document.querySelector('#user-input').value==='少用情绪总结。'&&!document.querySelector('#regen-button').disabled);
    assert.equal(await page.locator('#restore-composer-backup').isVisible(),true);
    await page.locator('#continue-button').click();await page.getByText(/找回的是重生成要求，请使用重生成按钮/).waitFor();
    await page.locator('#regen-button').click();await page.locator('#ticket-dialog').waitFor({state:'visible'});
    const requestId=(await page.locator('#ticket-text').inputValue()).match(/执行工作台请求 ([a-f0-9]+)/)[1];
    const preparedRegen=core('prepare',{...target,request_id:requestId});assert.equal(preparedRegen.regenerate,true);
    assert.ok(preparedRegen.context.includes('原始续写输入。'));
    core('commit',{...target,operation_id:preparedRegen.operation_id,regenerate:true,prose:'六月将铅笔夹进菜单。'});
    assert.deepEqual(errors,[]);fs.writeFileSync(path.join(artifacts,'result.json'),JSON.stringify({passed:true,errors,operation_id:ready.operation_id},null,2));
    console.log('Browser QA passed: save, reload, merge, ready snapshot, recovery ticket, archive, restore, regeneration type/backup, keyboard, narrow/light/dark.');
  }catch(error){await page.screenshot({path:path.join(artifacts,'failure.png'),fullPage:true});fs.writeFileSync(path.join(artifacts,'failure.txt'),await page.locator('body').innerText());throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
