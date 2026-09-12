// Shared by construction and RP. The browser sends saved tickets, never shell commands.
export class AgentPanel {
  constructor(api, scope, completed) {
    this.api=api;this.scope=scope;this.completed=completed;this.root=document.querySelector('#agent-panel');this.key='';
    this.root.innerHTML=`<label class="field">生成方式<select id="agent-provider"><option value="manual">复制小票</option><option value="codex">Codex · 本地生成</option><option value="opencode">OpenCode · 本地生成</option></select></label>
      <p class="small muted" id="agent-hint">选择本地生成后，发送即可由 Agent 处理并保存结果。</p>
      <p id="agent-status" class="small" role="status" aria-live="polite"></p>
      <div class="agent-actions"><button id="agent-retry" type="button" hidden>重试原任务</button><button id="agent-stop" type="button" hidden>停止生成</button></div>
      <details id="agent-response" hidden><summary>查看 Agent 回复</summary><div class="bible-text" id="agent-reply"></div></details>`;
    this.select=this.root.querySelector('select');
    this.select.onchange=()=>{this.save('agent-provider',this.select.value);this.hint();};
    this.root.querySelector('#agent-stop').onclick=()=>this.action(async()=>{
      const key=this.key,record=this.record;if(!record?.job?.id)return;
      await this.api('agent_stop',{job_id:record.job.id},true);
      if(key===this.key)this.root.querySelector('#agent-status').textContent='正在停止，请等待任务退出。';
    });
    this.root.querySelector('#agent-retry').onclick=()=>this.action(()=>this.send(this.record.ticket,true));
    window.addEventListener('storage',event=>{if(event.key==='diner:'+this.key){this.record=this.read(this.key);this.render();}});
  }
  read(key){try{return JSON.parse(localStorage.getItem('diner:'+key));}catch{return null;}}
  save(key,value){try{localStorage.setItem('diner:'+key,JSON.stringify(value));}catch{}}
  async init(){
    try{this.providers=(await this.api('agent_providers')).providers;}catch{this.providers=[];}
    this.select.value=this.read('agent-provider')||'manual';if(!this.select.value)this.select.value='manual';
    for(const item of this.providers){if(!item.available)this.select.querySelector(`[value="${item.id}"]`).textContent+=( ' · 未检测到');}
    this.hint();this.changed();this.timer=setInterval(()=>this.poll(),2000);
  }
  hint(){const p=this.providers?.find(p=>p.id===this.select.value);this.root.querySelector('#agent-hint').textContent=p&&!p.available?p.reason:this.select.value==='manual'?'复制小票后交给你选择的 Agent。':'使用本机平台账号与默认模型，每次从当前任务的已保存上下文开始。';}
  check(){this.changed();if(['starting','running','stopping','unknown'].includes(this.record?.job?.status))throw Error('原任务仍在处理，请等待完成或先停止。');if(this.select.value==='manual')return;const p=this.providers?.find(p=>p.id===this.select.value);if(!p?.available)throw Error(p?.reason||'无法检测生成平台，请刷新工作台。');}
  changed(){const key='agent-job:'+JSON.stringify(this.scope());if(key!==this.key){this.key=key;this.record=this.read(key);this.render();}}
  render(){
    const job=this.record?.job,active=['starting','running','stopping'].includes(job?.status);
    this.root.querySelector('#agent-status').textContent=job?.message||'';
    this.root.querySelector('#agent-stop').hidden=!active;
    this.root.querySelector('#agent-retry').hidden=!this.record||active||job?.status==='completed'||job?.status==='unknown';
    this.root.querySelector('#agent-response').hidden=!job?.reply;
    this.root.querySelector('#agent-reply').textContent=job?.reply||'';
    if(job?.reply&&job.status==='completed'&&this.openedReply!==job.id){this.root.querySelector('#agent-response').open=true;this.openedReply=job.id;}
  }
  async action(fn){try{await fn();}catch(error){this.root.querySelector('#agent-status').textContent=error.message;}}
  async send(ticket,retry=false){
    if(this.select.value==='manual'&&!retry)return false;
    this.changed();const key=this.key,provider=this.select.value==='manual'?this.record?.provider:this.select.value;
    const record={ticket,provider,attempt:crypto.randomUUID(),job:{status:'starting',message:'正在发送给 Agent…'}};
    this.record=record;this.save(key,record);this.render();
    try{record.job=await this.api('agent_start',{ticket_path:ticket.ticket_path,provider,retry},true);}
    catch(error){record.job={status:'failed',message:error.message+' 小票已保留，可重试或复制。'};}
    if(record.job.status==='completed'&&key===this.key)await this.completed?.();
    this.save(key,record);if(key===this.key){this.record=record;this.render();}return true;
  }
  async poll(){
    this.changed();const key=this.key,record=this.record;
    if(document.hidden||this.polling||!record?.job?.id||!['starting','running','stopping','unknown'].includes(record.job.status))return;
    this.polling=true;
    try{const job=await this.api('agent_status',{job_id:record.job.id});if((this.read(key)?.attempt??record.attempt)!==record.attempt)return;if(job.status==='completed'&&key===this.key)await this.completed?.();if((this.read(key)?.attempt??record.attempt)!==record.attempt)return;record.job=job;this.save(key,record);if(key===this.key){this.record=record;this.render();}}
    catch(error){if(key===this.key)this.root.querySelector('#agent-status').textContent='暂时无法连接后台；任务可能仍在运行，请勿重复发送。';}
    finally{this.polling=false;}
  }
}
