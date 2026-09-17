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
from agent_runner import AgentRunner, command, dispatch_prompt, executable
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

    def launch(self, ticket, mode="commit", provider="codex", retry=False, runner=None, model=None, reasoning_effort=None):
        def fake_command(*args):
            self.dispatched = args
            return [sys.executable, "-B", str(Path(__file__).resolve()), "--fake",
                    str(self.root), ticket["ticket_path"], mode, provider], None
        with patch("agent_runner.executable", return_value=sys.executable), patch("agent_runner.command", side_effect=fake_command):
            runner = runner or self.runner
            job = runner.start(ticket["ticket_path"], provider, retry=retry, model=model, reasoning_effort=reasoning_effort)
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

    def test_dispatch_is_a_small_pointer_not_an_inlined_story_or_manual(self):
        marker = "仅存在于小票中的用户原文-不要内联到启动提示词"
        ticket = self.ticket(marker)
        before = Path(ticket["ticket_path"]).read_bytes()
        for provider in ("codex", "opencode", "opencode_server", "antigravity"):
            prompt = dispatch_prompt(self.root, ticket["ticket_path"], provider)
            self.assertIn(ticket["instruction"], prompt)
            self.assertIn(str(ROOT / "workbench/agent-guide.md"), prompt)
            self.assertNotIn(marker, prompt)
            self.assertNotIn(ticket["full_instruction"], prompt)
            self.assertLess(len(prompt) - len(ticket["instruction"]), 750 if provider == "antigravity" else 500)
        self.assertEqual(before, Path(ticket["ticket_path"]).read_bytes())
        self.assertEqual("waiting", self.service.bible_view(self.p, request_id=ticket["id"])["status"])

    def test_selected_model_is_dispatched_recorded_and_retry_can_change_it(self):
        req = self.ticket()
        job = self.finish(self.launch(req, mode="prepare", provider="opencode", model="provider/model-a"))
        self.assertEqual("provider/model-a", job["model"])
        self.assertEqual("provider/model-a", self.dispatched[6])
        self.assertEqual("main_agent_only", job["helper_policy"])
        self.assertIn("ignore Luna/subagents", self.dispatched[3])
        self.assertIn("all memory thresholds still apply", self.dispatched[3])
        operation = self.service.bible_view(self.p, request_id=req["id"])["operation_id"]
        job = self.finish(self.launch(req, retry=True, model="model-b"))
        self.assertEqual("completed", job["status"], job)
        self.assertEqual("model-b", job["model"])
        self.assertEqual("session", job["helper_policy"])
        self.assertNotIn("ignore Luna/subagents", self.dispatched[3])
        self.assertEqual(operation, self.service.bible_view(self.p, request_id=req["id"])["operation_id"])

    def test_model_arguments_are_separate_and_invalid_values_never_launch(self):
        for provider, model in (("codex", "model-a"), ("opencode", "vendor/model-a:latest")):
            args, _ = command(provider, "cli.exe", self.root, "prompt", model=model)
            self.assertEqual(model, args[args.index("--model") + 1])
        req = self.ticket()
        for model in ("--help", "vendor/model --auto", "vendor/$(bad)", "model-only", "vendor/", {}, True, "x" * 241):
            with self.subTest(model=model), patch("agent_runner.executable") as binary:
                with self.assertRaises(StudioError):
                    self.runner.start(req["ticket_path"], "opencode", model=model)
                binary.assert_not_called()

    def test_local_model_discovery_filters_logs_and_handles_failures(self):
        import subprocess
        result = subprocess.CompletedProcess([], 0, "Loading models\nvendor/b\nvendor/a\nvendor/b\n", "")
        with patch("agent_runner.executable", return_value="cli.exe"), patch("agent_runner.subprocess.run", return_value=result) as run:
            self.assertEqual(["vendor/a", "vendor/b"], [m["id"] for m in self.runner.models("opencode")["models"]])
            self.assertEqual(["cli.exe", "models", "--verbose"], run.call_args.args[0])
            self.assertEqual(self.root, run.call_args.kwargs["cwd"])
            run.side_effect = subprocess.TimeoutExpired("models", 15)
            with self.assertRaisesRegex(StudioError, "超时"):
                self.runner.models("opencode")

    def test_reasoning_is_dispatched_persisted_and_can_change_on_retry(self):
        req = self.ticket()
        first = self.finish(self.launch(req, mode="prepare", model="model-a", reasoning_effort="high"))
        self.assertEqual("high", first["reasoning_effort"])
        self.assertEqual("high", self.dispatched[7])
        operation = self.service.bible_view(self.p, request_id=req["id"])["operation_id"]
        job = self.finish(self.launch(req, retry=True, model="model-b", reasoning_effort="low"))
        self.assertEqual("completed", job["status"])
        self.assertEqual("low", self.runner.status(job["id"])["reasoning_effort"])
        self.assertEqual("low", self.dispatched[7])
        self.assertEqual(operation, self.service.bible_view(self.p, request_id=req["id"])["operation_id"])
        args, _ = command("codex", "cli.exe", self.root, "prompt", model="model-b", reasoning_effort="low")
        self.assertIn('model_reasoning_effort="low"', args)
        args, _ = command("codex", "cli.exe", self.root, "prompt")
        self.assertFalse(any("model_reasoning_effort" in arg for arg in args))

    def test_invalid_or_unsupported_reasoning_never_launches(self):
        req = self.ticket()
        self.runner.codex_catalog = {"models": [{"id": "small", "reasoning_efforts": ["low"]}]}
        self.runner.opencode_catalogs = {name: {"models": []} for name in ("opencode", "opencode_server")}
        for provider, effort in (("codex", {}), ("codex", True), ("codex", "high --bad"),
                                 ("opencode", "high"), ("opencode_server", "high"), ("codex", "high")):
            with self.subTest(provider=provider, effort=effort), patch("agent_runner.executable") as binary:
                with self.assertRaises(StudioError):
                    self.runner.start(req["ticket_path"], provider, model="small", reasoning_effort=effort)
                binary.assert_not_called()

    def test_codex_catalog_is_read_without_creating_a_generation(self):
        catalog = {"models": [{"id": "test", "reasoning_efforts": ["low", "high"]}]}
        with patch("agent_runner.executable", return_value="cli.exe"), patch("agent_runner.discover_models", return_value=catalog) as discover:
            self.assertEqual(catalog, self.runner.models("codex"))
            discover.assert_called_once_with("cli.exe", self.root)
            self.assertIsNone(self.runner.active)
            self.assertFalse(self.runner.folder.exists())

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

    def test_http_model_catalog_is_read_only_and_checks_origin(self):
        server = DinerServer(("127.0.0.1", 0), self.root)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.object(server.agents, "models", return_value={"models": [{"id": "vendor/a", "name": "A"}]}) as models:
            with urllib.request.urlopen(server.origin + "/api/agent_models?provider=opencode_server") as response:
                self.assertEqual("vendor/a", json.load(response)["models"][0]["id"])
            models.assert_called_once_with("opencode_server")
            request = urllib.request.Request(server.origin + "/api/agent_models?provider=opencode", headers={"Origin": "https://evil.example"})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(403, error.exception.code)
            error.exception.close()
            self.assertEqual(1, models.call_count)


