"""Real loopback HTTP and CLI subprocesses; no model calls or user stories."""
import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from support import StudioCase
import test_agent_runner as runner_tests
from agent_runner import AgentRunner, command
from opencode_server import OpenCodeServer, local_url
from studio_core import StudioError, atomic_write_json
from studio_workbench import StudioService


class Backend(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_api()

    def do_POST(self):
        self.handle_api()

    def handle_api(self):
        server = self.server
        parsed = urlsplit(self.path)
        server.calls.append((self.command, parsed.path, parse_qs(parsed.query)))
        token = base64.b64encode(b"opencode:test-password").decode()
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        code = 200
        if self.headers.get("Authorization") != "Basic " + token:
            code, result = 401, {"error": "private-server-response"}
        elif parsed.path == "/global/health":
            result = {"healthy": True, "version": server.version}
        elif parsed.path == "/path":
            result = {"directory": server.directory}
        elif parsed.path == "/provider":
            result = {"connected": ["vendor"], "all": [
                {"id": "vendor", "key": "private-key", "models": {"model-a": {"name": "Model A", "secret": "private-key"}}},
                {"id": "offline", "models": {"hidden": {"name": "Hidden"}}}]}
        elif parsed.path == "/session" and self.command == "POST":
            server.created += 1
            result = {"id": "ses_test" + str(server.created)}
            server.titles.append(json.loads(body)["title"])
        elif parsed.path.endswith("/abort"):
            server.aborts.append(parsed.path.split("/")[2])
            failed = server.fail_abort or server.fail_abort_count > 0
            server.fail_abort_count = max(0, server.fail_abort_count - 1)
            code, result = (503, {"error": "test-password"}) if failed else (200, True)
        elif parsed.path == "/session/status":
            result = {"ses_unrelated": {"type": "busy"}}
        else:
            code, result = 404, {}
        raw = json.dumps(result).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class SharedServerTests(StudioCase):
    ticket = runner_tests.AgentRunnerTests.ticket
    finish = runner_tests.AgentRunnerTests.finish

    def setUp(self):
        super().setUp()
        self.service = StudioService(self.root, self.root)
        self.runner = AgentRunner(self.service)
        self.addCleanup(self.runner.close)
        self.p = self.project.name
        self.topic = self.service.bible_edit(self.p, "create", title="钟楼的新谜团",
            module="核心概念.md", text="先比较几条线索。")["topic"]
        self.env_patch = patch.dict(os.environ, {"STORY_STUDIO_OPENCODE_SERVER_URL": "",
                                               "OPENCODE_SERVER_USERNAME": "opencode",
                                               "OPENCODE_SERVER_PASSWORD": "test-password"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        self.backend.calls, self.backend.aborts, self.backend.titles = [], [], []
        self.backend.version = "1.18.30"
        self.backend.directory = str(self.root.resolve())
        self.backend.created = 0
        self.backend.fail_abort = False
        self.backend.fail_abort_count = 0
        thread = threading.Thread(target=self.backend.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.backend.server_close)
        self.addCleanup(self.backend.shutdown)
        self.url = f"http://127.0.0.1:{self.backend.server_port}"
        atomic_write_json(self.root / ".workbench/opencode-server.json",
                          {"url": self.url, "username": "opencode", "password": "test-password"})

    def launch(self, ticket, mode="commit", retry=False, model=None, reasoning_effort=None):
        self.captured = {}
        def fake_command(provider, binary, workspace, prompt, conn, sid, model=None, reasoning_effort=None):
            args, stdin = command(provider, binary, workspace, prompt, conn, sid, model, reasoning_effort)
            self.captured.update(args=args, sid=sid, prompt=prompt)
            return [sys.executable, "-B", str(Path(__file__).with_name("test_agent_runner.py")),
                    "--fake", str(self.root), ticket["ticket_path"], mode, "shared:" + sid], None
        with patch("agent_runner.executable", return_value=sys.executable), patch("agent_runner.command", side_effect=fake_command):
            job = self.runner.start(ticket["ticket_path"], "opencode_server", retry, model=model, reasoning_effort=reasoning_effort)
            # Keep patch in place until the worker has consumed it.
            import time
            end = time.monotonic() + 5
            while self.runner.active and not self.runner.process and time.monotonic() < end:
                time.sleep(.01)
        return job

    def test_shared_models_only_expose_connected_ids_and_names(self):
        result = self.runner.models("opencode_server")
        self.assertEqual([{"id": "vendor/model-a", "name": "Model A · vendor/model-a", "reasoning_efforts": []}], result["models"])
        self.assertNotIn("private-key", json.dumps(result))
        self.assertEqual(0, self.backend.created)
        self.assertEqual(str(self.root), self.backend.calls[-1][2]["directory"][0])

    def test_shared_selected_model_and_main_agent_policy(self):
        job = self.finish(self.launch(self.ticket(), model="vendor/model-a"))
        self.assertEqual("completed", job["status"], job)
        self.assertEqual("vendor/model-a", job["model"])
        self.assertEqual("main_agent_only", job["helper_policy"])
        args = self.captured["args"]
        self.assertEqual("vendor/model-a", args[args.index("--model") + 1])
        self.assertIn("ignore Luna/subagents", self.captured["prompt"])

    def test_shared_reasoning_variant_is_forwarded_and_recorded(self):
        self.runner.opencode_catalogs["opencode_server"] = {"models": [{"id": "vendor/model-a", "reasoning_efforts": ["high"]}]}
        job = self.finish(self.launch(self.ticket(), model="vendor/model-a", reasoning_effort="high"))
        self.assertEqual("completed", job["status"], job)
        self.assertEqual("high", job["reasoning_effort"])
        args = self.captured["args"]
        self.assertEqual("high", args[args.index("--variant") + 1])

    def test_attached_roundtrip_records_session_and_confirms_core(self):
        req = self.ticket()
        job = self.finish(self.launch(req))
        self.assertEqual("completed", job["status"], job)
        self.assertFalse(job["server_pending"])
        self.assertEqual("ses_test1", job["platform_session"])
        self.assertIn("先调查哪里", job["reply"])
        self.assertEqual(["ses_test1"], self.backend.aborts)
        self.assertIn("--attach", self.captured["args"])
        self.assertIn("--session", self.captured["args"])
        self.assertNotIn("--auto", self.captured["args"])
        self.assertNotIn("--continue", self.captured["args"])
        persisted = self.runner.job_path(job["id"]).read_text(encoding="utf-8")
        self.assertNotIn("test-password", persisted)
        self.assertNotIn("test-password", str(self.captured))
        for _, path, query in self.backend.calls:
            if path != "/global/health":
                self.assertEqual([str(self.root.resolve())], query["directory"])

    def test_no_commit_then_retry_uses_new_platform_session_same_operation(self):
        req = self.ticket()
        job = self.finish(self.launch(req, "prepare"))
        self.assertEqual("incomplete", job["status"])
        operation = self.service.bible_view(self.p, request_id=req["id"])["operation_id"]
        job = self.finish(self.launch(req, retry=True))
        self.assertEqual("completed", job["status"], job)
        self.assertEqual("ses_test2", job["platform_session"])
        self.assertEqual(operation, self.service.bible_view(self.p, request_id=req["id"])["operation_id"])

    def test_stop_cancels_only_owned_session(self):
        job = self.launch(self.ticket(), "wait")
        self.runner.stop(job["id"])
        job = self.finish(job)
        self.assertEqual("stopped", job["status"], job)
        self.assertEqual(["ses_test1"], self.backend.aborts)
        self.assertFalse(job["server_pending"])

    def test_disconnected_cancel_blocks_all_dispatch_until_reconciled(self):
        req = self.ticket()
        self.backend.fail_abort = True
        job = self.launch(req, "wait")
        self.runner.stop(job["id"])
        job = self.finish(job)
        self.assertEqual("unknown", job["status"])
        self.assertTrue(job["server_pending"])
        self.assertNotIn("test-password", json.dumps(job))
        fresh = AgentRunner(self.service)
        self.addCleanup(fresh.close)
        with patch("agent_runner.executable", return_value=sys.executable):
            with self.assertRaisesRegex(StudioError, "未确认停止"):
                fresh.start(req["ticket_path"], "codex", retry=True)
        self.backend.fail_abort = False
        self.assertEqual("stopped", fresh.stop(job["id"])["status"])
        job = self.finish(self.launch(req, retry=True))
        self.assertEqual("completed", job["status"], job)

    def test_late_commit_recovered_after_connection_returns(self):
        self.backend.fail_abort = True
        job = self.finish(self.launch(self.ticket()))
        self.assertEqual("unknown", job["status"])
        self.backend.fail_abort = False
        recovered = self.runner.stop(job["id"])
        self.assertEqual("completed", recovered["status"])
        self.assertEqual(1, self.backend.created)

    def test_transient_cancel_failure_preserves_confirmed_commit(self):
        self.backend.fail_abort_count = 1
        job = self.finish(self.launch(self.ticket()))
        self.assertEqual("completed", job["status"], job)
        self.assertFalse(job["server_pending"])
        self.assertEqual(["ses_test1", "ses_test1"], self.backend.aborts)

    def test_auth_version_and_directory_fail_without_prompt_dispatch(self):
        conn = OpenCodeServer(self.root, self.url, password="wrong")
        with self.assertRaisesRegex(StudioError, "认证失败"):
            conn.health()
        self.backend.version = "2.0.0"
        with self.assertRaisesRegex(StudioError, "1.x"):
            OpenCodeServer.configured(self.root).health()
        self.backend.version = "1.18.30"
        self.backend.directory = "/different/workspace"
        job = self.finish(self.launch(self.ticket()))
        self.assertEqual("failed", job["status"], job)
        self.assertEqual(0, self.backend.created)

    def test_provider_metadata_contains_no_secrets(self):
        with patch("agent_runner.executable", return_value=sys.executable):
            data = self.runner.providers()
        row = next(p for p in data["providers"] if p["id"] == "opencode_server")
        self.assertTrue(row["available"])
        self.assertEqual(self.url, row["server_url"])
        self.assertNotIn("test-password", json.dumps(data))

    def test_only_explicit_loopback_urls_and_safe_env_credentials(self):
        for url in ["https://remote.example:4096", "http://127.0.0.1:4096/path",
                    "http://name:secret@127.0.0.1:4096", "http://127.0.0.1:4096?key=secret",
                    "http://127.0.0.1", "file:///etc/passwd", "http://127.0.0.1:4096\n"]:
            with self.subTest(url=url), self.assertRaises(StudioError):
                local_url(url)
        conn = OpenCodeServer.configured(self.root)
        env = {}
        conn.env(env)
        self.assertEqual("test-password", env["OPENCODE_SERVER_PASSWORD"])
        self.assertIn("localhost", env["NO_PROXY"])
        self.assertEqual("[已隐藏]", conn.redact("test-password"))
