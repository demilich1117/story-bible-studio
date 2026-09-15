import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import ROOT
from studio_core import StudioError

sys.path.insert(0, str(ROOT / "workbench"))
from codex_cli import codex_executable
from start_codex import logged_in, prepare_codex


class CodexCliTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows installation layouts")
    def test_discovers_updated_desktop_cli_without_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "OpenAI/Codex/bin/old/codex.exe"
            new = root / "OpenAI/Codex/bin/new/codex.exe"
            for file, stamp in ((old, 100), (new, 200)):
                file.parent.mkdir(parents=True)
                file.touch()
                os.utime(file, (stamp, stamp))
            with patch.dict(os.environ, {"LOCALAPPDATA": folder, "STORY_STUDIO_CODEX": ""}), patch('codex_cli.shutil.which', return_value=None), patch('codex_cli.Path.home', return_value=root / 'home'):
                self.assertEqual(str(new.resolve()), codex_executable())
            with patch.dict(os.environ, {"STORY_STUDIO_CODEX": str(old)}):
                self.assertEqual(str(old.resolve()), codex_executable())
            with patch.dict(os.environ, {"STORY_STUDIO_CODEX": str(root / 'missing.exe')}):
                with self.assertRaises(StudioError):
                    codex_executable()

    def test_existing_login_skips_authorization(self):
        with patch('start_codex.codex_executable', return_value='cli.exe'), patch('start_codex.logged_in', return_value=True), patch('start_codex.subprocess.call') as login:
            self.assertEqual('cli.exe', prepare_codex())
            login.assert_not_called()

    def test_first_login_is_verified_and_check_mode_does_not_login(self):
        with patch('start_codex.codex_executable', return_value='cli.exe'), patch('start_codex.logged_in', side_effect=[False, True]), patch('start_codex.subprocess.call', return_value=0) as login:
            self.assertEqual('cli.exe', prepare_codex())
            login.assert_called_once_with(['cli.exe', 'login'], cwd=ROOT)
        with patch('start_codex.codex_executable', return_value='cli.exe'), patch('start_codex.logged_in', return_value=False), patch('start_codex.subprocess.call') as login:
            self.assertIsNone(prepare_codex(check_only=True))
            login.assert_not_called()

    def test_status_distinguishes_missing_login_from_cli_failure(self):
        for code, output, expected in ((0, 'Logged in', True), (1, 'Not logged in', False)):
            with patch('start_codex.subprocess.run', return_value=subprocess.CompletedProcess([], code, '', output)):
                self.assertIs(expected, logged_in('cli.exe'))
        with patch('start_codex.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'bad config secret')):
            with self.assertRaises(StudioError) as error:
                logged_in('cli.exe')
            self.assertNotIn('secret', str(error.exception))
