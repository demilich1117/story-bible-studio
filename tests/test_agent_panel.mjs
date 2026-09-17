import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../workbench/static/agent-panel.js',import.meta.url),'utf8');
const {AgentPanel}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
globalThis.Option=class {constructor(text,value){this.text=text;this.value=value;}};

function panel(provider,model,efforts,saved=''){
  const nodes=new Map();
  const item=Object.create(AgentPanel.prototype);
  item.select={value:provider};item.modelSelect={value:model};item.modelInput={value:''};
  item.root={querySelector(key){if(!nodes.has(key))nodes.set(key,{});return nodes.get(key);}};
  item.reasoningSelect={value:'',replaceChildren(...options){this.options=options;},add(option){this.options.push(option);}};
  item.catalogs={[provider]:[{id:model,reasoning_efforts:efforts}]};
  item.scope=()=>['作品','会话'];item.read=()=>saved;item.save=()=>{};
  item.refreshReasoning();return item;
}

for(const provider of ['antigravity','opencode','opencode_server','codex']){
  const choices=provider==='antigravity'?['low','medium','high']:['low','custom-deep'];
  const p=panel(provider,'vendor/model',choices,choices.at(-1));
  assert.equal(p.root.querySelector('#agent-reasoning-field').hidden,false);
  assert.deepEqual(p.reasoningSelect.options.map(o=>o.value),['',...choices]);
  assert.equal(p.selectedReasoning(),choices.at(-1));
  assert.equal(p.invalidReasoning,false);
  assert.ok(p.reasoningKey().includes(provider));
  p.changed=()=>{};p.render=()=>{};p.key='job';
  let payload;
  p.api=async(name,value)=>{payload=value;return {status:'running',id:'test'};};
  await p.send({ticket_path:'ticket.json'});
  assert.equal(payload.reasoning_effort,choices.at(-1));
  assert.equal(payload.provider,provider);
}
const stale=panel('opencode','vendor/model',['low'],'high');
assert.equal(stale.invalidReasoning,true);
stale.changed=()=>{};
await assert.rejects(()=>stale.send({ticket_path:'ticket.json'}),/不支持/);
const unknown=panel('opencode','custom',undefined);
assert.deepEqual(unknown.reasoningSelect.options.map(o=>o.value),['']);
const plain=panel('opencode_server','vendor/plain',[]);
assert.deepEqual(plain.reasoningSelect.options.map(o=>o.value),['']);
console.log('Agent panel: platform options, effort dispatch, unsupported values and defaults passed.');
