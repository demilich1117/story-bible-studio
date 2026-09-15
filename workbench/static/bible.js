import {AgentPanel} from './agent-panel.js';
const $ = selector => document.querySelector(selector);
const all = selector => [...document.querySelectorAll(selector)];
const labels = {open:'讨论中', proposal:'候选', complete:'已采用', parked:'已搁置', superseded:'已被修订', ready:'待提交', pending:'提交待恢复', waiting:'待接手', committed:'已完成', stale:'已过期', archived:'已保留归档', budget_blocked:'材料超限'};
const state = {workspace:'', project:'', graph:null, detail:null, selected:null, event:null, filter:'', scale:.85, collapsed:new Set(), outline:matchMedia('(max-width:640px)').matches, serial:0, detailSerial:0, ticket:null, busy:false};
const unsavedStorage=new Map();
const cache = {get(key, fallback=null){if(unsavedStorage.has(key))return unsavedStorage.get(key);try{return JSON.parse(localStorage.getItem('diner:'+key))??fallback;}catch{return fallback;}},set(key,value){try{localStorage.setItem('diner:'+key,JSON.stringify(value));unsavedStorage.delete(key);return true;}catch{unsavedStorage.set(key,value);$('#storage-warning').hidden=false;return false;}}};
const projectKey = () => JSON.stringify([state.workspace,state.project]);
const draftKey = () => 'bible-draft:'+JSON.stringify([state.workspace,state.project,state.selected]);
function message(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(message.timer);message.timer=setTimeout(()=>$('#toast').hidden=true,6000);}
const task = fn => async event => {try{await fn(event);}catch(error){message(error.message);}};
function element(tag, cls, text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!=null)el.textContent=text;return el;}
async function api(op,params={},write=false){
  if(write&&state.busy)throw Error('正在保存，请稍后再操作。');
  if(write)state.busy=true;
  try{const response=await fetch('/api/'+op+(write?'':'?'+new URLSearchParams(Object.entries(params).filter(([,v])=>v!=null))),write?{method:'POST',headers:{'Content-Type':'application/json','X-Studio-Token':state.token},body:JSON.stringify(params)}:{});const data=await response.json();if(!response.ok)throw Error(data.error||'读取失败');return data;}finally{if(write)state.busy=false;}
}
function saveDraft(){if(state.selected){const saved=cache.set(draftKey(),$('#idea-input').value);$('#idea-draft-hint').textContent=saved?'输入已保存在此浏览器':'输入尚未保存，请复制备份';}}
function saveView(){if(!state.project)return;const view={selected:state.selected,filter:state.filter,scale:state.scale,collapsed:[...state.collapsed],outline:state.outline,past:$('#show-past').checked,x:$('#map-viewport').scrollLeft,y:$('#map-viewport').scrollTop,detailY:$('#bible-detail').scrollTop};cache.set('bible-view:'+projectKey(),view);const params=new URL(location.href).searchParams;if(params.get('project')===state.project&&params.get('topic')===state.selected&&params.get('event')===state.event)history.replaceState({...history.state,view},'');}
function syncRoute(mode='push'){
  const params=new URLSearchParams({project:state.project});if(state.selected)params.set('topic',state.selected);if(state.event)params.set('event',state.event);
  const url='/bible?'+params;history[mode==='replace'||url===location.pathname+location.search?'replaceState':'pushState']({},'',url);saveView();
}
async function openRoute(view=null){const params=new URL(location.href).searchParams,project=params.get('project');if(!state.projects.some(p=>p.id===project))throw Error('链接中的作品不存在，请选择作品。');await selectProject(project,{mode:'none',topic:params.get('topic'),event:params.get('event'),view,restore:false});}
window.addEventListener('popstate',()=>task(async()=>{if(new URL(location.href).searchParams.has('project'))await openRoute(history.state?.view);else await selectProject(state.project,{mode:'replace'});})());
function applyScale(){const plane=$('#map-plane');plane.style.transform=`scale(${state.scale})`;$('#map-size').style.width=(state.mapWidth||900)*state.scale+'px';$('#map-size').style.height=(state.mapHeight||600)*state.scale+'px';$('#zoom-value').textContent=Math.round(state.scale*100)+'%';}
function focusNode(id){const node=all('.map-node').find(n=>n.dataset.node===id);if(!node)return;const view=$('#map-viewport');view.scrollTo(Math.max(0,(parseFloat(node.style.left)+107)*state.scale-view.clientWidth/2),Math.max(0,(parseFloat(node.style.top)+39)*state.scale-view.clientHeight/2));}
function setMode(){const has=!!state.graph;$('#map-viewport').hidden=!has||state.outline;$('#bible-outline').hidden=!has||!state.outline;$('#map-mode').setAttribute('aria-pressed',String(!state.outline));$('#outline-mode').setAttribute('aria-pressed',String(state.outline));$('.zoom-tools').hidden=state.outline;saveView();}
async function selectProject(id,navigation={}){
  saveNewTopicDraft();$('#new-topic-dialog').close();
  saveDraft();saveView();const serial=++state.serial;++state.detailSerial;state.project=id;state.selected=null;state.detail=null;state.event=null;state.filter='';state.graph=null;
  $('#detail-content').hidden=true;$('#detail-empty').hidden=false;$('#bible-detail').classList.remove('open');
  $('#bible-project').value=id;$('#new-topic').disabled=!id;if(!id){render();return;}
  cache.set('project:'+state.workspace,id);
  const saved=navigation.view||cache.get('bible-view:'+projectKey(),{});state.scale=saved.scale||.85;state.filter=navigation.topic&&!navigation.view?'':saved.filter||'';state.outline=saved.outline??matchMedia('(max-width:640px)').matches;$('#show-past').checked=!!saved.past;$('#bible-search').value='';
  const graph=await api('bible_view',{project:id});if(serial!==state.serial)return;state.graph=graph;state.collapsed=new Set(saved.collapsed||graph.modules.map(m=>'module:'+m));
  state.ticket=cache.get('bible-ticket:'+projectKey());render();
  const target=navigation.topic||(navigation.restore===false?null:saved.selected);
  if(target){if(!graph.topics.some(t=>t.id===target))throw Error('链接中的主题不存在，请从目录选择主题。');await selectTopic(target,true,navigation.event||null,'none');}
  requestAnimationFrame(()=>{if(serial!==state.serial)return;if(typeof saved.x==='number'&&(!navigation.topic||navigation.view)){$('#map-viewport').scrollLeft=saved.x;$('#map-viewport').scrollTop=saved.y||0;}else if(state.selected)focusNode(state.selected);$('#bible-detail').scrollTop=navigation.view?.detailY||0;});
  if(navigation.mode!=='none')syncRoute(navigation.mode||'push');
}
function visibleTopics(){const query=$('#bible-search').value.trim().toLocaleLowerCase();return (state.graph?.topics||[]).filter(t=>(!state.filter||t.module===state.filter)&&($('#show-past').checked||!['parked','superseded'].includes(t.status))&&(!query||(t.title+' '+t.summary).toLocaleLowerCase().includes(query)));}
function render(){
  $('#map-empty').hidden=!!state.graph;$('#map-title').textContent=state.project||'让零散的想法，慢慢连起来。';$('#bible-stage').textContent=state.graph?({frozen:'已冻结',revising:'修订中',building:'构筑中'}[state.graph.bible_status]||'构筑中'):'';
  $('#map-meta').textContent=state.graph?`${state.graph.topics.length} 个主题 · 从一个问题，看到它的来路。`:'选择一个作品，查看已有设定与讨论。';
  const pending=state.graph?.pending;$('#bible-pending').hidden=!pending;$('#archive-bible').hidden=pending?.status==='pending';$('#pending-label').textContent=pending?.status==='pending'?'有一次提交需要恢复，已保存的内容仍在。':'有一段讨论待完成，可以复制恢复指令接着处理。';
  $('#module-list').replaceChildren();$('#module-options').replaceChildren();
  if(state.graph){for(const module of ['',...state.graph.modules]){const count=state.graph.topics.filter(t=>!module||t.module===module).length;const button=element('button',state.filter===module?'active':'');button.type='button';button.append(element('span','',module?module.replace(/\.md$/,''):'全部主题'),element('small','',count));button.onclick=()=>{state.filter=module;if(module)state.collapsed.delete('module:'+module);render();saveView();};$('#module-list').append(button);if(module){const opt=element('option');opt.value=module;$('#module-options').append(opt);}}}
  renderGraph();renderOutline();renderTicketStatus();setMode();
}
function hierarchy(){
  const topics=visibleTopics();const root={id:'root',title:state.project,kind:'root',subtitle:'创作的起点',children:[]};
  for(const module of [...new Set(topics.map(t=>t.module))]){
    const group={id:'module:'+module,title:module.replace(/\.md$/,''),kind:'module',subtitle:'设定主题',children:[]};
    for(const topic of topics.filter(t=>t.module===module)){
      const node={id:topic.id,title:topic.title,kind:'topic',status:topic.status,revision:!!topic.revises,topic:topic.id,subtitle:(topic.needs_review?.length?'关联待复核 · ':'')+labels[topic.status],children:[]};
      const prompts=state.graph.prompts.filter(p=>p.topic_id===topic.id&&($('#show-past').checked||p.id===topic.prompt_id));
      for(const prompt of prompts){const question={id:prompt.id,title:prompt.question,kind:'prompt',topic:topic.id,event:prompt.event_id,subtitle:'当时的问题',children:[]};
        for(const [key,value] of Object.entries(prompt.options)){const chosen=prompt.selected.includes(key);if(!$('#show-past').checked&&prompt.selected.length&&!chosen)continue;question.children.push({id:prompt.id+':'+key,title:value,kind:'option',status:chosen?'complete':prompt.selected.length?'parked':'proposal',topic:topic.id,event:prompt.event_id,subtitle:`方案 ${key} · ${chosen?'已采用':prompt.selected.length?'未采用':'待比较'}`,children:[]});}
        node.children.push(question);
      }
      group.children.push(node);
    }
    root.children.push(group);
  }
  return root;
}
function renderGraph(){
  const plane=$('#map-plane');plane.replaceChildren();if(!state.graph)return;
  const query=$('#bible-search').value.trim(),count=visibleTopics().length;$('#search-result').hidden=!query&&count>0;$('#search-result').textContent=count?`找到 ${count} 个主题`:'没有匹配的主题。试试其他词、清空分类或显示旧方向。';
  const tree=hierarchy(),nodes=[],links=[];let row=0;
  function walk(node,depth){node.x=30+depth*278;const expanded=!!query&&['root','module'].includes(node.kind)||!state.collapsed.has(node.id)&&(node.kind!=='topic'||state.selected===node.id||state.expandedTopic===node.id);const children=expanded?node.children:[];if(children.length){for(const child of children){walk(child,depth+1);links.push([node,child]);}node.y=(children[0].y+children.at(-1).y)/2;}else{node.y=30+row*110;row++;}node.expanded=expanded;nodes.push(node);}
  walk(tree,0);state.mapWidth=Math.max(600,...nodes.map(n=>n.x+250));state.mapHeight=Math.max(400,row*110+70);
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('width',String(state.mapWidth));svg.setAttribute('height',String(state.mapHeight));svg.setAttribute('aria-hidden','true');
  for(const [from,to] of links){const path=document.createElementNS(svg.namespaceURI,'path');const x1=from.x+214,y1=from.y+39,x2=to.x,y2=to.y+39;path.setAttribute('d',`M${x1},${y1} C${x1+32},${y1} ${x2-32},${y2} ${x2},${y2}`);svg.append(path);}
  plane.append(svg);
  for(const topic of state.graph.topics.filter(t=>t.revises)){const from=nodes.find(n=>n.id===topic.revises),to=nodes.find(n=>n.id===topic.id);if(from&&to){const path=document.createElementNS(svg.namespaceURI,'path');const x=from.x+214,y1=from.y+39,y2=to.y+39;path.setAttribute('d',`M${x},${y1} C${x+44},${y1} ${x+44},${y2} ${x},${y2}`);path.setAttribute('stroke-dasharray','5 5');const title=document.createElementNS(svg.namespaceURI,'title');title.textContent='修订关系';path.append(title);svg.append(path);}}
  for(const node of nodes){const box=element('div',`map-node ${node.kind} ${node.status||''} ${node.revision?'revision':''} ${state.selected===node.id?'selected':''}`);box.style.left=node.x+'px';box.style.top=node.y+'px';box.dataset.node=node.id;const button=element('button');button.type='button';button.append(element('strong','',node.title),element('small','',node.subtitle));button.title=node.title;button.onclick=task(async()=>{if(node.topic)await selectTopic(node.topic,true,node.event);else if(node.kind==='module'){state.collapsed.has(node.id)?state.collapsed.delete(node.id):state.collapsed.add(node.id);renderGraph();saveView();}});box.append(button);
    if(node.children.length){const toggle=element('button','node-toggle',node.expanded?'−':'+');toggle.type='button';toggle.setAttribute('aria-label',(node.expanded?'折叠':'展开')+node.title);toggle.setAttribute('aria-expanded',String(node.expanded));toggle.onclick=()=>{if(node.expanded)state.collapsed.add(node.id);else{state.collapsed.delete(node.id);if(node.kind==='topic')state.expandedTopic=node.id;}renderGraph();saveView();};box.append(toggle);}plane.append(box);}
  applyScale();
}
function renderOutline(){const outline=$('#bible-outline');outline.replaceChildren();if(!state.graph)return;const topics=visibleTopics();for(const module of [...new Set(topics.map(t=>t.module))]){const group=element('details');group.open=true;group.append(element('summary','',module.replace(/\.md$/,'')));for(const topic of topics.filter(t=>t.module===module)){const button=element('button');button.type='button';button.append(element('strong','',topic.title),element('small','',`${labels[topic.status]} · ${topic.summary}`));button.onclick=task(()=>selectTopic(topic.id));group.append(button);}outline.append(group);}}
async function selectTopic(id,open=true,eventId=null,mode='push'){
  saveDraft();saveView();const serial=++state.detailSerial,project=state.project;
  const detail=await api('bible_view',{project,topic_id:id,event_id:eventId});if(serial!==state.detailSerial||project!==state.project)return;
  state.selected=id;state.detail=detail;state.event=eventId;state.history=detail.history;state.collapsed.delete('module:'+detail.topic.module);state.collapsed.delete(id);
  $('#idea-input').value=cache.get(draftKey(),detail.topic.draft||'');$('#detail-content').hidden=false;$('#detail-empty').hidden=true;if(open){document.body.classList.remove('detail-closed');$('#bible-detail').classList.add('open');}
  renderDetail();renderGraph();if(open&&!state.outline)focusNode(id);if(open&&mode!=='none')syncRoute(mode);saveView();
}
function renderDetail(){
  const {topic,canon,event}=state.detail;$('#topic-title').textContent=topic.title;$('#topic-status').textContent=labels[topic.status]||topic.status;$('#topic-module').textContent=topic.module;$('#topic-decision').textContent=topic.decision||'这个想法还在慢慢成形。';$('#topic-canon').textContent=canon||'还没有写入该模块的设定。';
  $('#topic-questions').textContent=[topic.open_questions?.length?'待讨论：'+topic.open_questions.join('；'):'',topic.intentional_blanks?.length?'有意留白：'+topic.intentional_blanks.join('；'):''].filter(Boolean).join('\n');
  $('#review-note').hidden=!topic.needs_review?.length;$('#review-note').textContent='关联设定已有修订，下一次构筑需要一起复核。';
  const adopted=['complete','superseded'].includes(topic.status);$('#save-idea').disabled=adopted;$('#park-idea').disabled=adopted;
  $('#previous-topic').hidden=!topic.revises;$('#current-topic').hidden=!topic.replaced_by;
  $('#source-note').hidden=!state.event;$('#source-note').textContent='这次将带上选中的历史讨论，结合当前有效设定继续。';$('#clear-history-focus').hidden=!state.event;
  renderHistory();const original=$('#history-original');original.replaceChildren();original.hidden=!event;
  if(event){if(event.kind==='legacy'){original.append(element('p','small muted','旧台账记录；原始问答与时间未保存。'),element('div','bible-text',event.legacy_decision||event.summary));}
    else{const data=event.data;for(const [label,text] of [['你的原话',data.transcript?.user],['当时的回答',data.transcript?.assistant]]){if(text!=null){original.append(element('h4','',label),element('div','bible-text',text));}}if(data.prompt){original.append(element('h4','','当时的选项'),element('div','bible-text',data.prompt.question+'\n'+Object.entries(data.prompt.options).map(([k,v])=>k+'. '+v).join('\n')));}for(const [module,edit] of Object.entries(data.edits||{})){const d=element('details');d.append(element('summary','','设定修改 · '+module),element('h4','','修改前'),element('div','bible-text',edit.before||'尚未创建'),element('h4','','修改后'),element('div','bible-text',edit.after));original.append(d);}if(!original.childNodes.length)original.append(element('p','muted','这条记录没有讨论原文。'));}}
  renderTicketStatus();
}
function renderHistory(){const list=$('#history-list');list.replaceChildren();for(const record of state.history||[]){const row=element('button','history-row'+(state.event===record.id?' active':''));row.type='button';row.append(element('small','',(record.created_at?new Date(record.created_at).toLocaleString('zh-CN'):'旧记录 · 时间未保存')+' · '+(labels[record.status]||'记录')),element('strong','',record.summary||'记下了这个方向'));row.onclick=task(()=>selectTopic(state.selected,true,record.id));list.append(row);}if(!list.childNodes.length)list.append(element('p','small muted','今后的讨论会留在这里。'));$('#more-history').hidden=state.detail.next_cursor==null;}
async function refresh(){if(!state.project||document.hidden||state.busy||refresh.running)return;refresh.running=true;const project=state.project,serial=state.serial;try{const graph=await api('bible_view',{project,version:state.graph?.version});if(project!==state.project||serial!==state.serial)return;if(!graph.unchanged){state.graph=graph;render();if(state.selected&&graph.topics.some(t=>t.id===state.selected))await selectTopic(state.selected,false,state.event);}if(state.ticket?.id){const current=await api('bible_view',{project,request_id:state.ticket.id});if(project!==state.project)return;const previous=state.ticket.status;state.ticket={...state.ticket,...current};cache.set('bible-ticket:'+projectKey(),state.ticket);if(current.status==='committed'&&previous!=='committed'&&$('#idea-input').value===state.ticket.user_text){$('#idea-input').value='';saveDraft();}renderTicketStatus();}}catch(error){message(error.message);}finally{refresh.running=false;}}
function renderTicketStatus(){agentPanel.changed();$('#last-bible-ticket').hidden=!state.ticket;$('#bible-ticket-status').textContent=state.ticket?'上张小票 · '+(labels[state.ticket.status]||state.ticket.status):'';}
let ticketShown=null,fullTicket=false;
function showTicket(ticket){ticketShown=ticket;fullTicket=false;$('#bible-ticket-text').value=ticket.instruction;$('#bible-ticket-format').textContent='完整指令';$('#bible-ticket-format').disabled=!ticket.full_instruction;$('#bible-ticket-dialog').showModal();}
async function copy(text){try{await navigator.clipboard.writeText(text);message('已复制，粘贴到 Agent 即可。');}catch{message('小票已保存，可选中指令手动复制。');}}
async function edit(action,extra={}){if(!state.detail)throw Error('先选择一个主题。');return api('bible_edit',{project:state.project,topic_id:state.selected,expected_version:state.detail.topic.version,action,text:$('#idea-input').value,...extra},true);}
async function request(kind){agentPanel.check();const project=state.project,user_text=$('#idea-input').value;if(!user_text.trim())throw Error('先写下想继续讨论或修改的内容。');const oldQuestion=state.event&&!(state.detail.prompt?.id&&state.detail.event?.data?.prompt?.id===state.detail.prompt.id);const result=await edit('request',{kind:oldQuestion?'revise':kind,prompt_id:state.detail.topic.prompt_id,history_ids:state.event?[state.event]:null});if(project!==state.project)return;state.ticket={...result,user_text};cache.set('bible-ticket:'+projectKey(),state.ticket);if(result.topic_id!==state.selected)await selectTopic(result.topic_id);renderTicketStatus();if(!await agentPanel.send(result)){showTicket(result);await copy(result.instruction);}await refresh();if(result.topic_id&&!state.outline)focusNode(result.topic_id);}
$('#bible-project').onchange=task(e=>selectProject(e.target.value));$('#bible-search').oninput=()=>{renderGraph();renderOutline();};$('#show-past').onchange=()=>{render();saveView();};$('#map-mode').onclick=()=>{state.outline=false;setMode();};$('#outline-mode').onclick=()=>{state.outline=true;setMode();};
$('#zoom-in').onclick=()=>{state.scale=Math.min(1.5,state.scale+.1);applyScale();saveView();};$('#zoom-out').onclick=()=>{state.scale=Math.max(.3,state.scale-.1);applyScale();saveView();};$('#fit-map').onclick=()=>{const view=$('#map-viewport');state.scale=Math.max(.3,Math.min(1,(view.clientWidth-30)/state.mapWidth,(view.clientHeight-30)/state.mapHeight));applyScale();view.scrollTo(0,0);saveView();};
$('#idea-input').oninput=saveDraft;$('#save-idea').onclick=task(async()=>{await edit('draft');message('草稿已保存。');await refresh();});$('#park-idea').onclick=task(async()=>{await edit('park');message('这个方向已搁置，可在旧方向中找到。');await refresh();});$('#continue-idea').onclick=task(()=>request('continue'));$('#revise-idea').onclick=task(()=>request('revise'));
const newTopicKey=()=> 'bible-new-topic:'+projectKey();
function saveNewTopicDraft(){if(!$('#new-topic-dialog').open)return;const saved=cache.set(newTopicKey(),{title:$('#new-topic-title').value,text:$('#new-topic-text').value,module:$('#new-topic-module').value});$('#new-topic-draft-hint').textContent=saved?'草稿已保留，关闭后可以继续填写。':'草稿尚未保存，请复制备份';}
$('#new-topic-form').addEventListener('input',saveNewTopicDraft);
$('#new-topic').onclick=()=>{if(!state.project)return message('先选择作品。');const draft=cache.get(newTopicKey(),{})||{};$('#new-topic-title').value=draft.title||'';$('#new-topic-text').value=draft.text||'';$('#new-topic-module').value=draft.module||state.filter||'核心概念.md';$('#new-topic-dialog').showModal();$('#new-topic-title').focus();};
$('#new-topic-form').onsubmit=task(async event=>{
  event.preventDefault();const project=state.project,key=newTopicKey();
  const draft={title:$('#new-topic-title').value,text:$('#new-topic-text').value,module:$('#new-topic-module').value};
  const result=await api('bible_edit',{project,action:'create',...draft},true);
  const current=cache.get(key);if(!current||JSON.stringify(current)===JSON.stringify(draft))cache.set(key,null);
  if(project!==state.project)return;
  $('#new-topic-dialog').close();state.filter='';await refresh();await selectTopic(result.topic.id);
});
$('#copy-topic-link').onclick=()=>copy(location.origin+'/bible?'+new URLSearchParams({project:state.project,topic:state.selected,...(state.event?{event:state.event}:{})}));
$('#recover-bible').onclick=task(async()=>{const result=await api('bible_edit',{project:state.project,action:'recovery',operation_id:state.graph.pending.operation_id},true);showTicket(result);await copy(result.instruction);});
$('#archive-bible').onclick=task(async()=>{await api('bible_edit',{project:state.project,action:'archive',operation_id:state.graph.pending.operation_id,text:'用户选择保留当前准备材料并重新准备'},true);message('原任务已保留，可以修改要求后重新复制指令。');await refresh();});
$('#last-bible-ticket').onclick=()=>{if(state.ticket)showTicket(state.ticket);};$('#copy-bible-ticket').onclick=()=>copy($('#bible-ticket-text').value);$('#bible-ticket-format').onclick=()=>{fullTicket=!fullTicket;$('#bible-ticket-text').value=fullTicket?ticketShown.full_instruction:ticketShown.instruction;$('#bible-ticket-format').textContent=fullTicket?'短指令':'完整指令';};$('#close-detail').onclick=()=>{document.body.classList.add('detail-closed');$('#bible-detail').classList.remove('open');};$('#clear-history-focus').onclick=task(()=>selectTopic(state.selected));
$('#previous-topic').onclick=task(()=>{ $('#show-past').checked=true;return selectTopic(state.detail.topic.revises);});$('#current-topic').onclick=task(()=>selectTopic(state.detail.topic.replaced_by));
$('#more-history').onclick=task(async()=>{const project=state.project,selected=state.selected;const result=await api('bible_view',{project,topic_id:selected,cursor:state.detail.next_cursor});if(project!==state.project||selected!==state.selected)return;state.history.push(...result.history);state.detail.next_cursor=result.next_cursor;renderHistory();});
for(const button of all('[data-close]'))button.onclick=()=>button.closest('dialog').close();
const viewport=$('#map-viewport');let drag=null;viewport.addEventListener('pointerdown',event=>{if(event.pointerType!=='mouse'||event.target.closest('button')||event.button!==0)return;drag={x:event.clientX,y:event.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};viewport.setPointerCapture(event.pointerId);viewport.classList.add('dragging');});viewport.addEventListener('pointermove',event=>{if(drag){viewport.scrollLeft=drag.left+drag.x-event.clientX;viewport.scrollTop=drag.top+drag.y-event.clientY;}});for(const name of ['pointerup','pointercancel'])viewport.addEventListener(name,()=>{drag=null;viewport.classList.remove('dragging');saveView();});viewport.addEventListener('scroll',()=>{clearTimeout(viewport.saveTimer);viewport.saveTimer=setTimeout(saveView,150);});
$('#theme-button').onclick=()=>{const dark=document.documentElement.dataset.theme!=='dark';document.documentElement.dataset.theme=dark?'dark':'light';cache.set('theme',dark?'dark':'light');};
window.addEventListener('storage',event=>{if(event.key==='diner:theme')document.documentElement.dataset.theme=cache.get('theme','light');});
window.addEventListener('beforeunload',event=>{saveDraft();saveView();saveNewTopicDraft();if(unsavedStorage.size){event.preventDefault();event.returnValue='';}});
async function boot(){
  document.documentElement.dataset.theme=cache.get('theme',cache.get('reading',{})?.theme||cache.get('bible-theme','light'));
  cache.set('theme',document.documentElement.dataset.theme);
  const boot=await api('bootstrap');state.token=boot.token;state.workspace=boot.workspace_id;
  const projects=await api('projects');state.projects=projects;
  $('#bible-project').replaceChildren(element('option','','选择作品'));$('#bible-project').firstChild.value='';
  for(const project of projects){const option=element('option','',project.name);option.value=project.id;$('#bible-project').append(option);}
  history.scrollRestoration='manual';
  if(new URL(location.href).searchParams.has('project')){try{await openRoute(history.state?.view);}catch(error){message(error.message);}}
  else{const chosen=cache.get('project:'+state.workspace);await selectProject(projects.some(p=>p.id===chosen)?chosen:(projects[0]?.id||''),{mode:'replace'});}
  setInterval(refresh,2500);
}
const agentPanel=new AgentPanel(api,()=>[state.workspace,'bible',state.project,state.selected],()=>refresh());
boot().then(()=>agentPanel.init()).catch(error=>message(error.message));
