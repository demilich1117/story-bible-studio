// Shared by construction and RP. The browser sends saved tickets, never shell commands.
export class AgentPanel {
  constructor(api, scope, completed) {
    this.api=api;this.scope=scope;this.completed=completed;this.root=document.querySelector('#agent-panel');this.key='';
    this.root.innerHTML=`<label class="field">生成方式<select id="agent-provider"><option value="manual">复制小票</option><option value="codex">Codex · 本地生成</option><option value="opencode">OpenCode · 本地生成</option><option value="opencode_server">OpenCode · 共享后台</option></select></label>
      <div id="agent-model-fields" hidden><label class="field">主模型<select id="agent-model"><option value="">使用平台默认模型（未指定）</option><option value="custom">手动填写模型 ID</option></select></label>
      <label class="field" id="agent-model-custom-field" hidden>模型 ID<input id="agent-model-custom" type="text" maxlength="240" autocomplete="off" placeholder="provider/model"></label>
      <label class="field" id="agent-reasoning-field" hidden>推理强度<select id="agent-reasoning"><option value="">使用 CLI 默认强度（未指定）</option></select></label>
      <p class="small muted" id="agent-reasoning-hint" role="status" hidden></p>
      <p class="small muted" id="agent-model-hint" role="status" aria-live="polite"></p></div>
      <p class="small muted" id="agent-hint">选择本地生成后，发送即可由 Agent 处理并保存结果。</p>
      <p id="agent-status" class="small" role="status" aria-live="polite"></p>
      <p id="agent-job-model" class="small muted" hidden></p>
      <p id="agent-session" class="small muted" hidden></p>
      <div class="agent-actions"><button id="agent-refresh" type="button">检查生成连接</button><button id="agent-retry" type="button" hidden>重试原任务</button><button id="agent-stop" type="button" hidden>停止生成</button><button id="agent-copy-session" type="button" hidden>复制 session ID</button></div>
      <details id="agent-response" hidden><summary>查看 Agent 回复</summary><div class="bible-text" id="agent-reply"></div></details>`;
    this.select=this.root.querySelector('select');
    this.modelSelect=this.root.querySelector('#agent-model');this.modelInput=this.root.querySelector('#agent-model-custom');
    this.reasoningSelect=this.root.querySelector('#agent-reasoning');this.catalogs={};
    this.select.onchange=()=>{this.save('agent-provider',this.select.value);this.hint();this.refreshModels();};
    this.modelSelect.onchange=()=>{this.root.querySelector('#agent-model-custom-field').hidden=this.modelSelect.value!=='custom';this.saveModel();this.refreshReasoning();};
    this.modelInput.oninput=()=>{this.saveModel();this.refreshReasoning();};
    this.reasoningSelect.onchange=()=>{this.save(this.reasoningKey(),this.reasoningSelect.value||null);this.refreshReasoning();};
    this.root.querySelector('#agent-stop').onclick=()=>this.action(async()=>{
      const key=this.key,record=this.record;if(!record?.job?.id)return;
      record.job=await this.api('agent_stop',{job_id:record.job.id},true);
      this.save(key,record);if(key===this.key){this.record=record;this.render();}
    });
    this.root.querySelector('#agent-retry').onclick=()=>this.action(()=>this.send(this.record.ticket,true));
    this.root.querySelector('#agent-refresh').onclick=()=>this.action(()=>this.refreshProviders());
    this.root.querySelector('#agent-copy-session').onclick=()=>this.action(()=>navigator.clipboard.writeText(this.record.job.platform_session));
    window.addEventListener('storage',event=>{if(event.key==='diner:'+this.key){this.record=this.read(this.key);this.render();}});
  }
  read(key){try{return JSON.parse(localStorage.getItem('diner:'+key));}catch{return null;}}
  save(key,value){try{localStorage.setItem('diner:'+key,JSON.stringify(value));}catch{}}
  async init(){
    this.select.value=this.read('agent-provider')||'manual';if(!this.select.value)this.select.value='manual';
    try{await this.refreshProviders();}catch{this.providers=[];}
    this.hint();this.changed();this.timer=setInterval(()=>this.poll(),2000);
  }
  async refreshProviders(){
    this.providers=(await this.api('agent_providers')).providers;
    const labels={codex:'Codex · 本地生成',opencode:'OpenCode · 本地生成',opencode_server:'OpenCode · 共享后台'};
    for(const item of this.providers){const option=this.select.querySelector(`[value="${item.id}"]`);if(option)option.textContent=labels[item.id]+(!item.available?(item.id==='opencode_server'?' · 未连接':item.desktop_available?' · 仅检测到桌面版':' · 未检测到 CLI'):'');}
    this.hint();
    await this.refreshModels();
  }
  modelKey(provider=this.select.value){return 'agent-model:'+JSON.stringify([this.scope()[0]||'',provider]);}
  selectedModel(){return (this.modelSelect.value==='custom'?this.modelInput.value.trim():this.modelSelect.value)||null;}
  saveModel(){this.save(this.modelKey(),this.selectedModel());}
  reasoningKey(){return 'agent-reasoning:'+JSON.stringify([this.scope()[0]||'',this.select.value,this.selectedModel()||'']);}
  selectedReasoning(){return this.select.value==='codex'?(this.reasoningSelect.value||null):null;}
  refreshReasoning(){
    const codex=this.select.value==='codex',hint=this.root.querySelector('#agent-reasoning-hint');
    this.root.querySelector('#agent-reasoning-field').hidden=!codex;hint.hidden=!codex;
    if(!codex)return;
    const labels={none:'无 · none',minimal:'最低 · minimal',low:'低 · low',medium:'中 · medium',high:'高 · high',xhigh:'很高 · xhigh',max:'最大 · max',ultra:'极高 · ultra'};
    const model=this.catalogs.codex?.find(item=>item.id===this.selectedModel());
    const efforts=model?.reasoning_efforts||Object.keys(labels),saved=this.read(this.reasoningKey())||'';
    this.reasoningSelect.replaceChildren(new Option('使用 CLI 默认强度（未指定）',''),...efforts.map(value=>new Option(labels[value]||value,value)));
    this.invalidReasoning=!!saved&&!efforts.includes(saved);
    if(this.invalidReasoning)this.reasoningSelect.add(new Option(`上次选择已不支持：${labels[saved]||saved}`,saved));
    this.reasoningSelect.value=saved;
    this.reasoningSelect.disabled=!!this.codexBackendOutdated;
    hint.textContent=this.invalidReasoning?'此模型不再支持上次的推理强度，请重新选择。':model?
      (efforts.length?`仅显示此模型支持的强度${model.default_reasoning_effort?`；模型建议值：${labels[model.default_reasoning_effort]||model.default_reasoning_effort}`:''}。默认选项沿用 CLI 配置。`:'此模型没有可选推理强度。'):
      '选择目录中的模型后可查看其支持的强度；当前选项是否可用由 CLI 确认。';
  }
  async refreshModels(){
    const provider=this.select.value,sequence=(this.modelSequence||0)+1;this.modelSequence=sequence;
    const fields=this.root.querySelector('#agent-model-fields'),hint=this.root.querySelector('#agent-model-hint');
    fields.hidden=provider==='manual';this.refreshReasoning();if(fields.hidden)return;
    const saved=this.read(this.modelKey())||'';
    const populate=(models,value)=>{
      this.modelSelect.replaceChildren(new Option('使用平台默认模型（未指定）',''),...models.map(m=>new Option(m.name||m.id,m.id)),new Option('手动填写模型 ID','custom'));
      this.modelSelect.value=models.some(m=>m.id===value)?value:value?'custom':'';
      this.modelInput.value=value;this.modelInput.placeholder=provider==='codex'?'填写 Codex 模型 ID':'provider/model';
      this.root.querySelector('#agent-model-custom-field').hidden=this.modelSelect.value!=='custom';
      this.refreshReasoning();
    };
    populate([],saved);hint.textContent='正在读取可用模型…';
    try{
      const data=await this.api('agent_models',{provider});
      if(sequence!==this.modelSequence||provider!==this.select.value)return;
      if(provider==='codex'){
        this.codexBackendOutdated=data.supports_reasoning_effort!==true&&
          !(Array.isArray(data.models)&&data.models.length>0&&data.models.every(model=>Array.isArray(model.reasoning_efforts)));
        this.refreshReasoning();
        if(this.codexBackendOutdated)throw Error('后台仍是旧版本。请先点击「关闭本地后台」，再双击「启动Codex工作台.cmd」并刷新此页。');
      }
      const emptyCustom=this.modelSelect.value==='custom'&&!this.selectedModel();
      this.catalogs[provider]=data.models||[];
      populate(data.models||[],this.selectedModel()||'');hint.textContent=data.hint||'选择用于下一次生成的主模型。';
      if(emptyCustom){this.modelSelect.value='custom';this.root.querySelector('#agent-model-custom-field').hidden=false;}
    }catch(error){if(sequence===this.modelSequence&&provider===this.select.value)hint.textContent=error.message+(provider==='codex'&&this.codexBackendOutdated?'':' 可手动填写模型 ID，或明确使用平台默认模型。');}
  }
  hint(){const p=this.providers?.find(p=>p.id===this.select.value);const base=p&&!p.available?p.reason:p?.hint|| (this.select.value==='manual'?'复制小票后交给你选择的 Agent。':'使用本机平台账号，每次从当前任务的已保存上下文开始。');this.root.querySelector('#agent-hint').textContent=base+(this.select.value.startsWith('opencode')?' 记忆整理和复杂召回由主 Agent 完成，不启用 Luna 辅助配置。':'');}
  check(){this.changed();if(['starting','running','stopping','unknown'].includes(this.record?.job?.status))throw Error('原任务仍在处理，请等待完成或先停止。');if(this.select.value==='manual')return;const p=this.providers?.find(p=>p.id===this.select.value);if(!p?.available)throw Error(p?.reason||'无法检测生成平台，请刷新工作台。');}
  changed(){const key='agent-job:'+JSON.stringify(this.scope());if(key!==this.key){this.key=key;this.record=this.read(key);this.render();}}
  render(){
    const job=this.record?.job,active=['starting','running','stopping'].includes(job?.status);
    this.root.querySelector('#agent-status').textContent=job?.message||'';
    const jobModel=this.root.querySelector('#agent-job-model');jobModel.hidden=!job;
    jobModel.textContent=job?`本次请求主模型：${job.model||'平台默认（未指定）'}${job.provider==='codex'||this.record?.provider==='codex'?` · 推理强度：${job.reasoning_effort||'CLI 默认（未指定）'}`:''}${job.helper_policy==='main_agent_only'?' · 辅助整理：主 Agent':''}`:'';
    this.root.querySelector('#agent-stop').hidden=!(active||job?.server_pending);
    this.root.querySelector('#agent-stop').textContent=job?.status==='unknown'?'停止并确认后台任务':'停止生成';
    const session=this.root.querySelector('#agent-session');session.hidden=!job?.server_url;
    session.textContent=job?.server_url?`${job.server_url} · session ${job.platform_session||'正在创建'}。桌面端需连接同一服务并打开此工作区。`:'';
    this.root.querySelector('#agent-copy-session').hidden=!job?.platform_session;
    this.root.querySelector('#agent-retry').hidden=!this.record||active||job?.status==='completed'||job?.status==='unknown';
    this.root.querySelector('#agent-response').hidden=!job?.reply;
    this.root.querySelector('#agent-reply').textContent=job?.reply||'';
    if(job?.reply&&job.status==='completed'&&this.openedReply!==job.id){this.root.querySelector('#agent-response').open=true;this.openedReply=job.id;}
  }
  async action(fn){try{await fn();}catch(error){this.root.querySelector('#agent-status').textContent=error.message;}}
  async send(ticket,retry=false){
    if(this.select.value==='manual'&&!retry)return false;
    this.changed();const key=this.key,provider=this.select.value==='manual'?this.record?.provider:this.select.value;
    if(provider==='codex'&&this.codexBackendOutdated)throw Error('后台仍是旧版本，请关闭本地后台并重新启动后再生成。');
    const model=this.select.value==='manual'?(this.record?.model||null):this.selectedModel();
    const reasoning_effort=this.select.value==='manual'?(this.record?.reasoning_effort||null):this.selectedReasoning();
    if(this.select.value==='codex'&&reasoning_effort&&!(this.catalogs.codex?.find(item=>item.id===model)?.reasoning_efforts||[reasoning_effort]).includes(reasoning_effort))throw Error('此模型不支持所选推理强度，请重新选择。');
    if(this.select.value!=='manual'&&this.modelSelect.value==='custom'&&!model)throw Error('请填写模型 ID，或选择使用平台默认模型。');
    const record={ticket,provider,model,reasoning_effort,attempt:crypto.randomUUID(),job:{status:'starting',model,reasoning_effort,message:'正在发送给 Agent…'}};
    this.record=record;this.save(key,record);this.render();
    try{record.job=await this.api('agent_start',{ticket_path:ticket.ticket_path,provider,retry,model,...(provider==='codex'?{reasoning_effort}:{})},true);}
    catch(error){record.job={status:'failed',model,reasoning_effort,message:error.message+' 小票已保留，可重试或复制。'};}
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
