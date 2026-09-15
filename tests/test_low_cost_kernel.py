import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from support import StudioCase, ROOT
from studio_core import (StudioError, create_session, commit_turn, apply_memory, prepare_memory,
    read_events, reduce_events, validate_session, add_variant, select_variant, create_checkpoint,
    branch_session, load_yaml, write_yaml, project_events_through_turn)
from studio_context import build_context
from studio_delivery import deliver, split_packet, PART_BYTES
from studio_session_recall import recall
from studio_workbench import StudioService
from studio_retrieval import PREFLIGHT_GATE


class LowCostKernel(StudioCase):
    def new(self, name='main', mode='economy'):
        return create_session(self.project, name, mode=mode)

    def test_delivery_exact_utf8_bounded_immutable(self):
        s = self.new()
        text = '# 开场\n' + '汉字🙂\n' * 11000 + '\n## 结尾\n正文  \n'
        result = {'status': 'ready', 'operation_id': 'op1', 'context': text, 'report': {'large': text}}
        r = deliver(s, result)
        self.assertNotIn('context', r)
        self.assertNotIn('report', r)
        bodies = [Path(p['path']).read_bytes() for p in r['context_parts']]
        self.assertEqual(text.encode(), b''.join(bodies))
        self.assertTrue(all(len(b) <= PART_BYTES for b in bodies))
        for part, body in zip(r['context_parts'], bodies):
            self.assertEqual(hashlib.sha256(body).hexdigest(), part['sha256'])
        self.assertEqual(r, deliver(s, result))
        self.assertIs(result, deliver(s, result, 'inline'))
        with self.assertRaises(StudioError):
            deliver(s, {**result, 'context':'unexpected replacement'})
        pending = deliver(s, {'status':'pending','operation_id':'op1','payload':{'assistant_text':'原提交' * 9000}})
        self.assertNotIn('payload', pending)
        self.assertIn('resume', pending['next'])
        Path(r['context_parts'][0]['path']).write_text('damage', encoding='utf-8')
        with self.assertRaises(StudioError):
            deliver(s, result)

    def test_prepare_and_regeneration_reference_their_own_context(self):
        s = self.new()
        service = StudioService(self.root, self.root)
        commit_turn(s, '收信', '被替换回合独有的标记')
        r = service.prepare(self.project.name, s.name, '重新写', service.version(self.project, s), regenerate=True, context_delivery='reference')
        text = ''.join(Path(p['path']).read_text(encoding='utf-8') for p in r['context_parts'])
        self.assertNotIn('被替换回合独有的标记', text)
        self.assertTrue(r['regenerate'])
        self.assertIn(r['operation_id'], r['context_parts'][0]['path'])

    def test_summary_omission_can_recall_original_evidence(self):
        s = self.new()
        commit_turn(s, '寄存', '紫铜匣藏在北塔第七级台阶下。')
        for n in range(9):
            commit_turn(s, '继续', '钟摆运行。')
        apply_memory(s, '已经校时。', 7)
        packet, report = build_context(self.project, s, '紫铜匣在哪里？')
        self.assertIn('北塔第七级台阶', packet)
        self.assertLessEqual(report['history_recall']['chars'], 3000)
        self.assertEqual('not_requested', build_context(self.project, s, '继续')[1]['history_recall']['status'])

    def test_selected_variant_branch_and_projected_boundary(self):
        s = self.new()
        commit_turn(s, '琥珀罗盘在哪', '琥珀罗盘在北塔。')
        create_checkpoint(s, 'first')
        v = add_variant(s, 1, '琥珀罗盘在南塔。')
        select_variant(s, 1, v)
        found = recall(s, '琥珀罗盘')
        self.assertIn('南塔', str(found))
        self.assertNotIn('北塔', str(found))
        branch = branch_session(self.project, s.name, 'first', 'branch')
        self.assertIn('北塔', str(recall(branch, '琥珀罗盘')))
        self.assertNotIn('南塔', str(recall(branch, '琥珀罗盘')))
        blank = self.new('sibling')
        self.assertEqual('not_found', recall(blank, '琥珀罗盘')['status'])
        projected = project_events_through_turn(read_events(s), 0)
        self.assertEqual('not_found', recall(s, '琥珀罗盘', events=projected)['status'])

    def test_index_corruption_and_append_rebuild(self):
        s = self.new()
        commit_turn(s, '找琥珀罗盘', '琥珀罗盘在北塔。')
        recall(s, '琥珀罗盘')
        path = s / '.runtime/session-recall-index.json'
        path.write_text('{invalid', encoding='utf-8')
        self.assertEqual('found', recall(s, '琥珀罗盘')['status'])
        commit_turn(s, '移走琥珀罗盘', '琥珀罗盘已移到南塔，北塔不再存放。')
        r = recall(s, '琥珀罗盘')
        self.assertEqual(2, r['results'][-1]['through_turn'])
        self.assertIn('当时事实', r['results'][-1]['text'])

    def test_recall_once_per_ready_operation(self):
        s = self.new()
        service = StudioService(self.root, self.root)
        r = service.prepare(self.project.name, s.name, '继续', service.version(self.project, s))
        args = (self.project.name, s.name, '琥珀罗盘', r['operation_id'])
        one = service.recall(*args)
        self.assertEqual(one, service.recall(*args))
        with self.assertRaises(StudioError):
            service.recall(self.project.name, s.name, '蓝色信封', r['operation_id'])

    def test_ambiguous_alias_is_not_guessed(self):
        s = self.new()
        commit_turn(s, '米拉收到琥珀罗盘', '罗盘在北塔')
        aliases = {'Mira Chen':['米拉','Mira Chen'], 'Mira Li':['米拉','Mira Li']}
        r = recall(s, '米拉的琥珀罗盘在哪里', aliases=aliases)
        self.assertEqual('ambiguous', r['status'])
        self.assertEqual([], r['results'])

    def test_regeneration_request_counts_toward_budget(self):
        s = self.new()
        commit_turn(s, '开始', '校时')
        service = StudioService(self.root, self.root)
        r = service.prepare(self.project.name, s.name, '改写要求' * 12000,
                            service.version(self.project, s), regenerate=True)
        self.assertEqual('budget_blocked', r['status'])

    def test_previous_output_config_uses_numeric_turn(self):
        from studio_core import output_config_snapshot
        s = self.new()
        config = load_yaml(s / '会话配置.yaml')
        for n in range(1, 11):
            cfg = {**config, 'prose': {'min_chars':800,'max_chars':900}} if n == 9 else config
            commit_turn(s, '继续', '校时', output_config=output_config_snapshot(cfg))
        packet, _ = build_context(self.project, s, '继续')
        self.assertNotIn('；变更：', packet)

    def test_optional_recall_never_triggers_compaction(self):
        s = self.new()
        commit_turn(s, '找琥珀罗盘', '调查')
        apply_memory(s, '琥珀罗盘' * 8400, 1)
        commit_turn(s, '继续', '调查')
        apply_memory(s, '正在调查。', 2)
        for _ in range(6):
            commit_turn(s, '继续', '调查')
        _, report = build_context(self.project, s, '琥珀罗盘在哪里？')
        self.assertEqual('ready', report['status'])
        self.assertLessEqual(report['history_recall']['chars'], 3000)
        self.assertNotIn('hard_context_budget', report['compaction']['reasons'])

    def test_static_overflow_blocks_even_with_compressible_turns(self):
        s = self.new()
        for _ in range(7):
            commit_turn(s, '继续', '调查')
        apply_memory(s, '超长活动事实' * 10000, 1)
        _, report = build_context(self.project, s, '继续')
        self.assertEqual('budget_blocked', report['status'])
        self.assertEqual('fixed_or_active', report['budget_blocker'])

    def test_memory_idempotence_after_projection_failure(self):
        s = self.new()
        commit_turn(s, '收信', '收信完成。')
        c = prepare_memory(s, 1)
        self.assertEqual(c, prepare_memory(s, 1))
        kwargs = {'expected_event_id': c['source_event_id'], 'operation_id': c['operation_id']}
        with patch('studio_core.rebuild_views', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                apply_memory(s, '收到信件。', 1, **kwargs)
        receipt = apply_memory(s, '收到信件。', 1, **kwargs)
        self.assertEqual(receipt, apply_memory(s, '收到信件。', 1, **kwargs))
        self.assertEqual([], validate_session(s))
        self.assertEqual(1, sum(e['type'] == 'memory_compacted' for e in read_events(s)))
        with self.assertRaises(StudioError):
            apply_memory(s, '不同内容', 1, **kwargs)

    def test_memory_stage_sources_policy_and_stale(self):
        s = self.new()
        for _ in range(8):
            commit_turn(s, '继续', '校时')
        c = prepare_memory(s, mode='quality')
        self.assertEqual(3, c['through_turn'])
        self.assertEqual('quality', c['policy_snapshot']['mode'])
        with self.assertRaises(StudioError):
            apply_memory(s, '摘要', 3, operation_id=c['operation_id'], stage_summaries=[{'from_turn':1,'through_turn':4,'text':'越界'}])
        r = apply_memory(s, '等待回信', 3, {'当前局面.md':'等待回信'}, operation_id=c['operation_id'],
                         stage_summaries=[{'from_turn':1,'through_turn':3,'text':'琥珀罗盘曾在北塔。'}])
        self.assertEqual('applied', r['status'])
        self.assertIn('北塔', str(recall(s, '琥珀罗盘')))
        c = prepare_memory(s, 5)
        commit_turn(s, '继续', '新事件')
        with self.assertRaises(StudioError):
            apply_memory(s, '摘要', 5, operation_id=c['operation_id'])

    def test_light_rules_and_unchanged_presets(self):
        from studio_policy import preset
        self.assertNotIn('必须按 G0→G5', PREFLIGHT_GATE)
        self.assertIn('不调用计数工具', PREFLIGHT_GATE)
        self.assertEqual(80000, preset()['context_budget_chars'])
        self.assertEqual(10, preset('economy')['memory_policy']['max_uncompressed_turns'])

    def test_commit_format_error_is_actionable_and_leaves_no_draft(self):
        s = self.new()
        service = StudioService(self.root, self.root)
        r = service.prepare(self.project.name, s.name, '继续', service.version(self.project, s), context_delivery='reference')
        self.assertIn('当前局面.md', r['commit_contract']['state_updates']['keys'])
        self.assertIn('不含状态栏', r['commit_contract']['prose'])
        kwargs = dict(project=self.project.name, session=s.name, operation_id=r['operation_id'], prose='钟声响了。')
        with self.assertRaises(StudioError) as caught:
            service.commit(**kwargs, state_updates={'时间':'傍晚'})
        self.assertIn('scene_patch', str(caught.exception))
        self.assertIn('当前局面.md', str(caught.exception))
        self.assertNotIn('钟声响了', str(caught.exception))
        self.assertFalse((s / '.runtime/current/response.md').exists())
        self.assertEqual(0, reduce_events(read_events(s))['current_turn'])
        result = service.commit(**kwargs, scene_patch={'time':'傍晚'}, state_updates={'当前局面.md':'钟声响了。'})
        self.assertEqual('committed', result['status'])
        self.assertEqual(1, reduce_events(read_events(s))['current_turn'])

    def test_memory_prepare_inherits_only_current_operation_policy(self):
        s = self.new()
        for _ in range(8):
            commit_turn(s, '继续', '校时')
        source = read_events(s)[-1]['event_id']
        runtime = s / '.runtime/current'
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / 'transaction.json').write_text(json.dumps({
            'status':'needs_compaction', 'source_event_id':source,
            'request_payload':{'mode':'quality'}}), encoding='utf-8')
        c = prepare_memory(s)
        self.assertEqual('quality', c['policy_snapshot']['mode'])
        self.assertEqual(3, c['through_turn'])
        commit_turn(s, '继续', '新事件')
        c = prepare_memory(s)
        self.assertEqual('economy', c['policy_snapshot']['mode'])
        self.assertEqual(6, c['through_turn'])

    def test_regeneration_memory_excludes_replaced_turn_and_state(self):
        s = self.new()
        for _ in range(10):
            commit_turn(s, '继续', '校时')
        commit_turn(s, '继续', '被替换独有正文', state_updates={'当前局面.md':'被替换独有状态'})
        service = StudioService(self.root, self.root)
        r = service.prepare(self.project.name, s.name, '重新写', service.version(self.project, s), regenerate=True)
        self.assertEqual('needs_compaction', r['status'])
        c = prepare_memory(s)
        self.assertEqual(10, c['projection_through_turn'])
        self.assertEqual(7, c['through_turn'])
        self.assertEqual(read_events(s)[-1]['event_id'], c['source_event_id'])
        self.assertNotIn('被替换独有', json.dumps(c, ensure_ascii=False))

    def test_usage_analyzer_does_not_double_count_or_expose_content(self):
        spec = importlib.util.spec_from_file_location('usage_audit', ROOT / 'workbench/analyze_usage.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ts = {'input': 10, 'output': 4, 'reasoning': 3, 'cache': {'read': 90, 'write': 0}}
        d = {'info': {'tokens': ts, 'cost': .1}, 'messages': [{'info': {'role': 'assistant','tokens':ts,'cost':.1},
             'parts': [{'type':'step-finish','tokens':ts}, {'type':'reasoning','text':'PRIVATE'},
                       {'type':'tool','tool':'read','state':{'input':{'path':'SECRET'},'output':'PRIVATE'}}]}]}
        r = module.analyze(d)
        self.assertEqual(107, r['total_tokens'])
        self.assertTrue(r['session_tokens_match'])
        self.assertNotIn('PRIVATE', json.dumps(r))
        self.assertNotIn('SECRET', json.dumps(r))
