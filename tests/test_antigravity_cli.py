import io
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from support import StudioCase
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workbench"))
import antigravity_cli as agy
from agent_runner import command, platform_policy
from configure_antigravity import permission_rule
from opencode_models import parse_catalog
import re
from studio_core import StudioError, create_session, read_events
import test_agent_runner as runner_tests


class AdapterTests(StudioCase):
    def test_detection_defaults_override_and_wrapper_rejection(self):
        binary = self.root / "agy/bin/agy.exe"
        binary.parent.mkdir(parents=True)
        binary.touch()
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root), "STORY_STUDIO_ANTIGRAVITY": ""}), patch.object(agy.shutil, "which", return_value=None), patch.object(agy.os, "name", "nt"):
            self.assertEqual(str(binary.resolve()), agy.antigravity_executable())
            with patch.dict(os.environ, {"STORY_STUDIO_ANTIGRAVITY": str(self.root / "missing.exe")}):
                with self.assertRaises(StudioError):
                    agy.antigravity_executable()
            wrapper = binary.with_suffix(".cmd")
            wrapper.touch()
            with patch.dict(os.environ, {"STORY_STUDIO_ANTIGRAVITY": str(wrapper)}):
                with self.assertRaises(StudioError):
                    agy.antigravity_executable()

    def test_ticket_is_stdin_and_context_is_fresh(self):
        prompt = '中文 " & $(never)\nnext line'
        args, stdin = command("antigravity", "agy.exe", self.root, prompt, model="gemini-test")
        self.assertEqual(prompt, json.loads(stdin)["message"]["content"])
        self.assertNotIn(prompt, args)
        self.assertEqual("gemini-test", args[args.index("--model") + 1])
        for flag in ("--continue", "--conversation", "--dangerously-skip-permissions"):
            self.assertNotIn(flag, args)
        self.assertIn("Platform: Antigravity", platform_policy("antigravity"))
        self.assertIn("ignore Luna/subagents", platform_policy("antigravity"))

    def test_msix_install_is_detected_from_non_packaged_environment(self):
        local = self.root / "AppData/Local"
        binary = local / "Packages/OpenAI.Codex_test/LocalCache/Local/agy/bin/agy.exe"
        binary.parent.mkdir(parents=True)
        binary.touch()
        with patch.dict(os.environ, {"LOCALAPPDATA": str(local), "STORY_STUDIO_ANTIGRAVITY": ""}), patch.object(agy.shutil, "which", return_value=None), patch.object(agy.Path, "home", return_value=self.root):
            self.assertEqual(str(binary.resolve()), agy.antigravity_executable())
            standard = local / "agy/bin/agy.exe"
            standard.parent.mkdir(parents=True)
            standard.touch()
            self.assertEqual(str(standard.resolve()), agy.antigravity_executable())

    def test_saved_user_location_survives_stale_launcher_environment(self):
        import winreg
        binary = self.root / "custom/agy.exe"
        binary.parent.mkdir()
        binary.touch()
        with patch.dict(os.environ), patch.object(winreg, "OpenKey") as key, patch.object(winreg, "QueryValueEx", return_value=(str(binary), winreg.REG_SZ)):
            os.environ.pop("STORY_STUDIO_ANTIGRAVITY", None)
            self.assertEqual(str(binary.resolve()), agy.antigravity_executable())
            key.assert_called_once_with(winreg.HKEY_CURRENT_USER, "Environment")

    def test_saved_user_location_survives_stale_launcher_environment(self):
        import winreg
        binary = self.root / "custom/agy.exe"
        binary.parent.mkdir()
        binary.touch()
        with patch.dict(os.environ), patch.object(winreg, "OpenKey") as key, patch.object(winreg, "QueryValueEx", return_value=(str(binary), winreg.REG_SZ)):
            os.environ.pop("STORY_STUDIO_ANTIGRAVITY", None)
            self.assertEqual(str(binary.resolve()), agy.antigravity_executable())
            key.assert_called_once_with(winreg.HKEY_CURRENT_USER, "Environment")

    def test_catalog_and_errors(self):
        output = "Fetching available models...\ngemini-test\tGemini Test (High)\nclaude-test\tClaude Test\n"
        with patch.object(agy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output)) as run:
            result = agy.discover_models("agy.exe", self.root)
            self.assertEqual(["gemini-test", "claude-test"], [m["id"] for m in result["models"]])
            run.side_effect = subprocess.TimeoutExpired("agy", 20)
            with self.assertRaisesRegex(StudioError, "超时"):
                agy.discover_models("agy.exe", self.root)
            run.side_effect = None
            run.return_value = subprocess.CompletedProcess([], 1, "")
            with self.assertRaises(StudioError):
                agy.discover_models("agy.exe", self.root)

    def test_event_stream_is_bounded_and_checks_conversation(self):
        job = {"reply": ""}
        agy.consume_event(job, {"event": "init", "conversation_id": "abc"})
        agy.consume_event(job, {"event": "step_update", "step_update": {"conversation_id": "abc", "step_type": "agent_response", "text_delta": "a" * 20000}})
        self.assertEqual(16000, len(job["reply"]))
        agy.consume_event(job, {"event": "result", "result": {"conversation_id": "abc", "status": "SUCCESS", "response": "final"}})
        self.assertEqual("final", job["reply"])
        agy.consume_event(job, {"event": "step_update", "step_update": {"step_type": "tool", "tool_info": {"output": "PRIVATE"}}})
        self.assertNotIn("PRIVATE", str(job))
        with self.assertRaises(StudioError):
            agy.consume_event(job, {"event": "init", "conversation_id": "other"})

    def test_diagnostics_do_not_persist_raw_content(self):
        flags = {}
        agy.read_diagnostics(io.StringIO("authentication required SECRET\ntool soft-denied SECRET\n"), flags)
        self.assertEqual({"auth": True, "permission": True}, flags)
        self.assertNotIn("SECRET", agy.diagnostic_hint(flags))

    def test_effort_flags_and_no_default_override(self):
        for provider, flag in (("antigravity", "--effort"), ("opencode", "--variant"), ("opencode_server", "--variant")):
            server = type("Server", (), {"url": "http://127.0.0.1:4096"})()
            args, _ = command(provider, "cli.exe", self.root, "prompt", server, "ses_test", "vendor/model", "high")
            self.assertEqual("high", args[args.index(flag) + 1])
            args, _ = command(provider, "cli.exe", self.root, "prompt", server, "ses_test", "vendor/model")
            self.assertNotIn(flag, args)
        with self.assertRaises(StudioError):
            command("antigravity", "cli.exe", self.root, "prompt", reasoning_effort="max")

    def test_opencode_catalog_only_returns_public_variants(self):
        info = {"name": "Model", "api": {"key": "SECRET"}, "variants": {"low": {}, "custom-deep": {"budget": 500}, "disabled": {"disabled": True}, "--bad": {}}}
        catalog = parse_catalog("Loading...\nvendor/model\n" + json.dumps(info, indent=2) + "\nvendor/plain\n{}\n")
        self.assertEqual(["low", "custom-deep"], catalog[0]["reasoning_efforts"])
        self.assertEqual([], catalog[1]["reasoning_efforts"])
        self.assertNotIn("SECRET", str(catalog))
        self.assertNotIn("budget", str(catalog))

    def test_permission_is_limited_to_core_and_single_command(self):
        from support import ROOT
        rule = permission_rule(ROOT)
        pattern = rule[len("command(regex:"):-1]
        script = ROOT / '.agents/skills/story-bible-studio/scripts/story_studio.py'
        allowed = f"python -B '{script}' ticket --workspace '{ROOT}' --file 'ticket.json'"
        self.assertRegex(allowed, pattern)
        for value in ("python -c 'bad'", allowed + "; echo bad", allowed + "\nwhoami", allowed + " $(bad)", allowed.replace("ticket --workspace", "milestone --workspace")):
            self.assertIsNone(re.fullmatch(pattern, value))


