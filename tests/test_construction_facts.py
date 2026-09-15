from support import StudioCase
from studio_core import StudioError
from studio_workbench import StudioService


class ConstructionFacts(StudioCase):
    def setUp(self):
        super().setUp()
        self.service = StudioService(self.root, self.root)
        self.p = self.project.name
        self.topic = self.service.bible_edit(self.p, 'create', title='人物细节', module='角色/Mira Chen.md', text='比较方向')['topic']

    def prepare(self, **kwargs):
        return self.service.bible_prepare(self.p, self.topic['id'], '继续讨论', **kwargs)

    def commit(self, r, **kwargs):
        return self.service.bible_commit(self.p, {'operation_id':r['operation_id'], 'decision':'本轮取舍',
            'assistant_text':'本轮公开讨论。', 'status':'open', **kwargs})

    def test_incremental_facts_survive_and_can_replace_revoke(self):
        r = self.prepare()
        self.commit(r, accepted_facts=[{'id':'silver-ring','action':'add','text':'祖母的银戒'}])
        r = self.prepare()
        self.commit(r, accepted_facts=[{'id':'workday','action':'add','text':'每周二修钟'}])
        r = self.prepare()
        facts = r['context']['topic']['accepted_facts']
        self.assertEqual(2, len(facts))
        self.assertEqual('祖母的银戒', facts[0]['text'])
        self.commit(r, accepted_facts=[{'id':'silver-ring','action':'replace','text':'母亲的银戒'},
                                      {'id':'workday','action':'revoke'}])
        facts = self.prepare()['context']['topic']['accepted_facts']
        self.assertEqual('母亲的银戒', facts[0]['text'])
        self.assertEqual('revoked', facts[1]['status'])
        self.assertEqual(r['operation_id'], facts[0]['source_event_id'])

    def test_proposal_cannot_confirm_facts_and_invalid_patch_is_atomic(self):
        r = self.prepare()
        with self.assertRaises(StudioError):
            self.commit(r, status='proposal', accepted_facts=[{'id':'ring','action':'add','text':'银戒'}])
        with self.assertRaises(StudioError):
            self.commit(r, accepted_facts=[{'id':'missing','action':'replace','text':'银戒'}])
        self.assertNotIn('accepted_facts', self.service.bible_view(self.p, self.topic['id'])['topic'])
        self.commit(r)

    def test_more_than_two_explicit_dependencies_are_loaded_and_guarded(self):
        names = ['世界设定.md','基础关系.md','基础时间线.md']
        r = self.prepare(related=names)
        self.assertTrue(all(n in r['context']['materials'] for n in names))
        self.commit(r, edits={'基础时间线.md':'# 时间线\n\n周二。'})

    def test_missing_dependency_is_reported_and_cannot_be_edited(self):
        r = self.prepare(related=['不存在.md'])
        self.assertIn('不存在.md', r['omitted'])
        with self.assertRaises(StudioError):
            self.commit(r, edits={'不存在.md':'# 错误\n\n未加载'})

    def test_revision_inherits_fact_baseline_and_old_callers_remain_valid(self):
        r = self.prepare()
        self.commit(r, accepted_facts=[{'id':'ring','action':'add','text':'银戒'}], status='complete',
                    edits={self.topic['module']:'# Mira Chen\n\n银戒。'})
        topic = self.service.bible_view(self.p, self.topic['id'])['topic']
        ticket = self.service.bible_edit(self.p, 'request', topic_id=topic['id'], expected_version=topic['version'],
                                         kind='revise', text='改为铜戒')
        revised = self.service.ticket(ticket['ticket_path'], context_delivery='inline')
        self.assertEqual('银戒', revised['context']['topic']['accepted_facts'][0]['text'])
        self.commit(revised, accepted_facts=[{'id':'ring','action':'replace','text':'铜戒'}])
