import concurrent.futures
import importlib.util
import json
import sys
import threading
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch

from support import StudioCase, ROOT
from studio_core import create_session, read_events, reduce_events, commit_turn, session_lock, LockError, load_yaml, write_yaml, StudioError
from studio_workbench import StudioService, Conflict
from studio_policy import set_config


class WorkbenchTests(StudioCase):
    def setUp(self):
        super().setUp()
        self.player()
        self.session = create_session(self.project, "main", profile_id="courier", opening_id="opening-01")
        self.service = StudioService(self.root, self.root)
        self.p = self.project.name

    def version(self):
        return self.service.version(self.project, self.session)

    def prepare(self, text="米拉核对邮戳。", **kwargs):
        return self.service.prepare(self.p, "main", user_text=text, expected_version=self.version(), **kwargs)

    def test_prepare_commit_retry_and_pending_guard(self):
        data = self.prepare()
        self.assertEqual("ready", data["status"])
        with self.assertRaises(Conflict):
            self.service.configure(self.p, "main", self.version(), pairs=["person=second"])
        result = self.service.commit(self.p, "main", data["operation_id"], "米拉指了指表盘。")
        again = self.service.commit(self.p, "main", data["operation_id"], "米拉指了指表盘。")
        self.assertTrue(again["replayed"])
        self.assertEqual(1, result["turn"])
        self.assertFalse(self.service.pending(self.session))
        with self.assertRaises(Conflict):
            self.service.commit(self.p, "main", data["operation_id"], "不同正文")

    def test_regeneration_excludes_replaced_turn_and_saves_unselected(self):
        commit_turn(self.session, "米拉检查蓝色信封。", "一切如常。", scene_patch={"location":"钟楼"})
        commit_turn(self.session, "我打开信封。", "独有结局标记：紫色烟花。", scene_patch={"location":"独有地点秘密岛"},
                    state_updates={"当前局面.md":"独有后果失去了怀表"})
        prepared = self.prepare("写得更含蓄。", regenerate=True)
        self.assertEqual("ready", prepared["status"])
        for forbidden in ("独有结局标记", "独有地点秘密岛", "独有后果"):
            self.assertNotIn(forbidden, prepared["context"])
        self.assertIn("我打开信封。", prepared["context"])
        result = self.service.commit(self.p,"main",prepared["operation_id"],"信封里是一张旧车票。",scene_patch={"location":"门厅"},regenerate=True)
        state = reduce_events(read_events(self.session))
        self.assertEqual(2,state["current_turn"])
        self.assertEqual("v1",state["selected_variants"]["2"])
        self.service.select(self.p,"main",2,result["variant"],self.version())
        self.assertEqual("门厅",reduce_events(read_events(self.session))["scene"]["location"])
        with self.assertRaises(StudioError):
            self.service.select(self.p,"main",1,"v1",self.version())

    def test_request_expiry_scope_and_single_consumption(self):
        request=self.service.request(self.p,"main",self.version(),"我等待米拉回答。",override={"prose":{"min_chars":20,"max_chars":50}})
        prepared=self.service.prepare(self.p,"main",request_id=request["id"])
        retry=self.service.prepare(self.p,"main",request_id=request["id"])
        self.assertEqual(prepared["operation_id"],retry["operation_id"])
        self.service.commit(self.p,"main",prepared["operation_id"],"米拉检查了邮戳。")
        with self.assertRaises(Conflict):
            self.service.prepare(self.p,"main",request_id=request["id"])
        saved=self.service.snapshot(self.p,"main")
        self.assertEqual(1000,saved["config"]["effective"]["prose"]["min_chars"])
        self.assertIn("--operation prepare",request["full_instruction"])
        request2=self.service.request(self.p,"main",self.version(),"下一轮")
        set_config(self.project,["person=second"],self.session)
        with self.assertRaises(Conflict):
            self.service.prepare(self.p,"main",request_id=request2["id"])

    def test_configuration_is_atomic_and_stale_edit_rejected(self):
        version=self.version()
        events_before=read_events(self.session)
        before=(self.session/"会话配置.yaml").read_bytes()
        with self.assertRaises(StudioError):
            self.service.configure(self.p,"main",version,pairs=["person=second","prose.min_chars=900","prose.max_chars=100"])
        self.assertEqual(before,(self.session/"会话配置.yaml").read_bytes())
        self.service.configure(self.p,"main",version,pairs=["person=second"])
        with self.assertRaises(Conflict):
            self.service.configure(self.p,"main",version,pairs=["person=first"])
        self.service.configure(self.p,"main",self.version(),undo=True)
        self.assertEqual(events_before,read_events(self.session))

    def test_lock_reentrant_only_for_owner_thread(self):
        with session_lock(self.session):
            with session_lock(self.session):
                self.assertTrue((self.session/".session.lock").exists())
            def contender():
                with session_lock(self.session):
                    return "wrong"
            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                with self.assertRaises(LockError):
                    pool.submit(contender).result()
        self.assertFalse((self.session/".session.lock").exists())

    def test_paths_and_corrupt_events_do_not_get_repaired(self):
        with self.assertRaises(StudioError):
            self.service.snapshot("../outside","main")
        with self.assertRaises(StudioError):
            self.service.snapshot(self.p,"../main")
        event=self.session/"events.jsonl"
        with event.open("a",encoding="utf-8") as f:f.write('{"broken":')
        before=event.read_bytes()
        with self.assertRaises(StudioError):self.service.snapshot(self.p,"main")
        self.assertEqual(before,event.read_bytes())

    def test_project_metadata_does_not_read_session_events(self):
        with patch('studio_workbench.read_events',side_effect=AssertionError("should not read sibling story")):
            data=self.service.project(self.p)
        self.assertEqual("main",data["sessions"][0]["id"])

    def test_preview_validates_without_persisting(self):
        before=(self.session/"会话配置.yaml").read_bytes()
        view=self.service.preview(self.p,"main",{"interaction_preset":"short-rp"})
        self.assertEqual(150,view["effective"]["prose"]["min_chars"])
        with self.assertRaises(StudioError):
            self.service.preview(self.p,"main",{"prose":{"min_chars":500,"max_chars":20}})
        self.assertEqual(before,(self.session/"会话配置.yaml").read_bytes())

    def test_legacy_prepare_cannot_overlap_regeneration(self):
        from studio_context import write_context
        commit_turn(self.session,"输入","旧回复")
        self.prepare(regenerate=True)
        with self.assertRaises(StudioError):write_context(self.project,self.session,"继续")

    def test_checkpoint_branch_preserves_isolation(self):
        commit_turn(self.session,"输入一","正文一")
        self.service.checkpoint(self.p,"main","bookmark",self.version(),1)
        self.service.branch(self.p,"main","bookmark","fork",self.version())
        commit_turn(self.session,"源分支秘密","源分支秘密正文")
        other=self.service.snapshot(self.p,"fork")
        self.assertEqual(1,other["current_turn"])
        self.assertNotIn("源分支秘密",json.dumps(other,ensure_ascii=False))

    def test_http_origin_token_and_snapshot(self):
        sys.path.insert(0,str(ROOT/"workbench"))
        from server import DinerServer
        server=DinerServer(("127.0.0.1",0),self.root)
        server.service=self.service
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with urllib.request.urlopen(server.origin+'/api/bootstrap') as response:
            bootstrap=json.load(response)
        self.assertIn("token",bootstrap)
        for headers in ({"Origin":"https://example.com"},{"Host":"evil.example"}):
            request=urllib.request.Request(server.origin+'/api/projects',headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request)
            self.assertEqual(403,caught.exception.code)
            caught.exception.close()
        request=urllib.request.Request(server.origin+'/api/configure',data=b'{}',headers={"Content-Type":"application/json"})
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request)
        self.assertEqual(403,caught.exception.code)
        caught.exception.close()
        request=urllib.request.Request(server.origin+'/api/commit',data=b'{}',headers={"Content-Type":"application/json","X-Studio-Token":bootstrap['token']})
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request)
        self.assertEqual(404,caught.exception.code)
        caught.exception.close()
        from urllib.parse import urlencode
        target = {"project": self.p, "session": "main"}
        prepared = self.prepare()
        query = urlencode({**target, "operation_id": prepared["operation_id"], "action": "archive"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(server.origin + '/api/operation_view?' + query)
        self.assertEqual(400, caught.exception.code)
        caught.exception.close()
        self.assertTrue(self.service.pending(self.session))
        payload = {**target, "action": "save", "expected_version": 0,
                   "items": [{"id":"r1", "type":"guidance", "text":"保持克制。"}]}
        headers = {"Content-Type":"application/json", "X-Studio-Token":bootstrap['token']}
        request = urllib.request.Request(server.origin+'/api/requirements', data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request) as response:
            self.assertEqual(1, json.load(response)['version'])
        payload = {**target, "operation_id":prepared['operation_id'], "action":"archive",
                   "expected_version":self.version(), "reason":"工作台重新开始"}
        request = urllib.request.Request(server.origin+'/api/operation_edit', data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request) as response:
            archived = json.load(response)
        self.assertEqual('archived', archived['status'])
        self.assertFalse(self.service.pending(self.session))

    def test_blocked_preparation_can_retry_after_reviewed_memory(self):
        for i in range(7):commit_turn(self.session,f"输入{i}",f"米拉核对了第{i}封信。")
        cfg=load_yaml(self.session/'会话配置.yaml');cfg['memory_policy']['max_uncompressed_turns']=6;write_yaml(self.session/'会话配置.yaml',cfg)
        request=self.service.request(self.p,'main',self.version(),'继续核对信封。')
        prepared=self.service.prepare(self.p,'main',request_id=request['id'])
        self.assertEqual('needs_compaction',prepared['status'])
        candidate=self.service.memory(self.p,'main',through_turn=2)
        self.service.memory(self.p,'main',action='apply',text='# 压缩记忆\n\n米拉核对了前两封信。',through_turn=2,expected_event_id=candidate['source_event_id'])
        resumed=self.service.prepare(self.p,'main',request_id=request['id'])
        self.assertEqual('ready',resumed['status'])

    def test_ready_cross_entry_protection_and_explicit_archive(self):
        from studio_context import write_context
        data = self.prepare()
        before = (self.session / '.runtime/current/transaction.json').read_bytes()
        with self.assertRaisesRegex(StudioError, '归档'):
            write_context(self.project, self.session, '改走另一条路。')
        self.assertEqual(before, (self.session / '.runtime/current/transaction.json').read_bytes())
        operation = self.service.operation(self.p, 'main')
        self.assertEqual(data['context'], operation['context'])
        (self.session / '.runtime/current/response.md').write_text('保留的草稿', encoding='utf-8')
        archived = self.service.operation(self.p, 'main', data['operation_id'], 'archive', self.version(), '用户改变方向')
        self.assertEqual('保留的草稿', (Path(archived['path']) / 'response.md').read_text(encoding='utf-8'))
        self.assertTrue((Path(archived['path']) / 'context-packet.md').exists())
        self.assertEqual('archived', self.service.operation(self.p, 'main', data['operation_id'])['status'])
        self.assertEqual('ready', self.prepare('改走另一条路。')['status'])

    def test_direct_prepare_retry_preserves_operation_and_rejects_different_request(self):
        for regenerate in (False, True):
            if regenerate:
                active = self.service.operation(self.p, 'main')
                self.service.commit(self.p, 'main', active['operation_id'], '米拉放下怀表。')
            version = self.version()
            first = self.service.prepare(self.p, 'main', '看看怀表。', version, regenerate=regenerate)
            second = self.service.prepare(self.p, 'main', '看看怀表。', version, regenerate=regenerate)
            self.assertEqual(first, second)
            with self.assertRaises(Conflict):
                self.service.prepare(self.p, 'main', '换一个输入', self.version(), regenerate=regenerate)

    def test_blocked_configuration_recovery_and_ticket_receipt(self):
        cfg = load_yaml(self.session / '会话配置.yaml')
        cfg['context_budget_chars'] = 200
        write_yaml(self.session / '会话配置.yaml', cfg)
        request = self.service.request(self.p, 'main', self.version(), '核对钟面。')
        prepared = self.service.prepare(self.p, 'main', request_id=request['id'])
        self.assertEqual('budget_blocked', prepared['status'])
        self.assertIn('section_chars', prepared['report'])
        self.assertTrue(self.service.snapshot(self.p, 'main')['can_configure'])
        self.service.configure(self.p, 'main', self.version(), mode='quality')
        prepared = self.service.prepare(self.p, 'main', request_id=request['id'])
        self.assertEqual('ready', prepared['status'])
        self.assertEqual('ready', self.service.request_status(self.p, 'main', request['id'])['status'])
        self.service.commit(self.p, 'main', prepared['operation_id'], '钟面没有改变。', motifs=['clock', 'clock', ' clock '])
        receipt = self.service.commit(self.p, 'main', prepared['operation_id'], '钟面没有改变。', motifs=['clock', 'clock', ' clock '])
        self.assertTrue(receipt['replayed'])
        self.assertEqual('committed', self.service.request_status(self.p, 'main', request['id'])['status'])
        self.assertEqual(1, reduce_events(read_events(self.session))['current_turn'])

    def test_checkpoint_core_rejects_duplicate_and_catalog_tracks_new_session(self):
        from studio_core import create_checkpoint, branch_session
        commit_turn(self.session, '输入一', '正文一')
        checkpoint = create_checkpoint(self.session, 'stable', 1)
        original = checkpoint.read_bytes()
        commit_turn(self.session, '输入二', '正文二')
        count = len(read_events(self.session))
        with self.assertRaises(StudioError):
            create_checkpoint(self.session, 'stable', 2)
        self.assertEqual(original, checkpoint.read_bytes())
        self.assertEqual(count, len(read_events(self.session)))
        before = self.service.catalog_version(self.project)
        fork = branch_session(self.project, 'main', 'stable', 'fork')
        self.assertEqual(1, reduce_events(read_events(fork))['current_turn'])
        with patch('studio_workbench.read_events', side_effect=AssertionError('metadata only')):
            self.assertNotEqual(before, self.service.catalog_version(self.project))


@__import__('unittest').skipUnless(importlib.util.find_spec('mcp'), 'optional MCP SDK is not installed')
class MCPProtocolTests(__import__('unittest').TestCase):
    def test_stdio_initialize_list_and_call(self):
        import asyncio
        import tempfile
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def check(root):
            params=StdioServerParameters(command=sys.executable,args=['-B',str(ROOT/'workbench/mcp_server.py'),'--workspace',str(root)])
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    tools=await session.list_tools()
                    self.assertIn('studio_prepare',[t.name for t in tools.tools])
                    response=await session.call_tool('studio_projects',{})
                    self.assertFalse(response.isError)
                    guide=await session.read_resource('studio://guide')
                    self.assertTrue(guide.contents)
                    guide_text = guide.contents[0].text
                    self.assertIn('studio://skill', guide_text)
                    self.assertNotIn('## 不可破坏的分层', guide_text)
                    skill = await session.read_resource('studio://skill')
                    self.assertIn('## 不可破坏的分层', skill.contents[0].text)
                    self.assertIn('studio_operation', [t.name for t in tools.tools])
                    self.assertIn('studio_requirements', [t.name for t in tools.tools])
                    from diner_fixture import create_demo
                    project = create_demo(Path(root))
                    async def call(name, payload):
                        response = await session.call_tool(name, payload)
                        self.assertFalse(response.isError, response.content)
                        return json.loads(response.content[0].text)
                    target = {'project': project.name, 'session': '先别关灯'}
                    view = await call('studio_session', target)
                    reqs = await call('studio_requirements', target)
                    await call('studio_requirements', {**target, 'action': 'save', 'expected_version': reqs['version'],
                                                      'items': [{'id':'word1','type':'banned','text':'不容置疑'}]})
                    payload = {**target, 'user_text': '我走进餐馆。', 'expected_version': view['version']}
                    prepared = await call('studio_prepare', payload)
                    self.assertEqual(prepared, await call('studio_prepare', payload))
                    recovered = await call('studio_operation', target)
                    self.assertEqual(prepared['context'], recovered['context'])
                    checked = await call('studio_requirements', {**target, 'action':'check', 'operation_id':prepared['operation_id'], 'prose':'不容置疑。'})
                    self.assertEqual('不容置疑', checked['hits'][0]['text'])
                    commit = {**target, 'operation_id': prepared['operation_id'], 'prose': '六月递来菜单。', 'motifs': ['menu', 'menu']}
                    await call('studio_commit', commit)
                    self.assertTrue((await call('studio_commit', commit))['replayed'])
                    self.assertIn('studio_ticket', [t.name for t in tools.tools])
                    session_events = project / '会话/先别关灯/events.jsonl'
                    original_events = session_events.read_bytes()
                    topic = (await call('studio_bible_edit', {'project': project.name, 'action': 'create',
                        'payload': {'title': '餐馆里的一个谜团', 'module': '故事发动机.md', 'text': '六月为什么留下旧菜单？'}}))['topic']
                    ticket = await call('studio_bible_edit', {'project': project.name, 'action': 'request',
                        'payload': {'topic_id': topic['id'], 'expected_version': topic['version'], 'text': '先比较几个方向。'}})
                    prepared = await call('studio_ticket', {'ticket_path': ticket['ticket_path']})
                    self.assertEqual('bible', prepared['kind'])
                    self.assertEqual('ready', prepared['status'])
                    result = await call('studio_bible_commit', {'project': project.name, 'payload': {
                        'operation_id': prepared['operation_id'], 'status': 'open', 'decision': '菜单关联了一位熟客。',
                        'assistant_text': '一份旧菜单可以是一段日常的纪念。',
                        'next_prompt': {'question': '那位熟客是谁？', 'options': {'1': '老邻居', '2': '旧同事'}}}})
                    self.assertEqual('committed', result['status'])
                    viewed = await call('studio_bible_view', {'project': project.name, 'topic_id': topic['id'],
                        'event_id': prepared['operation_id']})
                    self.assertEqual('先比较几个方向。', viewed['event']['data']['transcript']['user'])
                    self.assertEqual(original_events, session_events.read_bytes())
        with tempfile.TemporaryDirectory() as root:asyncio.run(check(Path(root)))