class AntigravityIntegrationTests(StudioCase):
    def setUp(self):
        super().setUp()
        self.service = runner_tests.StudioService(self.root, self.root)
        self.runner = runner_tests.AgentRunner(self.service)
        self.addCleanup(self.runner.close)
        self.p = self.project.name
        self.topic = self.service.bible_edit(self.p, "create", title="钟楼的新谜团",
            module="核心概念.md", text="先比较几条线索。")["topic"]
    ticket = runner_tests.AgentRunnerTests.ticket
    launch = runner_tests.AgentRunnerTests.launch
    finish = runner_tests.AgentRunnerTests.finish

    def test_real_core_commit_and_retry(self):
        req = self.ticket()
        first = self.finish(self.launch(req, mode="prepare", provider="antigravity"))
        self.assertEqual("incomplete", first["status"])
        operation = self.service.bible_view(self.p, request_id=req["id"])["operation_id"]
        job = self.finish(self.launch(req, provider="antigravity", retry=True))
        self.assertEqual("completed", job["status"])
        self.assertEqual("fake-agy", job["platform_session"])
        self.assertEqual("main_agent_only", job["helper_policy"])
        self.assertEqual(operation, self.service.bible_view(self.p, request_id=req["id"])["operation_id"])


    def test_auth_and_soft_denial_keep_ticket(self):
        for mode, status, hint in (("auth", "failed", "登录"), ("denied", "incomplete", "权限不足")):
            req = self.ticket()
            job = self.finish(self.launch(req, mode=mode, provider="antigravity"))
            self.assertEqual(status, job["status"])
            self.assertIn(hint, job["message"])
            self.assertEqual("waiting", self.service.bible_view(self.p, request_id=req["id"])["status"])


    def test_rp_commits_through_core(self):
        session = create_session(self.project, "agy-test")
        req = self.service.request(self.p, "agy-test", self.service.version(self.project, session), "核对钟面。")
        job = self.finish(self.launch(req, provider="antigravity"))
        self.assertEqual("completed", job["status"])
        self.assertEqual(1, sum(e["type"] == "turn_committed" for e in read_events(session)))


    def test_stop_retains_original_ticket(self):
        req = self.ticket()
        job = self.launch(req, mode="wait", provider="antigravity")
        self.runner.stop(job["id"])
        self.assertEqual("stopped", self.finish(job)["status"])
        self.assertEqual("waiting", self.service.bible_view(self.p, request_id=req["id"])["status"])

    def test_effort_persists_and_opencode_rejects_unadvertised_variant(self):
        req = self.ticket()
        first = self.finish(self.launch(req, mode="prepare", provider="antigravity", reasoning_effort="low"))
        self.assertEqual("low", first["reasoning_effort"])
        self.runner.opencode_catalogs["opencode"] = {"models": [{"id": "vendor/model", "reasoning_efforts": ["custom-deep"]}]}
        with patch("agent_runner.executable") as binary:
            with self.assertRaises(StudioError):
                self.runner.start(req["ticket_path"], "opencode", retry=True, model="vendor/model", reasoning_effort="high")
            binary.assert_not_called()
        job = self.finish(self.launch(req, retry=True, provider="opencode", model="vendor/model", reasoning_effort="custom-deep"))
        self.assertEqual("completed", job["status"])
        self.assertEqual("custom-deep", self.dispatched[7])
        self.assertEqual("custom-deep", job["reasoning_effort"])
