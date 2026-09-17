from unittest.mock import patch

from support import StudioCase
from studio_core import StudioError
from studio_workbench import StudioService
from studio_construction_history import read_state


class ConstructionChatTests(StudioCase):
    def setUp(self):
        super().setUp()
        self.service = StudioService(self.root, self.root)
        self.p = self.project.name
        self.topic = self.service.bible_edit(self.p, 'create', title='外貌', module='角色/Mira Chen.md')['topic']

    def prepare(self, text='采用蓝色外套', **kwargs):
        return self.service.bible_prepare(self.p, self.topic['id'], text, **kwargs)

    def payload(self, request, **kwargs):
        return {'operation_id': request['operation_id'], 'status': 'open', 'decision': '采用蓝色外套',
                'assistant_text': '已采用蓝色外套。', **kwargs}

    def test_close_and_transfer_question_survives_restart_and_projection_rebuild(self):
        request = self.prepare()
        payload = self.payload(request, status='complete', edits={self.topic['module']: '# 人物\n蓝色外套。'},
            next_topic={'title': '职业', 'module': '世界设定.md'},
            next_prompt={'question': '选择什么职业？', 'options': {'1': '修表师', '2': '自由撰稿人'}})
        receipt = self.service.bible_commit(self.p, payload)
        self.assertNotEqual(self.topic['id'], receipt['next_topic_id'])
        (self.project / '构筑/decisions.json').unlink()
        fresh = StudioService(self.root, self.root)
        handoff = fresh.bible_continue(self.p)
        self.assertEqual('职业', handoff['title'])
        self.assertEqual('自由撰稿人', handoff['prompt']['options']['2'])
        request = fresh.bible_prepare(self.p, handoff['topic_id'], '2', prompt_id=handoff['prompt']['id'])
        fresh.bible_commit(self.p, self.payload(request, choice='2', decision=''))
        self.assertEqual('自由撰稿人', fresh.bible_continue(self.p)['decision'])
        self.assertEqual('complete', fresh.bible_view(self.p, self.topic['id'])['topic']['status'])

    def test_interrupted_handoff_reuses_exact_target(self):
        request = self.prepare()
        payload = self.payload(request, edits={self.topic['module']: '# 人物\n蓝色外套。'},
            next_topic={'title': '职业', 'module': '世界设定.md'},
            next_prompt={'question': '职业？', 'options': {}})
        with patch('studio_construction.atomic_write_text', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.service.bible_commit(self.p, payload)
        self.assertEqual('recovery_required', self.service.bible_continue(self.p)['status'])
        receipt = self.service.bible_operation(self.p, 'resume', request['operation_id'])
        self.assertEqual(receipt, self.service.bible_commit(self.p, payload))
        self.assertEqual(1, len([t for t in read_state(self.project)['topics'].values() if t['title'] == '职业']))

    def test_existing_next_topic_requires_fresh_version_and_preserves_unanswered_prompt(self):
        target = self.service.bible_edit(self.p, 'create', title='职业', module='世界设定.md')['topic']
        request = self.prepare()
        payload = self.payload(request, next_topic={'topic_id': target['id'], 'expected_version': 0},
                               next_prompt={'question': '职业？', 'options': {}})
        with self.assertRaises(StudioError):
            self.service.bible_commit(self.p, payload)
        payload['next_topic']['expected_version'] = 1
        self.service.bible_commit(self.p, payload)
        request = self.prepare()
        with self.assertRaises(StudioError):
            self.service.bible_commit(self.p, self.payload(request,
                next_topic={'topic_id': target['id'], 'expected_version': 2},
                next_prompt={'question': '不能覆盖', 'options': {}}))

    def test_continue_does_not_guess_among_topics_or_load_history(self):
        with patch('studio_bible_workbench.current_prompt', side_effect=AssertionError('no topic selected')):
            receipt = self.service.bible_continue(self.p)
        self.assertEqual('needs_topic', receipt['status'])
        self.assertLessEqual(len(receipt['candidates']), 12)
        request = self.prepare()
        self.service.bible_commit(self.p, self.payload(request))
        self.assertEqual(self.topic['id'], self.service.bible_continue(self.p)['topic_id'])

    def test_required_materials_block_before_creating_transaction(self):
        result = self.prepare(required_related=['不存在.md'])
        self.assertEqual('materials_blocked', result['status'])
        self.assertFalse((self.project / '.runtime/bible/prepare.json').exists())
        (self.project / 'StoryBible/世界设定.md').write_text('世界压力' * 3000, encoding='utf-8')
        result = self.prepare(required_related=['世界设定.md'], budget=2000)
        self.assertEqual('budget_blocked', result['status'])
        self.assertFalse((self.project / '.runtime/bible/prepare.json').exists())
        result = self.prepare(required_related=['世界设定.md'], budget=20000)
        self.assertIn('世界设定.md', result['context']['materials'])

    def test_direct_revision_is_ticket_free_idempotent_and_keeps_original(self):
        r = self.prepare()
        self.service.bible_commit(self.p, self.payload(r, status='complete', edits={self.topic['module']: '# 人物\n蓝色外套。'}))
        old = self.service.bible_view(self.p, self.topic['id'])['topic']
        r = self.service.bible_revise_prepare(self.p, old['id'], '改成红色', old['version'])
        self.assertEqual(r, self.service.bible_revise_prepare(self.p, old['id'], '改成红色', old['version']))
        self.assertFalse((self.project / '.workbench/bible/requests').exists())
        self.assertEqual('complete', self.service.bible_view(self.p, old['id'])['topic']['status'])
        done = self.service.bible_commit(self.p, self.payload(r, status='complete', decision='改成红色',
                                                edits={self.topic['module']: '# 人物\n红色外套。'}))
        self.assertEqual(done, self.service.bible_revise_prepare(self.p, old['id'], '改成红色', old['version']))
        self.assertEqual('superseded', self.service.bible_view(self.p, old['id'])['topic']['status'])

    def test_fact_application_tracks_pending_replacement_and_requires_review_on_rewrite(self):
        r = self.prepare()
        self.service.bible_commit(self.p, self.payload(r, applied_facts=[],
            accepted_facts=[{'id': 'coat', 'action': 'add', 'text': '蓝色外套'}]))
        self.assertEqual(['coat'], self.service.bible_continue(self.p)['pending_fact_ids'])
        r = self.prepare()
        payload = self.payload(r, status='complete', edits={self.topic['module']: '# 人物\n蓝色外套。'})
        with self.assertRaises(StudioError):
            self.service.bible_commit(self.p, payload)
        payload.update(status='open', applied_facts=['coat'])
        self.service.bible_commit(self.p, payload)
        r = self.prepare()
        self.service.bible_commit(self.p, self.payload(r,
            accepted_facts=[{'id': 'coat', 'action': 'replace', 'text': '红色外套'}]))
        self.assertEqual(['coat'], self.service.bible_continue(self.p)['pending_fact_ids'])
        r = self.prepare()
        self.service.bible_commit(self.p, self.payload(r, status='complete', applied_facts=['coat'],
            edits={self.topic['module']: '# 人物\n红色外套。'}))
        fact = self.service.bible_view(self.p, self.topic['id'])['topic']['accepted_facts'][0]
        self.assertEqual('applied', fact['application'])
        self.assertEqual(r['operation_id'], fact['applied_event_id'])

    def test_cli_transport_exposes_new_operations_with_reference_delivery(self):
        r = self.prepare()
        self.service.bible_commit(self.p, self.payload(r))
        result = self.service.dispatch_transport('bible_continue', {'project': self.p})
        self.assertEqual(self.topic['id'], result['topic_id'])
        result = self.service.dispatch_transport('bible_revise_prepare', {'project': self.p,
            'topic_id': self.topic['id'], 'expected_version': result['topic_version'], 'user_text': '改成红色'})
        self.assertEqual('ready', result['status'])
        self.assertIn('context_parts', result)

    def test_revision_handoff_keeps_dependency_review_and_next_question(self):
        r = self.prepare()
        self.service.bible_commit(self.p, self.payload(r, status='complete', edits={self.topic['module']: '# 人物\n蓝色外套。'}))
        dependent = self.service.bible_edit(self.p, 'create', title='职业', module='世界设定.md')['topic']
        r = self.service.bible_prepare(self.p, dependent['id'], '依赖外貌')
        self.service.bible_commit(self.p, self.payload(r, depends_on=[self.topic['id']]))
        r = self.service.bible_revise_prepare(self.p, self.topic['id'], '红色外套', 2)
        done = self.service.bible_commit(self.p, self.payload(r, status='complete',
            edits={self.topic['module']: '# 人物\n红色外套。'},
            next_topic={'topic_id': dependent['id'], 'expected_version': 2},
            next_prompt={'question': '职业是否受影响？', 'options': {'1': '调整'}}))
        next_step = self.service.bible_continue(self.p)
        self.assertEqual(done['prompt_id'], next_step['prompt']['id'])
        self.assertEqual([self.topic['id']], next_step['needs_review'])

    def test_direct_revision_pending_retry_recovers_original_payload(self):
        r = self.service.bible_revise_prepare(self.p, self.topic['id'], '红色外套', 1)
        payload = self.payload(r, edits={self.topic['module']: '# 人物\n红色外套。'})
        with patch('studio_construction.atomic_write_text', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.service.bible_commit(self.p, payload)
        retry = self.service.bible_revise_prepare(self.p, self.topic['id'], '红色外套', 1)
        self.assertEqual('pending', retry['status'])
        self.assertEqual(payload, retry['payload'])

    def test_required_ticket_materials_and_invalid_delivery_do_not_prepare(self):
        ticket = self.service.bible_edit(self.p, 'request', topic_id=self.topic['id'], expected_version=1,
                                        text='需要历史', required_related=['不存在.md'])
        self.assertEqual('materials_blocked', self.service.ticket(ticket['ticket_path'])['status'])
        before = read_state(self.project)
        with self.assertRaises(StudioError):
            self.service.dispatch_transport('bible_revise_prepare', {'project': self.p, 'topic_id': self.topic['id'],
                'expected_version': 1, 'user_text': '改红色', 'context_delivery': 'bad'})
        self.assertEqual(before, read_state(self.project))

    def test_pending_facts_block_freeze_and_revoke_can_be_applied(self):
        from studio_bible import finalize_bible
        r = self.prepare()
        self.service.bible_commit(self.p, self.payload(r, applied_facts=[],
            accepted_facts=[{'id': 'coat', 'action': 'add', 'text': '蓝色外套'}]))
        with self.assertRaisesRegex(StudioError, '事实落实'):
            finalize_bible(self.project, apply=True)
        r = self.prepare()
        self.service.bible_commit(self.p, self.payload(r, status='complete', applied_facts=['coat'],
            accepted_facts=[{'id': 'coat', 'action': 'revoke'}], edits={self.topic['module']: '# 人物\n修表师。'}))
        self.assertEqual([], self.service.bible_continue(self.p)['pending_fact_ids'])

    def test_revision_handoff_keeps_dependency_review_and_next_question(self):
        r = self.prepare()
        self.service.bible_commit(self.p, self.payload(r, status='complete', edits={self.topic['module']: '# 人物\n蓝色外套。'}))
        dependent = self.service.bible_edit(self.p, 'create', title='职业', module='世界设定.md')['topic']
        r = self.service.bible_prepare(self.p, dependent['id'], '依赖外貌')
        self.service.bible_commit(self.p, self.payload(r, depends_on=[self.topic['id']]))
        r = self.service.bible_revise_prepare(self.p, self.topic['id'], '红色外套', 2)
        done = self.service.bible_commit(self.p, self.payload(r, status='complete',
            edits={self.topic['module']: '# 人物\n红色外套。'},
            next_topic={'topic_id': dependent['id'], 'expected_version': 2},
            next_prompt={'question': '职业是否受影响？', 'options': {'1': '调整'}}))
        next_step = self.service.bible_continue(self.p)
        self.assertEqual(done['prompt_id'], next_step['prompt']['id'])
        self.assertEqual([self.topic['id']], next_step['needs_review'])

    def test_direct_revision_pending_retry_recovers_original_payload(self):
        r = self.service.bible_revise_prepare(self.p, self.topic['id'], '红色外套', 1)
        payload = self.payload(r, edits={self.topic['module']: '# 人物\n红色外套。'})
        with patch('studio_construction.atomic_write_text', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.service.bible_commit(self.p, payload)
        retry = self.service.bible_revise_prepare(self.p, self.topic['id'], '红色外套', 1)
        self.assertEqual('pending', retry['status'])
        self.assertEqual(payload, retry['payload'])

    def test_required_ticket_materials_and_invalid_delivery_do_not_prepare(self):
        ticket = self.service.bible_edit(self.p, 'request', topic_id=self.topic['id'], expected_version=1,
                                        text='需要历史', required_related=['不存在.md'])
        self.assertEqual('materials_blocked', self.service.ticket(ticket['ticket_path'])['status'])
        before = read_state(self.project)
        with self.assertRaises(StudioError):
            self.service.dispatch_transport('bible_revise_prepare', {'project': self.p, 'topic_id': self.topic['id'],
                'expected_version': 1, 'user_text': '改红色', 'context_delivery': 'bad'})
        self.assertEqual(before, read_state(self.project))

    def test_pending_facts_block_freeze_and_revoke_can_be_applied(self):
        from studio_bible import finalize_bible
        r = self.prepare()
        self.service.bible_commit(self.p, self.payload(r, applied_facts=[],
            accepted_facts=[{'id': 'coat', 'action': 'add', 'text': '蓝色外套'}]))
        with self.assertRaisesRegex(StudioError, '事实落实'):
            finalize_bible(self.project, apply=True)
        r = self.prepare()
        self.service.bible_commit(self.p, self.payload(r, status='complete', applied_facts=['coat'],
            accepted_facts=[{'id': 'coat', 'action': 'revoke'}], edits={self.topic['module']: '# 人物\n修表师。'}))
        self.assertEqual([], self.service.bible_continue(self.p)['pending_fact_ids'])