def fake_cli():
    workspace, path, mode, provider = sys.argv[2:]
    service = StudioService(Path(workspace), Path(workspace))
    if provider == "antigravity" and mode in {"auth", "denied"}:
        print("authentication required" if mode == "auth" else "tool soft-denied: permissions.allow", file=sys.stderr)
        print(json.dumps({"event": "result", "result": {"conversation_id": "fake-agy", "status": "ERROR" if mode == "auth" else "SUCCESS", "response": ""}}))
        sys.exit(1 if mode == "auth" else 0)
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
    if provider == "antigravity":
        print(json.dumps({"event": "init", "conversation_id": "fake-agy", "init": {}}))
        print(json.dumps({"event": "step_update", "step_update": {"conversation_id": "fake-agy", "step_type": "agent_response", "text_delta": "已经处理"}}))
        print(json.dumps({"event": "result", "result": {"conversation_id": "fake-agy", "status": "SUCCESS", "response": "已经处理。"}}))
    elif provider == "codex":
        print(json.dumps({"type":"thread.started", "thread_id":"fake-thread"}))
        print(json.dumps({"type":"item.completed", "item":{"type":"agent_message", "text":"河岸和钟楼都保留。"}}))
    else:
        sid = provider.removeprefix("shared:") if provider.startswith("shared:") else "fake-opencode"
        print(json.dumps({"type":"text", "sessionID":sid, "part":{"text":"河岸和钟楼都保留。"}}))


if __name__ == "__main__" and "--fake" in sys.argv:
    fake_cli()
