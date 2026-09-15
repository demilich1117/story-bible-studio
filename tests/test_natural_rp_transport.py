import argparse
import json
from pathlib import Path
from unittest.mock import patch

from support import StudioCase, ROOT
from studio_core import create_session, commit_turn, StudioError
from studio_workbench import StudioService
from story_studio import run


class NaturalRPTransport(StudioCase):
    def setUp(self):
        super().setUp()
        self.session = create_session(self.project, 'natural', mode='economy')
        self.service = StudioService(self.root, self.root)
        self.target = {'project': self.project.name, 'session': self.session.name}

    def cli(self, operation, payload):
        path = self.root / 'args.json'
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        args = argparse.Namespace(group='workbench', operation=operation,
                                  workspace=str(self.root), payload_file=str(path))
        with patch('studio_workbench.StudioService', return_value=self.service):
            return run(args)

    def test_natural_cli_prepare_recovery_commit_and_inline(self):
        version = self.cli('status', self.target)['version']
        params = {**self.target, 'user_text':'继续校时', 'expected_version':version}
        r = self.cli('prepare', params)
        self.assertEqual('reference', r['context_delivery'])
        self.assertNotIn('context', r)
        self.assertIn('commit_contract', r)
        body = ''.join(Path(p['path']).read_text(encoding='utf-8') for p in r['context_parts'])
        self.assertIn('继续校时', body)
        recovered = self.cli('operation', {**self.target, 'operation_id':r['operation_id']})
        self.assertEqual(r['operation_id'], recovered['operation_id'])
        self.assertNotIn('context', recovered)
        inline = self.cli('prepare', {**params, 'context_delivery':'inline'})
        self.assertEqual(body, inline['context'])
        self.assertIn('context', self.service.prepare(**params))
        receipt = self.cli('commit', {**self.target, 'operation_id':r['operation_id'], 'prose':'钟声响了。'})
        self.assertEqual('committed', receipt['status'])

    def test_memory_cli_reference_and_apply(self):
        for _ in range(5):
            commit_turn(self.session, '继续', '检查钟摆。')
        r = self.cli('memory', {**self.target, 'through_turn':2, 'mode':'quality'})
        self.assertEqual('prepared', r['status'])
        candidate = json.loads(''.join(Path(p['path']).read_text(encoding='utf-8') for p in r['context_parts']))
        self.assertEqual('quality', candidate['policy_snapshot']['mode'])
        self.assertEqual(2, len(candidate['turns']))
        receipt = self.cli('memory', {**self.target, 'action':'apply', 'operation_id':r['operation_id'],
                                    'through_turn':2, 'text':'完成钟摆检查。'})
        self.assertEqual('applied', receipt['status'])
        self.assertNotIn('context_parts', receipt)

    def test_invalid_delivery_has_no_prepare_side_effects(self):
        params = {**self.target, 'user_text':'继续', 'expected_version':self.service.status(**self.target)['version'],
                  'context_delivery':'invalid'}
        with self.assertRaises(StudioError):
            self.cli('prepare', params)
        self.assertFalse((self.session / '.runtime/current/transaction.json').exists())
        with self.assertRaises(StudioError):
            self.cli('memory', {**self.target, 'context_delivery':'invalid'})
        self.assertFalse((self.session / '.runtime/memory-candidate.json').exists())

    def test_entry_guides_route_natural_rp_to_structured_interface(self):
        guide = (ROOT / '.agents/skills/story-bible-studio/references/roleplay-v3.md').read_text(encoding='utf-8')
        self.assertIn('studio_status', guide)
        self.assertIn('context_parts', guide)
        self.assertIn('不作为桌面自然语言 RP 的默认入口', guide)
        self.assertNotIn('将用户原文保存到本会话 `.runtime/current/user.md`', guide)
