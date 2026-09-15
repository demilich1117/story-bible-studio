"""Exercise the stdio model handshake and cleanup with a real fake subprocess."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import ROOT
from studio_core import StudioError

sys.path.insert(0, str(ROOT / "workbench"))
from codex_models import discover_models


SERVER = '''
import json, sys, time
mode = sys.argv[1]
initialized = False
def emit(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    method = msg['method']
    if method == 'initialize':
        emit({'id': msg['id'], 'result': {}})
    elif method == 'initialized':
        initialized = True
    elif method == 'model/list':
        assert initialized
        if mode == 'timeout':
            time.sleep(10)
        if mode == 'exit':
            sys.exit(1)
        if mode == 'error':
            emit({'id': msg['id'], 'error': {'message': 'credential-must-not-leak'}})
            continue
        emit({'method': 'notification', 'params': {}})
        print('diagnostic ignored', flush=True)
        second = msg['params'].get('cursor') == 'page2'
        row = {'id': 'picker-id', 'model': 'actual-model', 'displayName': 'Example',
               'supportedReasoningEfforts': [{'reasoningEffort':'low'}, {'reasoningEffort':'high'}],
               'defaultReasoningEffort': 'low', 'isDefault': True, 'secret': 'must-not-leak'}
        data = [row, dict(row, model='hidden-model', hidden=True)]
        if second:
            data += [dict(row, model='second-model')]
        emit({'id': msg['id'], 'result': {'data': data, 'nextCursor': None if second and mode != 'loop' else 'page2'}})
    else:
        raise AssertionError('must not create tasks or turns')
'''


class CodexModelTests(unittest.TestCase):
    def discover(self, mode='ok', timeout=3):
        real_popen = subprocess.Popen
        processes = []
        def launch(args, **kwargs):
            self.assertEqual(['codex.exe', 'app-server'], args)
            proc = real_popen([sys.executable, '-u', '-c', SERVER, mode], **kwargs)
            processes.append(proc)
            return proc
        with tempfile.TemporaryDirectory() as folder, patch('codex_models.subprocess.Popen', side_effect=launch):
            try:
                return discover_models('codex.exe', Path(folder), timeout=timeout)
            finally:
                for proc in processes:
                    self.assertIsNotNone(proc.poll(), 'query process leaked')
                    self.assertTrue(proc.stdout.closed)
                    self.assertTrue(proc.stdin.closed)

    def test_handshake_pagination_capabilities_and_only_public_fields(self):
        result = self.discover()
        self.assertEqual(['actual-model', 'second-model'], [m['id'] for m in result['models']])
        self.assertEqual(['low', 'high'], result['models'][0]['reasoning_efforts'])
        self.assertNotIn('must-not-leak', str(result))

    def test_error_eof_and_repeated_cursor(self):
        for mode in ('error', 'exit', 'loop'):
            with self.subTest(mode=mode), self.assertRaises(StudioError) as error:
                self.discover(mode)
            self.assertNotIn('credential-must-not-leak', str(error.exception))

    def test_timeout_cleans_up_process(self):
        with self.assertRaisesRegex(StudioError, '超时'):
            self.discover('timeout', timeout=.3)
