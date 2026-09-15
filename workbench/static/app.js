import {AgentPanel} from './agent-panel.js';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const state = {token: '', projects: [], project: null, session: null, data: null, patch: {}, override: null, preview: null, scope: 'session', connections: {}, serial: 0, ticket: null, workspaceId: '', refreshSerial: 0};
const unsavedStorage = new Map();
const storage = {get(k, fallback) {if(unsavedStorage.has(k))return unsavedStorage.get(k);try {return JSON.parse(localStorage.getItem('diner:' + k)) ?? fallback;} catch {return fallback;}}, set(k, v) {try {localStorage.setItem('diner:' + k, JSON.stringify(v));unsavedStorage.delete(k);return true;} catch {unsavedStorage.set(k,v);$('#storage-warning').hidden=false;return false;}}};
let writing=false;
const escape = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[x]));
const sessionKey = (project,session) => JSON.stringify([state.workspaceId,project,session]);
const key = () => sessionKey(state.project?.id,state.session);
const lastSessionKey = id => `session:${state.workspaceId}:${id}`;
function toast(message) {const box = $('#toast'); box.textContent = message; box.hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => box.hidden = true, 6500);}
async function api(op, params = {}, write = false) {
  if(write&&writing)throw new Error('正在保存，请稍后再操作。');
  const buttons=write?$$('button:not([data-close])').map(el=>[el,el.disabled]):[];
  if(write){writing=true;buttons.forEach(([el])=>el.disabled=true);}
  try {
  const url = '/api/' + op + (write ? '' : '?' + new URLSearchParams(Object.entries(params).filter(([,v]) => v != null)));
  const response = await fetch(url, write ? {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Studio-Token': state.token}, body: JSON.stringify(params)} : {});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
  } finally {if(write){writing=false;buttons.forEach(([el,disabled])=>{if(el.isConnected)el.disabled=disabled;});}}
}
function task(fn) {return async event => {try {await fn(event);} catch (error) {toast(error.message);}};}
function pathGet(obj, path) {return path.split('.').reduce((o,k) => o?.[k], obj);}
function pathSet(obj, path, value) {const keys=path.split('.'); let cursor=obj; keys.slice(0,-1).forEach(k => cursor=cursor[k] ??= {}); cursor[keys.at(-1)]=value;}
function patchObject() {const obj={}; Object.entries(state.patch).forEach(([k,v])=>pathSet(obj,k,v)); return obj;}
function dirty() {return Object.keys(state.patch).length > 0;}
function hasUnsaved(){return dirty()||state.reqDirty;}
function option(value, label) {const el=document.createElement('option'); el.value=value; el.textContent=label; return el;}
function requireSession() {if (!state.data) throw new Error('先选一张会话卡座。');}

function renderSessions() {
  const query=$('#session-search').value.trim().toLocaleLowerCase();
  const sessions=(state.project?.sessions || []).filter(s=>s.id.toLocaleLowerCase().includes(query));
  $('#session-list').replaceChildren();
  if (!sessions.length) {$('#session-list').innerHTML=query?'<p class="muted small" role="status">没有匹配的会话。试试其他名称，或清空搜索。</p>':'<p class="muted small">这里还没有会话。<br>新开一张卡座吧。</p>';return;}
  for (const s of sessions) {
    const button=document.createElement('button'); button.className='session-item' + (state.session===s.id?' active':'');
    button.setAttribute('aria-current',state.session===s.id?'true':'false');
    const title=document.createElement('strong'); title.textContent=s.id;
    const sub=document.createElement('small'); sub.textContent=(s.pending?'待完成回合 · ':'')+(s.updated?new Date(s.updated*1000).toLocaleDateString('zh-CN'):'尚未开场');
    button.append(title,sub); button.onclick=task(()=>selectSession(s.id)); $('#session-list').append(button);
  }
}
async function loadProject(id, restore=true, navigation={}) {
  if (hasUnsaved() && !window.confirm('有尚未保存的设置或创作要求，放弃这些改动并切换作品？')) {$('#project-select').value=state.project?.id||'';return;}
  saveDraft(); const serial=++state.serial; const p=await api('project',{project:id}); if(serial!==state.serial)return;
  state.project=p; state.session=null; state.data=null; state.patch={}; state.override=null; state.preview=null;state.ticket=null;
  state.reqDirty=false;state.requirementsBase=null;state.requirementsDraft=[];state.recoveryOptions=null;renderRequirements();
  storage.set('project:'+state.workspaceId,id); $('#project-select').value=id; $('#session-search').value='';renderSessions(); renderConfig();
  $('#session-view').hidden=true; $('#composer').hidden=true; $('#welcome').hidden=false;
  const previous=storage.get(lastSessionKey(id),state.legacyStorage?storage.get(`session:${id}`,null):null);
  if (restore && previous && p.sessions.some(s=>s.id===previous)) await selectSession(previous,navigation);
  else if(navigation.mode!=='none')syncRoute(navigation.mode||'push');
  return true;
}
function saveDraft() {
  if(!state.session)return;
  const saved=storage.set('draft:'+key(),$('#user-input').value);
  $('#draft-hint').textContent=saved?'草稿已保存在此浏览器':'草稿尚未保存，请复制备份';
  const route=new URL(location.href).searchParams;
  if(route.get('project')===state.project?.id&&route.get('session')===state.session)history.replaceState({...history.state,position:readingPosition()},'');
  storage.set('position:'+key(),readingPosition());
  storage.set('override:'+key(),state.override);
  storage.set('ticket:'+key(),state.ticket);
  storage.set('recovery-options:'+key(),state.recoveryOptions||null);
}
function readingTop(){return parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--workbench-top'))||110;}
function readingPosition(){
  const top=readingTop(),article=$$('#story [data-turn]').find(el=>el.getBoundingClientRect().bottom>top);
  return {turn:article?Number(article.dataset.turn):null,offset:article?article.getBoundingClientRect().top-top:0,scrollY:window.scrollY,first:state.data?.turns[0]?.turn};
}
function restorePosition(position){
  if(!position)return;
  const article=position.turn?$(`#story [data-turn="${position.turn}"]`):null;
  if(article)window.scrollBy(0,article.getBoundingClientRect().top-readingTop()-position.offset);
  else window.scrollTo(0,position.scrollY||0);
}
async function sessionRange(project,session,first,serial){
  const data=await api('snapshot',{project,session});
  if(serial!==state.serial)return null;
  while(first&&data.has_older&&data.turns[0]?.turn>first){
    const older=await api('snapshot',{project,session,before:data.turns[0].turn,limit:50});
    if(serial!==state.serial)return null;
    if(older.version!==data.version)throw new Error('故事正在更新，请稍后重试。');
    if(!older.turns.length)break;
    data.turns.unshift(...older.turns);data.has_older=older.has_older;
  }
  return data;
}
async function selectSession(id, navigation={}) {
  if (hasUnsaved() && !window.confirm('有尚未保存的设置或创作要求，放弃这些改动并切换会话？')) return;
  saveDraft(); const project=state.project.id, serial=++state.serial;
  const newKey=sessionKey(project,id);
  if(state.legacyStorage&&!storage.get('migrated:'+newKey,false)){
    const legacy=`${project}:${id}`;
    for(const prefix of ['draft:','override:'])if(storage.get(prefix+newKey,null)===null)storage.set(prefix+newKey,storage.get(prefix+legacy,null));
    storage.set('position:'+newKey,storage.get('position:'+newKey,{scrollY:storage.get('scroll:'+legacy,0)}));
    storage.set('migrated:'+newKey,true);
  }
  const position=navigation.position||storage.get('position:'+sessionKey(project,id),null);
  let data;
  try{data=await sessionRange(project,id,navigation.turn||position?.first||position?.turn,serial);}catch(error){
    if(serial!==state.serial)return;
    const diagnostic=await api('session_diagnostic',{project,session:id});if(serial!==state.serial)return;
    state.session=id;state.data=null;state.patch={};state.reqDirty=false;state.requirementsBase=null;state.requirementsDraft=[];
    state.override=storage.get('override:'+newKey,null);state.preview=null;state.recoveryOptions=storage.get('recovery-options:'+newKey,null);state.ticket=storage.get('ticket:'+newKey,null);$('#user-input').value=storage.get('draft:'+newKey,'');renderTicket();
    $('#welcome').hidden=true;$('#session-view').hidden=false;$('#composer').hidden=true;
    $('#session-title').textContent=id;$('#session-meta').textContent='暂时无法读取会话';$('#story').replaceChildren();$('#pending-notice').hidden=false;$('#pending-notice').textContent=error.message;
    $('#recovery-panel').hidden=false;$('#operation-list').replaceChildren();const button=document.createElement('button');button.textContent='复制诊断指令';button.onclick=task(()=>copyRecovery({}));$('#operation-list').append(button);
    const retry=document.createElement('button');retry.textContent='重新读取';retry.onclick=task(()=>selectSession(id));$('#operation-list').append(retry);
    $('#restart-available').hidden=true;$('#load-older').hidden=true;$('#new-content').hidden=true;
    ['#checkpoint-button','#branch-button','#turn-jump','#jump-button'].forEach(s=>$(s).disabled=true);renderConfig();renderRequirements();renderSessions();return;
  }
  if(!data||serial!==state.serial)return;
  state.session=id;state.data=data;state.patch={};state.override=storage.get('override:'+key(),null);state.preview=null;
  state.reqDirty=false;state.requirementsBase=data.requirements;state.requirementsDraft=structuredClone(data.requirements.items);state.recoveryOptions=storage.get('recovery-options:'+key(),null);renderRequirements();
  state.ticket=storage.get('ticket:'+key(),null);state.ticketVersion=null;
  if(state.override){state.preview=await api('preview',{project,session:id,override:state.override},true);if(serial!==state.serial)return;}
  storage.set(lastSessionKey(project),id);$('#user-input').value=storage.get('draft:'+key(),'');
  $('#welcome').hidden=true;$('#session-view').hidden=false;$('#composer').hidden=false;
  state.returnPosition=null;$('#return-to-reading').hidden=true;
  closeDrawers();renderSessions();renderSession();renderConfig();
  if(navigation.mode!=='none')syncRoute(navigation.mode||'push',navigation.turn);
  await new Promise(resolve=>requestAnimationFrame(resolve));
  if(serial!==state.serial)return;
  if(navigation.turn)await jumpToTurn(navigation.turn,'none');else restorePosition(position||{scrollY:0});
  await refreshTicket(serial);
}

function syncRoute(mode='push',turn=null){
  const params=new URLSearchParams();if(state.project)params.set('project',state.project.id);if(state.session)params.set('session',state.session);if(turn)params.set('turn',turn);
  const url='/'+(params.size?'?'+params:'');
  const method=mode==='replace'||url===location.pathname+location.search?'replaceState':'pushState';
  history[method]({project:state.project?.id,session:state.session},'',url);
}
async function openRoute(position=null){
  const params=new URL(location.href).searchParams,project=params.get('project');
  if(!project)return;
  if(!state.projects.some(p=>p.id===project))throw Error('链接中的作品不存在，请从左侧选择作品。');
  const session=params.get('session'),raw=params.get('turn'),turn=raw===null?null:Number(raw);
  if(raw!==null&&(!session||!Number.isSafeInteger(turn)||turn<1))throw Error('链接中的回合编号无效。');
  const changed=await loadProject(project,false,{mode:'none'});if(!changed){syncRoute('replace');return;}
  if(session){if(!state.project.sessions.some(s=>s.id===session))throw Error('链接中的会话不存在，请从左侧选择会话。');await selectSession(session,{mode:'none',turn,position});}
}
window.addEventListener('popstate',()=>task(async()=>{
  if(!new URL(location.href).searchParams.has('project')){await loadProject(state.project?.id||state.projects[0]?.id,true,{mode:'replace'});return;}
  await openRoute(history.state?.position);
})());
function inlineText(text) {
  const frag=document.createDocumentFragment();
  for(const part of text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g)) {
    if(part.startsWith('**')&&part.endsWith('**')){const el=document.createElement('strong');el.textContent=part.slice(2,-2);frag.append(el);}
    else if(part.startsWith('*')&&part.endsWith('*')){const el=document.createElement('em');el.textContent=part.slice(1,-1);frag.append(el);}
    else frag.append(document.createTextNode(part));
  }return frag;
}
// No raw HTML, remote images, executable URLs or renderer dependencies.
function proseElement(text) {
  const wrapper=document.createElement('div');wrapper.className='prose';
  for(const block of String(text||'').split(/\n\s*\n/)){
    let el=document.createElement('p'), value=block;
    if(/^#{1,4} /.test(block)){el=document.createElement('h3');value=block.replace(/^#{1,4} /,'');}
    else if(/^> /.test(block)){el=document.createElement('blockquote');value=block.replace(/^> /gm,'');}
    el.append(inlineText(value));wrapper.append(el);
  }return wrapper;
}
function turnElement(row) {
  const article=document.createElement('article');article.className='turn';article.dataset.turn=row.turn;
  const user=document.createElement('div');user.className='user-message';user.textContent=row.user;article.append(user);
  const heading=document.createElement('div');heading.className='turn-heading';
  const label=document.createElement('span');label.textContent=`ORDER ${String(row.turn).padStart(3,'0')} / 第 ${row.turn} 轮`;
  const controls=document.createElement('div');controls.className='turn-actions';
  const select=document.createElement('select');select.setAttribute('aria-label',`第 ${row.turn} 轮回复版本`);
  Object.keys(row.variants).forEach(v=>select.append(option(v,`${v}${v===row.selected?' · 当前':''}`)));select.value=row.selected;
  const copy=document.createElement('button');copy.textContent='复制';copy.onclick=task(()=>copyText(row.variants[select.value].prose));
  const link=document.createElement('button');link.textContent='定位链接';link.onclick=task(()=>copyText(location.origin+'/?'+new URLSearchParams({project:state.project.id,session:state.session,turn:row.turn})));
  const choose=document.createElement('button');choose.textContent='采用此版';choose.hidden=true;
  choose.onclick=task(async()=>{await api('select',{project:state.project.id,session:state.session,turn:row.turn,variant:select.value,expected_version:state.data.version},true);await refreshSession();toast('已采用这个版本，场景与状态已同步。');});
  controls.append(select,copy,link,choose);heading.append(label,controls);article.append(heading);
  const content=document.createElement('div');article.append(content);
  function show(){content.replaceChildren();const v=row.variants[select.value];const preview=select.value!==row.selected;
    choose.hidden=!preview||row.turn!==state.data.current_turn;choose.disabled=state.data.pending;
    if(preview){const note=document.createElement('p');note.className='variant-notice';note.textContent=row.turn===state.data.current_turn?'正在预览候选，尚未改变剧情。':'历史版本仅预览；要采用，请在这一轮建立检查点并进入分支。';content.append(note);}
    content.append(proseElement(v.prose));
    if(v.status){const details=document.createElement('details');details.className='status-block';const summary=document.createElement('summary');summary.textContent='本轮状态';const pre=document.createElement('pre');pre.textContent=v.status;details.append(summary,pre);content.append(details);}
  }
  select.onchange=show;show();return article;
}
function renderSession() {
  const d=state.data;if(!d)return;
  $('#project-label').textContent=state.project.id+' / NOW SERVING';$('#session-title').textContent=state.session;
  $('#session-meta').textContent=`第 ${d.current_turn} 轮 · ${d.profile_id || '自由角色'} · ${d.pending?'有待完成回合':'已保存的故事'}`;
  $('#pending-notice').hidden=!d.pending;
  $('#pending-notice').textContent=d.can_configure?'本轮上下文尚未就绪。可以调整模式或设置，再让 Agent 使用原请求继续准备。':'本桌有待完成回合。请在 Agent 提交，或明确归档原操作后再调整。';
  $('#load-older').hidden=!d.has_older;$('#new-content').hidden=true;
  $('#story').replaceChildren(...d.turns.map(turnElement));
  if(!d.has_older&&d.opening?.text){const block=document.createElement('section');block.className='turn';const title=document.createElement('h3');title.textContent='开场 · '+(d.opening.title||'');block.append(title,proseElement(d.opening.text));$('#story').prepend(block);}
  if(!d.turns.length&&!d.opening?.text)$('#story').innerHTML='<div class="empty-story"><svg aria-hidden="true"><use href="/diner.svg#burger"/></svg><h2>这张桌子，还等着第一段故事。</h2><p>在下面写下开场，把小票带给你的 Agent。</p></div>';
  ['#continue-button','#regen-button','#checkpoint-button','#branch-button'].forEach(s=>$(s).disabled=d.pending);
  $('#regen-button').disabled=d.pending||!d.current_turn;
  $('#turn-jump').max=d.current_turn;$('#turn-jump').disabled=!d.current_turn;$('#jump-button').disabled=!d.current_turn;
  $('#turn-count').textContent=`/ ${d.current_turn} 轮`;
  $('#turn-jump-form').hidden=!d.current_turn;
  updateOverride();
  renderTicket();
  renderRecovery();
}
async function refreshSession() {
  if(!state.session)return;
  const session=state.session,project=state.project.id,serial=state.serial,refresh=++state.refreshSerial;
  const data=await sessionRange(project,session,state.data?.turns[0]?.turn,serial);
  if(!data||serial!==state.serial||session!==state.session||refresh!==state.refreshSerial)return;
  const position=readingPosition();state.data=data;renderSession();restorePosition(position);if(!dirty())renderConfig();
  acceptRequirements(data.requirements);
  await refreshTicket(serial);
}
function currentConfig(){return $('#scope').value==='project'?state.project?.config:$('#scope').value==='turn'&&state.preview?state.preview:state.data?.config;}
function renderConfig(){
  renderConfigSummary();
  const cfg=currentConfig(), turn=$('#scope').value==='turn', busy=state.data?.pending && !state.data?.can_configure && !($('#scope').value==='project');
  $('#config-fields').disabled=!cfg||!!busy;
  if(cfg){
    $$('[data-key]').forEach(el=>{let value=pathGet(cfg.effective,el.dataset.key);if(el.dataset.key==='prose.enabled'&&value==null)value=cfg.effective.prose?.min_chars!=null||cfg.effective.prose?.max_chars!=null;if(el.dataset.key in state.patch)value=state.patch[el.dataset.key];
      if(el.type==='checkbox')el.checked=value===true;else if(el.dataset.key==='status_bar.fields')el.value=(value||[]).join('，');else {const val=value??'';if(el.tagName==='SELECT'&&val&&!Array.from(el.options).some(o=>o.value===String(val)))el.append(option(val,String(val)));el.value=val;}
    });
    const languages=state.patch['bilingual.languages']??cfg.effective.bilingual?.languages??{};$('#languages').value=Object.entries(languages).map(([k,v])=>`${k}=${v}`).join('\n');
    const sources={session:'本会话',session_defaults:'新会话默认',builtin:'内置默认','short-rp':'短 RP 预设','short-rp preset':'短 RP 预设','short-rp override':'短 RP 微调','short-rp saved override':'短 RP 微调',override:'本轮覆盖',temporary:'本轮覆盖',saved:'已保存'};
    $$('[data-source]').forEach(el=>{const source=cfg.sources[el.dataset.source];el.textContent='来源：'+(sources[source]||source||'默认');});
  }
  $('#save-config').textContent=turn?'附在下一张小票上':'保存这份菜单';
  $('#config-hint').textContent=turn?'仅随下一张交接小票生效，不修改持续设置。':'只保存你改动的选项。';
  $('#undo-config').disabled=turn;$('#reset-config').disabled=turn;
  const target=$('#scope').value==='project'?state.project:state.data;
  const select=$('#style-select');select.replaceChildren();
  if(state.project){state.project.styles.presets.forEach(p=>select.append(option(p.name,p.name)));if($('#scope').value!=='project')select.prepend(option('inherit','跟随当前项目文风'));select.value=target?.style?.value||'';}
  $('#mode-select').value=target?.mode||'quality';
  ['#save-style','#save-mode','#style-select','#mode-select'].forEach(s=>$(s).disabled=turn||!target||!!busy);
}
function renderConfigSummary(){
  const effective=(state.preview||state.data?.config)?.effective,box=$('#effective-settings');box.replaceChildren();
  if(!effective){$('#effective-scope').textContent='当前设置';box.textContent='选择会话后，在这里查看当前生效的设置。';return;}
  const items=[['字数',effective.prose?.enabled===false?'不设目标':`${effective.prose?.min_chars??'不限'}—${effective.prose?.max_chars??'不限'} 字`],['人称',({first:'第一人称',second:'第二人称',third:'第三人称'})[effective.person]||'沿用'],['扮演',({'co-narrative':'共同叙事','user-driven':'用户主导','short-rp':'不新增用户言行'})[effective.agency]||'沿用'],['状态栏',effective.status_bar?.enabled?'显示':'隐藏']];
  for(const [label,value] of items){const row=document.createElement('div'),name=document.createElement('dt'),text=document.createElement('dd');name.textContent=label;text.textContent=value;row.append(name,text);box.append(row);}
  $('#effective-scope').textContent=state.override?'已附本轮覆盖 · 下一张小票生效':'本会话当前生效';
}
function updateOverride(){const note=$('#override-note');note.hidden=!state.override;note.textContent='本轮菜单已附上 · 成功保存交接小票后，下一张恢复持续设置。';}
function renderTicket(){
  agentPanel.changed();
  const labels={waiting:'等待 Agent 接手',ready:'上下文已就绪，等待提交',needs_compaction:'需要先整理记忆',budget_blocked:'上下文超出预算',stale:'上下文已变化，请在 Agent 恢复',committed:'已提交',archived:'已归档',expired:'已过期，请重新生成小票'};
  $('#request-note').hidden=!state.ticket;$('#last-ticket').hidden=!state.ticket;
  $('#request-note').textContent=state.ticket?'上张小票 · '+(labels[state.ticket.status]||'已保存')+(state.ticket.turn?` · 第 ${state.ticket.turn} 轮`:''):'';
}
async function refreshTicket(serial=state.serial,version=null){
  const ticket=state.ticket;if(!ticket)return;
  if(version&&state.ticketVersion===version)return;
  const result=await api('request_status',{project:state.project.id,session:state.session,request_id:ticket.id});
  if(serial!==state.serial||state.ticket?.id!==ticket.id)return;
  state.ticketVersion=version;
  state.ticket={...ticket,...result};
  if(result.status==='committed'&&ticket.status!=='committed'&&$('#user-input').value===ticket.user_text){
    $('#user-input').value='';$('#draft-hint').textContent='上一张小票已提交，可以开始下一轮。';
  }
  saveDraft();renderTicket();
}
function configureParams(extra){const projectScope=$('#scope').value==='project';return {project:state.project.id,session:projectScope?null:state.session,expected_version:projectScope?state.project.version:state.data.version,...extra};}
async function saveConfig(extra){await api('configure',configureParams(extra),true);state.patch={};const id=state.project.id;state.project=await api('project',{project:id});if(state.session)await refreshSession();else renderConfig();toast('菜单已保存。');}
async function copyText(text){await navigator.clipboard.writeText(text);toast('已复制。');}
let shownTicket=null,showFullTicket=false;
function showTicket(ticket){shownTicket=ticket;showFullTicket=false;$('#ticket-text').value=ticket.instruction;$('#ticket-format').textContent='完整指令';$('#ticket-format').disabled=!ticket.full_instruction;$('#ticket-dialog').showModal();}
async function handoff(kind){requireSession();agentPanel.check();if(hasUnsaved())throw new Error('先保存正在编辑的菜单和创作要求，再复制指令。');
  const serial=state.serial,oldKey=key(),user_text=$('#user-input').value;
  const recovered=state.recoveryOptions||{};
  if(recovered.kind&&recovered.kind!==kind)throw new Error(recovered.kind==='regenerate'?'找回的是重生成要求，请使用重生成按钮；要另写输入，可先退出原任务恢复。':'找回的是续写输入，请使用续玩按钮；要另写输入，可先退出原任务恢复。');
  const result=await api('request',{project:state.project.id,session:state.session,expected_version:state.data.version,user_text,kind,override:state.override,style_override:recovered.style_override,mode:recovered.mode,query:recovered.query||''},true);
  const ticket={...result,user_text,status:'waiting'};storage.set('ticket:'+oldKey,ticket);storage.set('override:'+oldKey,null);
  if(serial!==state.serial)return;
  state.ticket=ticket;state.ticketVersion=null;state.override=null;state.preview=null;state.recoveryOptions=null;updateOverride();saveDraft();renderConfig();renderTicket();
  if(await agentPanel.send(result))return;
  showTicket(result);
  try{await navigator.clipboard.writeText(result.instruction);toast('小票已复制，粘贴到你选择的 Agent 即可。');}catch{toast('小票已保存，请点击复制，或选中指令手动复制。');}
}

$('#project-select').onchange=task(e=>loadProject(e.target.value));$('#session-search').oninput=renderSessions;
$('#back-to-top').onclick=()=>window.scrollTo({top:0,behavior:'instant'});
$('#write-reply').onclick=()=>{if(!state.returnPosition)state.returnPosition=readingPosition();$('#return-to-reading').hidden=false;$('#composer').scrollIntoView({block:'start',behavior:'instant'});$('#user-input').focus({preventScroll:true});};
$('#return-to-reading').onclick=()=>{restorePosition(state.returnPosition);state.returnPosition=null;$('#return-to-reading').hidden=true;$('#write-reply').focus({preventScroll:true});};
$('#copy-session-link').onclick=task(()=>copyText(location.origin+'/?'+new URLSearchParams({project:state.project.id,session:state.session})));
$('#turn-jump-form').onsubmit=task(async e=>{e.preventDefault();await jumpToTurn(Number($('#turn-jump').value));});
async function jumpToTurn(target,mode='push'){
  requireSession();const data=state.data,serial=state.serial;
  if(!Number.isInteger(target)||target<1||target>data.current_turn)throw new Error('请输入已有的回合编号。');
  const project=state.project.id,session=state.session,rows=[...data.turns];let hasOlder=data.has_older;
  $('#jump-button').disabled=true;$('#jump-button').textContent='定位中…';
  try{
    while(!rows.some(row=>row.turn===target)&&hasOlder){
      const oldest=rows[0]?.turn;if(!oldest)break;
      const older=await api('snapshot',{project,session,before:oldest,limit:50});
      if(serial!==state.serial||state.data!==data)return;
      if(older.version!==data.version)throw new Error('故事已有更新，请更新内容后再跳转。');
      if(!older.turns.length)break;
      rows.unshift(...older.turns);hasOlder=older.has_older;
    }
    if(serial!==state.serial||state.data!==data)return;
    if(!rows.some(row=>row.turn===target))throw new Error('没有找到这一轮。');
    if(rows.length!==data.turns.length){data.turns=rows;data.has_older=hasOlder;renderSession();}
    saveDraft();if(mode!=='none')syncRoute(mode,target);
    const article=$(`[data-turn="${target}"]`);article.tabIndex=-1;
    article.focus({preventScroll:true});article.scrollIntoView({block:'start',behavior:'instant'});saveDraft();
  }finally{$('#jump-button').disabled=!state.data?.current_turn;$('#jump-button').textContent='跳转';}
}
$('#new-content').onclick=task(async()=>{if(dirty()){if(!window.confirm('更新后需要重新核对设置，放弃未保存的菜单改动？'))return;state.patch={};}await refreshSession();});
$('#load-older').onclick=task(async()=>{const project=state.project.id,session=state.session,serial=state.serial;const oldest=state.data.turns[0]?.turn;if(!oldest)return;const older=await api('snapshot',{project,session,before:oldest});if(serial!==state.serial)return;if(older.version!==state.data.version){await refreshSession();return;}
  const anchor=$(`[data-turn="${oldest}"]`), beforeTop=anchor?.getBoundingClientRect().top||0;state.data.turns=[...older.turns,...state.data.turns];state.data.has_older=older.has_older;renderSession();const afterTop=$(`[data-turn="${oldest}"]`)?.getBoundingClientRect().top||0;window.scrollBy(0,afterTop-beforeTop);
});
$$('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());
$('#reading-button').onclick=()=>$('#reading-dialog').showModal();$('#connect-button').onclick=()=>$('#connect-dialog').showModal();
$('#focus-button').onclick=()=>{const active=document.body.classList.toggle('focus');$('#focus-button').setAttribute('aria-pressed',String(active));$('#focus-button').textContent=active?'退出专注':'专注阅读';};
let drawerTrigger=null;
function closeDrawers(restore=false){
  ['#sidebar','#settings'].forEach(s=>$(s).classList.remove('open'));
  ['#sessions-button','#settings-button'].forEach(s=>$(s).setAttribute('aria-expanded','false'));
  if(restore&&drawerTrigger)drawerTrigger.focus();drawerTrigger=null;
}
function toggleDrawer(panel,button){
  const open=$(panel).classList.contains('open');closeDrawers();
  if(open){$(button).focus();return;}
  $(panel).classList.add('open');$(button).setAttribute('aria-expanded','true');drawerTrigger=$(button);
  $(panel).querySelector('input:not(:disabled),select:not(:disabled),button:not(:disabled)')?.focus();
}
$('#sessions-button').onclick=()=>toggleDrawer('#sidebar','#sessions-button');
$('#settings-button').onclick=()=>toggleDrawer('#settings','#settings-button');
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawers(true);});
$('#user-input').oninput=saveDraft;
window.addEventListener('beforeunload',saveDraft);
$('#scope').onchange=()=>{if(dirty()&&!window.confirm('有尚未保存的改动，放弃后切换生效范围？')){$('#scope').value=state.scope;return;}state.scope=$('#scope').value;state.patch={};renderConfig();};
$$('[data-key]').forEach(el=>{const record=()=>{
  let value=el.type==='checkbox'?el.checked:el.type==='number'?(el.value?Number(el.value):null):el.dataset.key==='status_bar.fields'?el.value.split(/[,，]/).map(v=>v.trim()).filter(Boolean):el.value;
  state.patch[el.dataset.key]=value;$('#config-hint').textContent='有未保存的改动。';
};el.addEventListener('input',record);el.addEventListener('change',record);});
$('#languages').onchange=()=>{const languages={};for(const line of $('#languages').value.split('\n').filter(s=>s.trim())){const index=line.indexOf('=');if(index<1||!line.slice(index+1).trim()){toast('语言格式：角色=语言，每行一个。');return;}languages[line.slice(0,index).trim()]=line.slice(index+1).trim();}state.patch['bilingual.languages']=languages;$('#config-hint').textContent='有未保存的改动。';};
$('#config-form').onsubmit=task(async e=>{e.preventDefault();if(!dirty()){toast('菜单没有改动。');return;}
  if($('#scope').value==='turn'){const next=structuredClone(state.override||{});for(const [k,v] of Object.entries(state.patch))pathSet(next,k,v);state.preview=await api('preview',{project:state.project.id,session:state.session,override:next},true);state.override=next;state.patch={};updateOverride();saveDraft();renderConfig();toast('本轮菜单已附上，下次复制指令时一同交接。');}
  else await saveConfig({pairs:Object.entries(state.patch).map(([k,v])=>`${k}=${JSON.stringify(v)}`)});
});
$('#reset-config').onclick=task(()=>saveConfig({reset:$('#reset-module').value}));$('#undo-config').onclick=task(()=>saveConfig({undo:true}));
$('#save-style').onclick=task(()=>saveConfig({style:$('#style-select').value}));$('#save-mode').onclick=task(()=>saveConfig({mode:$('#mode-select').value}));
$('#continue-button').onclick=task(()=>handoff('continue'));$('#regen-button').onclick=task(()=>handoff('regenerate'));
$('#copy-ticket').onclick=task(()=>copyText($('#ticket-text').value));
$('#last-ticket').onclick=()=>{if(state.ticket)showTicket(state.ticket);};
$('#ticket-format').onclick=()=>{showFullTicket=!showFullTicket;$('#ticket-text').value=showFullTicket?shownTicket.full_instruction:shownTicket.instruction;$('#ticket-format').textContent=showFullTicket?'短指令':'完整指令';};
$('#platform').onchange=()=>$('#connection-config').textContent=state.connections[$('#platform').value]||'';
$('#copy-connection').onclick=task(()=>copyText($('#connection-config').textContent));
$('#new-session').onclick=task(async()=>{if(!state.project)throw new Error('请先选择作品。');state.project=await api('project',{project:state.project.id});$('#create-form').reset();$('#create-profile').replaceChildren(option('','不绑定 Profile'));
  state.project.profiles.filter(p=>p.status==='ready').forEach(p=>$('#create-profile').append(option(p.profile_id,p.display_name||p.profile_id)));$('#create-opening').replaceChildren(option('','空白开始'));$('#create-dialog').showModal();
});
$('#create-profile').onchange=task(async()=>{const profile=$('#create-profile').value;$('#create-opening').replaceChildren(option('','空白开始'));if(!profile)return;const data=await api('openings',{project:state.project.id,profile});if(profile!==$('#create-profile').value)return;data.candidates.forEach(c=>$('#create-opening').append(option(c.id,c.title||c.id)));});
$('#create-form').onsubmit=task(async e=>{e.preventDefault();const result=await api('new_session',{project:state.project.id,session_id:$('#create-name').value.trim(),expected_version:state.project.version,profile:$('#create-profile').value||null,opening:$('#create-opening').value||null},true);$('#create-dialog').close();state.project=await api('project',{project:state.project.id});await selectSession(result.session);toast('新卡座准备好了。');});
$('#checkpoint-button').onclick=task(()=>{requireSession();$('#bookmark-name').value=`书签-${state.data.current_turn}-${new Date().toISOString().slice(11,19).replaceAll(':','')}`;$('#bookmark-turn').value=state.data.current_turn;$('#bookmark-turn').max=state.data.current_turn;$('#bookmark-dialog').showModal();});
$('#bookmark-form').onsubmit=task(async e=>{e.preventDefault();await api('checkpoint',{project:state.project.id,session:state.session,checkpoint_id:$('#bookmark-name').value.trim(),expected_version:state.data.version,through_turn:Number($('#bookmark-turn').value)},true);$('#bookmark-dialog').close();await refreshSession();toast('书签已保存。');});
$('#branch-button').onclick=task(()=>{requireSession();const checkpoints=Object.entries(state.data.checkpoints);if(!checkpoints.length)throw new Error('先用“留个书签”创建一个检查点。');$('#branch-checkpoint').replaceChildren(...checkpoints.map(([id,c])=>option(id,`${id} · 第 ${c.through_turn} 轮`)));$('#branch-name').value='';$('#branch-dialog').showModal();});
$('#branch-form').onsubmit=task(async e=>{e.preventDefault();const result=await api('branch',{project:state.project.id,session:state.session,checkpoint_id:$('#branch-checkpoint').value,new_session:$('#branch-name').value.trim(),expected_version:state.data.version},true);$('#branch-dialog').close();state.project=await api('project',{project:state.project.id});await selectSession(result.session);toast('故事分支已创建。');});
$('#stop-button').onclick=task(async()=>{if(!window.confirm('关闭本地后台？将停止本工作台启动的生成任务，已保存的故事仍保留。'))return;await api('shutdown',{},true);$('#connection').textContent='CLOSED';clearInterval(pollTimer);toast('本地后台已关闭，可以关闭网页。');});

const prefs=storage.get('reading',{theme:'light',font:'serif','font-size':18,'line-height':1.85,'read-width':760});
prefs.theme=storage.get('theme',prefs.theme||storage.get('bible-theme','light'));
storage.set('theme',prefs.theme);
function applyPrefs(){document.documentElement.dataset.theme=prefs.theme;const style=document.documentElement.style;style.setProperty('--reading-size',prefs['font-size']+'px');style.setProperty('--reading-line',prefs['line-height']);style.setProperty('--reading-width',prefs['read-width']+'px');style.setProperty('--reading-font',prefs.font==='sans'?'"Segoe UI","Microsoft YaHei",sans-serif':'Georgia,"Songti SC","SimSun",serif');}
for(const id of ['theme','font','font-size','line-height','read-width']){$('#'+id).value=prefs[id];$('#'+id).oninput=e=>{prefs[id]=e.target.value;applyPrefs();storage.set('reading',prefs);if(id==='theme')storage.set('theme',prefs.theme);};}applyPrefs();
window.addEventListener('storage',event=>{if(event.key==='diner:theme'){prefs.theme=storage.get('theme','light');$('#theme').value=prefs.theme;applyPrefs();}});
let polling=false;
const pollTimer=setInterval(async()=>{if(document.hidden||!state.session||!state.data||polling||writing)return;polling=true;const serial=state.serial;
  try{const status=await api('status',{project:state.project.id,session:state.session});if(serial!==state.serial)return;$('#connection').textContent='OPEN · 本地';
    if(status.catalog_version!==state.project.catalog_version){
      const project=await api('project',{project:state.project.id});if(serial!==state.serial)return;
      state.project=project;renderSessions();
    }
    if(status.pending!==state.data.pending||status.can_configure!==state.data.can_configure){state.data.pending=status.pending;state.data.can_configure=status.can_configure;$('#pending-notice').hidden=!status.pending;$('#pending-notice').textContent=status.can_configure?'本轮上下文尚未就绪。可调整模式或设置，再让 Agent 使用原请求继续准备。':'本桌有待完成回合。请在 Agent 提交或明确归档原操作。';if(!dirty())renderConfig();['#continue-button','#checkpoint-button','#branch-button'].forEach(s=>$(s).disabled=status.pending);$('#regen-button').disabled=status.pending||!state.data.current_turn;}
    if(!status.locked&&status.requirements_version!==state.requirementsBase?.version){
      const req=await api('requirements_view',{project:state.project.id,session:state.session});if(serial!==state.serial)return;acceptRequirements(req);
    }
    if(status.version!==state.data.version&&!status.locked)await refreshTicket(serial,status.version);
    if(serial!==state.serial)return;
    if(status.version!==state.data.version&&!status.locked){if(status.pending||state.data.operations?.length||(!dirty()&&window.innerHeight+window.scrollY>=document.documentElement.scrollHeight-180))await refreshSession();else $('#new-content').hidden=false;}
  }catch{$('#connection').textContent='暂时断连';}finally{polling=false;}
},2000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&state.session&&state.data&&!dirty())task(async()=>{
  const serial=state.serial,status=await api('status',{project:state.project.id,session:state.session});
  if(serial===state.serial&&status.version!==state.data.version&&!status.locked)await refreshSession();
})();});
async function boot(){
  const bootstrap=await api('bootstrap');state.token=bootstrap.token;state.workspaceId=bootstrap.workspace_id;state.legacyStorage=bootstrap.legacy_workspace;state.connections=bootstrap.connections;$('#platform').onchange();
  state.projects=await api('projects');$('#project-select').replaceChildren(...state.projects.map(p=>option(p.id,p.name)));
  $('#connection').textContent='OPEN · 本地';
  if(!state.projects.length){$('#session-list').innerHTML='<p class="muted small">还没有作品。先在 Agent 中创建 Story Bible 项目。</p>';$('#new-session').disabled=true;return;}
  history.scrollRestoration='manual';
  if(new URL(location.href).searchParams.has('project')){try{await openRoute(history.state?.position);}catch(error){toast(error.message);}return;}
  const previous=storage.get('project:'+state.workspaceId,state.legacyStorage?storage.get('project',null):null);await loadProject(state.projects.some(p=>p.id===previous)?previous:state.projects[0].id,true,{mode:'replace'});
}
function requirementsHint(){
  const req=state.requirementsBase;
  $('#requirements-hint').textContent=state.reqDirty?'有未保存的创作要求。':!req?'选一个会话，保存它的写作偏好。':req.after_current?'已保存；正在生成的回复沿用原要求，之后生效。':'已保存；下次准备回复自动生效。';
  $('#save-requirements').disabled=!state.reqDirty;
  $('#undo-requirements').disabled=!req?.can_undo||!!state.reqDirty;
}
function renderRequirements(){
  $('#requirements-fields').disabled=!state.data;
  const items=state.requirementsDraft||[];
  $('#requirements-count').textContent=state.requirementsBase?`${state.requirementsBase.items.length} 条已保存`:'';
  for(const kind of ['banned','guidance']){
    const list=$(`#${kind}-list`);list.replaceChildren();
    for(const item of items.filter(i=>i.type===kind)){
      const row=document.createElement('div');row.className='requirement-row';
      const input=document.createElement('textarea');input.rows=kind==='banned'?1:3;input.value=item.text;input.setAttribute('aria-label',kind==='banned'?'禁词或短语':'自然语言创作要求');
      input.placeholder=kind==='banned'?'例如：不容置疑':'例如：少用情绪总结，保留人物的克制。';
      input.oninput=()=>{item.text=input.value;state.reqDirty=true;requirementsHint();};
      const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','删除这条要求');
      remove.onclick=()=>{state.requirementsDraft=state.requirementsDraft.filter(i=>i.id!==item.id);state.reqDirty=true;renderRequirements();};
      row.append(input,remove);list.append(row);
    }
    if(!list.children.length){const empty=document.createElement('p');empty.className='small muted';empty.textContent=kind==='banned'?'还没有禁词。':'写下一次，后续持续记住。';list.append(empty);}
  }
  requirementsHint();
}
function acceptRequirements(req){
  if(!state.reqDirty){state.requirementsBase=req;state.requirementsDraft=structuredClone(req.items);$('#reload-requirements').hidden=true;renderRequirements();}
  else if(req.version!==state.requirementsBase?.version){$('#requirements-hint').textContent='另一端已修改要求；你的草稿已保留，请合并最新版本。';$('#reload-requirements').hidden=false;}
}
for(const kind of ['banned','guidance'])$(`#add-${kind}`).onclick=()=>{
  state.requirementsDraft.push({id:crypto.randomUUID(),type:kind,text:''});state.reqDirty=true;renderRequirements();$(`#${kind}-list`).lastElementChild.querySelector('textarea').focus();
};
$('#save-requirements').onclick=task(async()=>{
  requireSession();const serial=state.serial,sent=structuredClone(state.requirementsDraft);
  try{const req=await api('requirements',{project:state.project.id,session:state.session,action:'save',expected_version:state.requirementsBase.version,items:sent},true);
    if(serial!==state.serial)return;state.reqDirty=JSON.stringify(sent)!==JSON.stringify(state.requirementsDraft);state.data.requirements=req;state.requirementsBase=req;
    if(state.reqDirty)renderRequirements();else acceptRequirements(req);
    toast(state.reqDirty?'已保存提交的要求；保存期间的新编辑仍未保存。':req.after_current?'要求已保存，正在生成的回复完成后生效。':'要求已保存，下次回复生效。');
  }catch(error){if(serial===state.serial)$('#reload-requirements').hidden=false;throw error;}
});
$('#undo-requirements').onclick=task(async()=>{
  const serial=state.serial,req=await api('requirements',{project:state.project.id,session:state.session,action:'undo',expected_version:state.requirementsBase.version},true);
  if(serial!==state.serial)return;state.data.requirements=req;acceptRequirements(req);toast('已撤销上次要求修改，下次准备回复生效。');
});
$('#reload-requirements').onclick=task(async()=>{
  const serial=state.serial,latest=await api('requirements_view',{project:state.project.id,session:state.session});if(serial!==state.serial)return;
  state.mergeLatest=latest;
  const describe=items=>items.map(i=>`${i.type==='banned'?'禁词':'要求'}：${i.text}`).join('\n\n')||'（空）';
  $('#requirements-mine').textContent=describe(state.requirementsDraft);$('#requirements-latest').textContent=describe(latest.items);
  $('#merge-banned').value=state.requirementsDraft.filter(i=>i.type==='banned').map(i=>i.text).join('\n');
  $('#merge-guidance').value=state.requirementsDraft.filter(i=>i.type==='guidance').map(i=>i.text).join('\n\n');$('#requirements-merge').showModal();
});
$('#accept-merge').onclick=()=>{
  const original=[...state.requirementsDraft,...state.mergeLatest.items],used=new Set();
  state.requirementsDraft=['banned','guidance'].flatMap(type=>$(`#merge-${type}`).value.split(type==='banned'?/\n/:/\n\s*\n/).map(text=>text.trim()).filter(Boolean).map(text=>{
    const old=original.find(i=>i.type===type&&i.text===text&&!used.has(i.id)),id=old?.id||crypto.randomUUID();used.add(id);return {id,type,text};
  }));
  state.requirementsBase=state.mergeLatest;state.reqDirty=true;$('#reload-requirements').hidden=true;$('#requirements-merge').close();renderRequirements();
};
function renderRecovery(){
  const rows=state.data?.operations||[];$('#recovery-panel').hidden=!rows.length;$('#operation-list').replaceChildren();
  const labels={ready:'上下文已就绪，等待提交',needs_compaction:'需要整理记忆',budget_blocked:'上下文超出预算',stale:'原上下文已过期',committed:'已提交，待完成收尾',damaged:'本地事务需要诊断'};
  for(const op of rows){
    const block=document.createElement('div'),title=document.createElement('strong');block.dataset.operationId=op.operation_id||'diagnostic';title.textContent=`${op.kind==='regenerate'?'重生成':'续写'} · ${labels[op.status]||op.status}`;
    const note=document.createElement('p');note.className='small muted';note.textContent=op.error||(op.has_draft?'有本地草稿，可交接复核。':'尚无已保存草稿。');
    const actions=document.createElement('div');actions.className='recovery-actions';
    const recover=document.createElement('button');recover.type='button';recover.textContent=op.status==='damaged'?'复制诊断指令':'复制恢复指令';recover.onclick=task(()=>copyRecovery(op));actions.append(recover);
    if(op.operation_id){const restart=document.createElement('button');restart.type='button';restart.textContent=op.status==='committed'?'完成收尾':'保留草稿并重新开始';restart.onclick=task(()=>editOperation(op));actions.append(restart);}
    block.append(title,note,actions);$('#operation-list').append(block);
  }
  $('#restart-available').hidden=!state.data?.last_restart;
  $('#restore-composer-backup').hidden=!storage.get('composer-backup:'+key(),null);
  $('#leave-recovery').hidden=!state.recoveryOptions;
  if(state.recoveryOptions)$('#draft-hint').textContent=state.recoveryOptions.kind==='regenerate'?'已找回重生成要求 · 使用重生成按钮':'已找回续写输入 · 使用续玩按钮';
}
async function copyRecovery(op){
  const serial=state.serial,result=await api('recovery_ticket',{project:state.project.id,session:state.session,operation_id:op.operation_id||null},true);
  if(serial!==state.serial)return;showTicket(result);
  try{await navigator.clipboard.writeText(result.instruction);toast('恢复指令已复制。');}catch{toast('恢复指令已生成，请手动复制。');}
}
async function restoreRecovery(recovery){
  const current=$('#user-input').value;
  if(current.trim()&&(current!==recovery.user_text||JSON.stringify(state.override)!==JSON.stringify(recovery.override))){
    storage.set('composer-backup:'+key(),{text:current,override:state.override,recoveryOptions:state.recoveryOptions});
    if(!window.confirm('输入框已有不同草稿，已备份在本浏览器。载入原任务输入？取消可保留当前草稿。'))return;
  }
  state.override=recovery.override||null;state.preview=null;state.recoveryOptions=recovery;$('#user-input').value=recovery.user_text||'';
  if(state.override)state.preview=await api('preview',{project:state.project.id,session:state.session,override:state.override},true);
  saveDraft();updateOverride();renderConfig();renderRecovery();$('#user-input').focus();$('#composer').scrollIntoView({block:'center'});
}
async function editOperation(op){
  const serial=state.serial,result=await api('operation_edit',{project:state.project.id,session:state.session,operation_id:op.operation_id,action:op.status==='committed'?'finish':'archive',expected_version:state.data.version,reason:'用户在工作台选择保留草稿并重新开始'},true);
  if(serial!==state.serial)return;await refreshSession();
  if(result.status==='archived'){await restoreRecovery(result.recovery);toast('原任务已归档。可以修改要求，再复制新的指令。');}
  else toast(result.needs_finish?'原任务已提交，请点击完成收尾。':'收尾完成，正文没有重复追加。');
}
$('#restore-restart').onclick=task(()=>restoreRecovery(state.data.last_restart));
$('#leave-recovery').onclick=()=>{state.recoveryOptions=null;saveDraft();renderRecovery();$('#draft-hint').textContent='已退出恢复，输入和本轮设置仍保留，可自行修改。';};
$('#restore-composer-backup').onclick=task(async()=>{
  const backup=storage.get('composer-backup:'+key(),null);if(!backup)return;
  await restoreRecovery({...(backup.recoveryOptions||{kind:'continue'}),user_text:backup.text,override:backup.override});
});
window.addEventListener('beforeunload',event=>{if(hasUnsaved()||unsavedStorage.size){event.preventDefault();event.returnValue='';}});
const agentPanel=new AgentPanel(api,()=>[state.workspaceId,'session',state.project?.id,state.session],()=>refreshSession());
task(async()=>{await boot();await agentPanel.init();})();
