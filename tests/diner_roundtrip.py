"""Manually invoked UI-ticket -> real CLI prepare/commit QA, demo workspace only."""
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
workspace=ROOT/'.workbench/demo'
requests=list(workspace.glob('作品/*/会话/*/.workbench/requests/*-prepare.json'))
if not requests:raise SystemExit('Create a demo ticket in the browser first.')
payload=max(requests,key=lambda p:p.stat().st_mtime_ns)
cli=ROOT/'.agents/skills/story-bible-studio/scripts/story_studio.py'
def run(operation,path):
    result=subprocess.run([sys.executable,'-B',str(cli),'workbench','--workspace',str(workspace),'--operation',operation,'--payload-file',str(path)],capture_output=True,text=True,encoding='utf-8',check=True)
    return json.loads(result.stdout)
prepared=run('prepare',payload)
if prepared['status']!='ready':raise SystemExit(prepared['status'])
request=json.loads(payload.read_text(encoding='utf-8'))
commit={k:request[k] for k in ('project','session')}
commit.update(operation_id=prepared['operation_id'],prose='六月往小票上添了一笔，把它重新夹在出餐口。\n\n“再一份薯条。”她重复了一遍，手指在纸面轻轻点了点，“这份现炸。你慢慢坐，雨一时半会儿停不了。”\n\n窗边的灯亮着。她转身回到厨房，留下一阵杯中冰块细小的响声。',regenerate=False,scene_patch={'location':'临窗卡座'})
target=workspace/'qa-commit.json';target.write_text(json.dumps(commit,ensure_ascii=False),encoding='utf-8')
print(json.dumps(run('commit',target),ensure_ascii=False))
