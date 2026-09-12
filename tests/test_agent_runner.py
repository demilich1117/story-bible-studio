"""Dispatch integration uses a fake CLI but the real ticket and event engine."""
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from support import ROOT, StudioCase
from studio_core import StudioError, create_session, read_events, session_lock
from studio_workbench import StudioService

sys.path.insert(0, str(ROOT / "workbench"))
from agent_runner import AgentRunner, command, executable
from server import DinerServer


class AgentRunnerTests(StudioCase):
    def setUp(self):
        super().setUp()
        self.service = StudioService(self.root, self.root)
        self.runner = AgentRunner(self.service)
        self.addCleanup(self.runner.close)
        self.p = self.project.name
        self.topic = self.service.bible_edit(self.p, "create", title="钟楼的新谜团",
            module="核心概念.md", text="先比较几条线索。")["topic"]

    def ticket(self, text="先比较河岸和钟楼。", kind="continue"):
        topic = self.service.bible_view(self.p, self.topic["id"])["topic"]
        return self.service.bible_edit(self.p, "request", topic_id=topic["id"],
            expected_version=topic["version"], text=text, kind=kind)

    def launch(self, ticket, mode="commit", provider="codex", retry=False, runner=None):
        def fake_command(*args):
            return [sys.executable, "-B", str(Path(__file__).resolve()), "--fake",
                    str(self.root), ticket["ticket_path"], mode, provider], None
        with patch("agent_runner.executable", return_value=sys.executable), patch("agent_runner.command", side_effect=fake_command):
            runner = runner or self.runner
            job = runner.start(ticket["ticket_path"], provider, retry=retry)
            # Keep the patched command alive until Popen has been called.
            end = time.monotonic() + 5
            while runner.active and not runner.process and time.monotonic() < end:
                time.sleep(.01)
            return job

    def finish(self, job):
        self.runner.worker.join(10)
        self.assertFalse(self.runner.worker.is_alive(), "fake CLI did not finish")
        return self.runner.status(job["id"])

    def test_bible_roundtrip_preserves_user_question_and_never_writes_rp(self):
        session = create_session(self.project, "untouched")
        before = (session / "events.jsonl").read_bytes()
        text = '原文带有引号 " & $(echo secret) 和换行\n先比较两种方向。'
        req = self.ticket(text)
        job = self.finish(self.launch(req))
        self.assertEqual("completed", job["status"], job)
        self.assertEqual("fake-thread", job["platform_session"])
        self.assertIn("河岸", job["reply"])
        self.assertIn("先调查哪里？", job["reply"])
        op = self.service.bible_view(self.p, request_id=req["id"])["operation_id"]
        detail = self.service.bible_view(self.p, self.topic["id"], event_id=op)
        self.assertEqual(text, detail["event"]["data"]["transcript"]["user"])
        self.assertEqual("先调查哪里？", detail["prompt"]["question"])
        self.assertEqual(before, (session / "events.jsonl").read_bytes())

    def test_successful_cli_exit_without_commit_is_incomplete_then_retry_uses_original_ticket(self):
        req = self.ticket()
        job = self.finish(self.launch(req, mode="prepare"))
        self.assertEqual("incomplete", job["status"])
        original = self.service.bible_view(self.p, request_id=req["id"])["operation_id"]
        job = self.finish(self.launch(req, retry=True, provider="opencode"))
        self.assertEqual("completed", job["status"], job)
        self.assertEqual(original, self.service.bible_view(self.p, request_id=req["id"])["operation_id"])
        self.assertEqual("fake-opencode", job["platform_session"])

    def test_completed_retry_and_reload_do_not_generate_again(self):
        req = self.ticket()
        job = self.finish(self.launch(req))
        self.assertEqual("completed", job["status"], job)
        before = (self.project / "构筑/events.jsonl").read_bytes()
        fresh = AgentRunner(self.service)
        self.addCleanup(fresh.close)
        self.assertEqual("completed", fresh.status(job["id"])["status"])
        with patch("agent_runner.executable", return_value=sys.executable), patch("agent_runner.command", side_effect=AssertionError("must not launch")):
            self.assertEqual("completed", fresh.start(req["ticket_path"], "codex")["status"])
            self.assertEqual("completed", fresh.start(req["ticket_path"], "codex", retry=True)["status"])
        self.assertEqual(before, (self.project / "构筑/events.jsonl").read_bytes())

    def test_revision_retains_adopted_source(self):
        prepared = self.service.bible_prepare(self.p, self.topic["id"], "采用钟楼。")
        self.service.bible_commit(self.p, {"operation_id": prepared["operation_id"], "status": "complete",
            "assistant_text": "采用钟楼。", "decision": "钟楼", "edits": {"核心概念.md": "# 核心概念\n\n钟楼。"}})
        req = self.ticket("改为从河岸开始。", "revise")
        job = self.finish(self.launch(req))
        self.assertEqual("completed", job["status"], job)
        detail = self.service.bible_view(self.p, req["topic_id"])
        self.assertEqual(self.topic["id"], detail["topic"]["revises"])
        self.assertEqual("钟楼", self.service.bible_view(self.p, self.topic["id"])["topic"]["decision"])

    def test_rp_and_regeneration_use_correct_commit_mode(self):
        session = create_session(self.project, "main")
        for kind in ("continue", "regenerate"):
            req = self.service.request(self.p, "main", self.service.version(self.project, session), "我核对钟面。", kind=kind)
            job = self.finish(self.launch(req))
            self.assertEqual("completed", job["status"], job)
        events = read_events(session)
        self.assertEqual(1, sum(e["type"] == "turn_committed" for e in events))
        self.assertEqual(1, sum(e["type"] == "variant_added" for e in events))

    def test_single_writer_duplicate_and_stop(self):
        req = self.ticket()
        job = self.launch(req, mode="wait")
        with patch("agent_runner.executable", return_value=sys.executable):
            self.assertEqual(job["id"], self.runner.start(req["ticket_path"], "opencode")["id"])
            other = AgentRunner(self.service)
            self.addCleanup(other.close)
            with self.assertRaisesRegex(StudioError, "调度锁"):
                other.start(req["ticket_path"], "codex", retry=True)
        self.runner.stop(job["id"])
        self.assertEqual("stopped", self.finish(job)["status"])
        self.assertEqual("waiting", self.service.bible_view(self.p, request_id=req["id"])["status"])
        self.assertFalse((self.runner.folder / ".session.lock").exists())

    def test_recovery_ticket_finishes_same_preparation(self):
        req = self.ticket()
        self.finish(self.launch(req, mode="prepare"))
        op = self.service.bible_view(self.p, request_id=req["id"])["operation_id"]
        recovery = self.service.bible_edit(self.p, "recovery", operation_id=op)
        job = self.finish(self.launch(recovery))
        self.assertEqual("completed", job["status"], job)
        self.assertEqual(op, self.service.bible_view(self.p, request_id=req["id"])["operation_id"])

    def test_cli_error_and_missing_executable_keep_ticket(self):
        req = self.ticket()
        job = self.finish(self.launch(req, mode="error"))
        self.assertEqual("failed", job["status"])
        self.assertEqual(7, job["exit_code"])
        with patch("agent_runner.executable", side_effect=StudioError("未找到 CLI")):
            with self.assertRaisesRegex(StudioError, "未找到"):
                self.runner.start(req["ticket_path"], "codex", retry=True)
        self.assertTrue(Path(req["ticket_path"]).is_file())

    def test_reject_arbitrary_paths_payloads_and_traversal(self):
        req = self.ticket()
        for path in (str(self.root / "arbitrary.json"), str(self.root.parent / "secret.json")):
            with self.assertRaises(StudioError):
                self.runner.ticket_info(path)
        data = json.loads(Path(req["ticket_path"]).read_text(encoding="utf-8"))
        data["project"] = "another"
        Path(req["ticket_path"]).write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(StudioError):
            self.runner.ticket_info(req["ticket_path"])
        with self.assertRaises(StudioError):
            self.runner.status("../secret")

    def test_unowned_running_job_is_not_silently_restarted(self):
        req = self.ticket()
        job = self.finish(self.launch(req))
        self.runner._save(job, status="running")
        self.assertEqual("interrupted", self.runner.status(job["id"])["status"])
        with session_lock(self.runner.folder):
            self.assertEqual("unknown", self.runner.status(job["id"])["status"])

    def test_commands_use_fresh_context_and_no_shell_bypass(self):
        prompt = 'a "quoted" prompt & $(example)'
        args, stdin = command("codex", "codex.exe", self.root, prompt)
        self.assertEqual(prompt, stdin)
        self.assertNotIn(prompt, args)
        self.assertIn("workspace-write", args)
        args, stdin = command("opencode", "opencode.exe", self.root, prompt)
        self.assertEqual(prompt, args[-1])
        self.assertIsNone(stdin)
        self.assertNotIn("--continue", args)
        self.assertNotIn("--auto", args)

    def test_desktop_is_reported_but_never_executed_as_cli(self):
        local, roaming = self.root / "Local", self.root / "Roaming"
        desktop = local / "Programs/@opencode-aidesktop/OpenCode.exe"
        desktop.parent.mkdir(parents=True)
        desktop.touch()
        with patch.dict("os.environ", {"LOCALAPPDATA": str(local), "APPDATA": str(roaming), "STORY_STUDIO_OPENCODE": ""}), patch("agent_runner.shutil.which", return_value=None):
            with self.assertRaisesRegex(StudioError, "已检测到 OpenCode 桌面版"):
                executable("opencode")
            row = self.runner.providers()["providers"][1]
            self.assertFalse(row["available"])
            self.assertTrue(row["desktop_available"])
            cli = desktop.parent / "resources/opencode-cli.exe"
            cli.parent.mkdir()
            cli.touch()
            self.assertEqual(str(cli.resolve()), executable("opencode"))

    def test_cached_desktop_cli_is_detected_without_path(self):
        local, roaming = self.root / "Local", self.root / "Roaming"
        cli = roaming / "OpenCode/cli/1.2.3/opencode-cli.exe"
        cli.parent.mkdir(parents=True)
        cli.touch()
        with patch.dict("os.environ", {"LOCALAPPDATA": str(local), "APPDATA": str(roaming), "STORY_STUDIO_OPENCODE": ""}), patch("agent_runner.shutil.which", return_value=None):
            self.assertEqual(str(cli.resolve()), executable("opencode"))

    def test_http_dispatch_requires_origin_token_and_no_generation_payloads(self):
        server = DinerServer(("127.0.0.1", 0), self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for endpoint in ("agent_start", "agent_stop"):
            for headers in ({}, {"X-Studio-Token": server.token, "Origin": "https://evil.example"}):
                request = urllib.request.Request(server.origin + "/api/" + endpoint, data=b"{}",
                    headers={"Content-Type": "application/json", **headers})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(403, error.exception.code)
                error.exception.close()
        request = urllib.request.Request(server.origin + "/api/agent_start", data=b'{"command":"echo bad"}',
            headers={"Content-Type":"application/json", "X-Studio-Token":server.token})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(400, error.exception.code)
        error.exception.close()


def fake_cli():
    workspace, path, mode, provider = sys.argv[2:]
    service = StudioService(Path(workspace), Path(workspace))
    if mode == "error":
        print(json.dumps({"type":"error", "message":"fake login required"}), flush=True)
        sys.exit(7)
    if mode == "wait":
        time.sleep(60)
        return
    info = AgentRunner(service).ticket_info(path)
    prepared = service.ticket(path)
    if mode != "prepare":
        if info["kind"] == "bible":
            service.bible_commit(info["project"], {"operation_id":prepared["operation_id"], "status":"open",
                "decision":"河岸与钟楼暂时并行。", "assistant_text":"可以比较河岸和钟楼。先调查哪里？",
                "next_prompt":{"question":"先调查哪里？", "options":{"1":"河岸", "2":"钟楼"}}})
        else:
            service.commit(info["project"], info["session"], prepared["operation_id"], "米拉核对钟面。", regenerate=prepared.get("regenerate", False))
    if provider == "codex":
        print(json.dumps({"type":"thread.started", "thread_id":"fake-thread"}))
        print(json.dumps({"type":"item.completed", "item":{"type":"agent_message", "text":"河岸和钟楼都保留。"}}))
    else:
        sid = provider.removeprefix("shared:") if provider.startswith("shared:") else "fake-opencode"
        print(json.dumps({"type":"text", "sessionID":sid, "part":{"text":"河岸和钟楼都保留。"}}))


if __name__ == "__main__" and "--fake" in sys.argv:
    fake_cli()
