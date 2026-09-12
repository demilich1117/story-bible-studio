import concurrent.futures
import json
from unittest.mock import patch

from support import StudioCase
from studio_core import (create_session, read_events, commit_turn, branch_session, create_checkpoint,
                         StudioError, session_lock, LockError, load_yaml, write_yaml)
from studio_workbench import StudioService, Conflict
from story_studio import build_parser, run


class RequirementsRecoveryTests(StudioCase):
    def setUp(self):
        super().setUp()
        self.session = create_session(self.project, "main")
        self.service = StudioService(self.root, self.root)
        self.p = self.project.name

    def version(self):
        return self.service.version(self.project, self.session)

    def rules(self, **kw):
        return self.service.requirements(self.p, "main", **kw)

    def save(self, text, kind="banned"):
        return self.rules(action="save", expected_version=self.rules()["version"], items=[{"id": "r1", "type": kind, "text": text}])

    def prepare(self, text="继续核对钟面。", **kw):
        return self.service.prepare(self.p, "main", text, self.version(), **kw)

    def commit(self, prepared, text="她放下怀表。"):
        return self.service.commit(self.p, "main", prepared["operation_id"], text, regenerate=prepared["regenerate"])

    def test_rules_persist_independently_and_pin_ready_reply(self):
        before = self.version()
        ticket = self.service.request(self.p, "main", before, "继续")
        self.save("不容置疑")
        self.assertEqual(before, self.version())
        prepared = self.service.prepare(self.p, "main", request_id=ticket["id"])
        self.assertIn("不容置疑", prepared["context"])
        self.assertTrue(self.save("彻底")["after_current"])
        self.assertEqual(prepared, self.service.prepare(self.p, "main", request_id=ticket["id"]))
        checked = self.rules(action="check", operation_id=prepared["operation_id"], prose="彻底。不容置疑。")
        self.assertEqual(["不容置疑"], [h["text"] for h in checked["hits"]])
        result = self.commit(prepared, "不容置疑。")
        self.assertEqual(1, result["requirements_check"]["requirements_version"])
        self.assertEqual(1, read_events(self.session)[-1]["payload"]["requirements"]["version"])
        next_reply = self.prepare()
        self.assertEqual(2, next_reply["requirements"]["version"])
        self.assertIn("彻底", next_reply["context"])

    def test_save_undo_validation_isolation_and_branch(self):
        self.assertEqual(0, self.rules()["version"])
        self.save("保持克制", "guidance")
        with self.assertRaises(StudioError):
            self.rules(action="save", expected_version=0, items=[])
        with self.assertRaises(StudioError):
            self.rules(action="save", expected_version=1, items=[{"id": "r", "type": "banned", "text": ""}])
        self.assertEqual(1, self.rules()["version"])
        self.rules(action="undo", expected_version=1)
        self.assertFalse(self.rules()["items"])
        self.assertFalse(self.rules()["can_undo"])
        self.save("不要总结每一轮。", "guidance")
        other = create_session(self.project, "other")
        self.assertEqual([], self.service.requirements(self.p, other.name)["items"])
        commit_turn(self.session, "继续", "她校准怀表。")
        create_checkpoint(self.session, "cp", 1)
        target = branch_session(self.project, "main", "cp", "branch")
        self.assertEqual(self.rules()["items"], self.service.requirements(self.p, target.name)["items"])
        self.save("新要求", "guidance")
        self.assertNotEqual(self.rules()["items"], self.service.requirements(self.p, target.name)["items"])

    def test_blocked_prepare_reloads_new_requirements(self):
        cfg = load_yaml(self.session / "会话配置.yaml")
        cfg["context_budget_chars"] = 200
        write_yaml(self.session / "会话配置.yaml", cfg)
        ticket = self.service.request(self.p, "main", self.version(), "继续")
        first = self.service.prepare(self.p, "main", request_id=ticket["id"])
        self.assertEqual("budget_blocked", first["status"])
        self.save("保持人物口气", "guidance")
        second = self.service.prepare(self.p, "main", request_id=ticket["id"])
        self.assertEqual(1, second["requirements"]["version"])
        self.assertIn("本会话创作要求", second["report"]["section_chars"])

    def test_recovery_of_direct_cli_preparation_keeps_overrides(self):
        from studio_context import write_context
        override = {"interaction_preset":"short-rp"}
        write_context(self.project, self.session, "原始 CLI 输入。", query="钟面", config_override=override)
        original = self.service.operation(self.p, "main")
        recovery = original["recovery"]
        restored = self.service.prepare(self.p, "main", recovery["user_text"], original["version"],
                                        override=recovery["override"], style_override=recovery["style_override"],
                                        mode=recovery["mode"], query=recovery["query"])
        self.assertEqual(original["operation_id"], restored["operation_id"])
        self.assertEqual(original["context"], restored["context"])

    def test_archive_retains_input_drafts_and_rejects_late_commit(self):
        self.save("旧禁词")
        override = {"prose": {"min_chars": 100, "max_chars": 200}}
        prepared = self.prepare("原输入。", override=override)
        (self.session / ".runtime/current/response.md").write_text("未提交的草稿", encoding="utf-8")
        shown = self.service.operation(self.p, "main", prepared["operation_id"])
        self.assertEqual("未提交的草稿", shown["drafts"]["response.md"])
        old_version = self.version()
        archived = self.service.operation(self.p, "main", prepared["operation_id"], "archive", old_version, "重新开始")
        again = self.service.operation(self.p, "main", prepared["operation_id"], "archive", old_version, "重试")
        self.assertEqual(archived["recovery"], again["recovery"])
        self.assertEqual("原输入。", archived["recovery"]["user_text"])
        self.assertEqual(override, archived["recovery"]["override"])
        self.assertFalse(self.service.pending(self.session))
        self.assertEqual(archived["recovery"], self.service.snapshot(self.p, "main")["last_restart"])
        self.save("新禁词")
        new = self.prepare("原输入。", override=override)
        self.assertNotEqual(prepared["operation_id"], new["operation_id"])
        self.assertEqual(2, new["requirements"]["version"])
        with self.assertRaises(Conflict):
            self.commit(prepared)
        args = build_parser().parse_args(["turn", "commit", "--project", str(self.project), "--session", "main",
                                         "--operation-id", prepared["operation_id"]])
        with self.assertRaisesRegex(StudioError, "ID"):
            run(args)
        self.commit(new)

    def test_regeneration_recovery_keeps_author_instructions(self):
        commit_turn(self.session, "我核对时间。", "她举起怀表。")
        prepared = self.prepare("不要总结。", regenerate=True)
        self.save("保持口气", "guidance")
        original = self.service.operation(self.p, "main", prepared["operation_id"])
        self.assertEqual(0, original["requirements"]["version"])
        self.assertEqual("regenerate", original["recovery"]["kind"])
        self.assertEqual("不要总结。", original["recovery"]["user_text"])
        ticket = self.service.recovery_ticket(self.p, "main", prepared["operation_id"])
        self.assertIn(prepared["operation_id"], ticket["instruction"])
        archived = self.service.operation(self.p, "main", prepared["operation_id"], "archive", self.version(), "重新开始")
        recovered = self.prepare(archived["recovery"]["user_text"], regenerate=True)
        self.assertEqual(1, recovered["requirements"]["version"])
        self.assertIn("我核对时间。", recovered["context"])
        self.assertEqual(2, len(self.commit(recovered)["variant"]))

    def test_committed_before_cleanup_finishes_without_duplicate_events(self):
        for regenerate in (False, True):
            if regenerate:
                prepared = self.prepare("重写", regenerate=True)
            else:
                prepared = self.prepare()
            with patch("studio_core.rebuild_views", side_effect=RuntimeError("模拟渲染中断")):
                with self.assertRaises(RuntimeError):
                    self.commit(prepared)
            count = len(read_events(self.session))
            result = self.service.operation(self.p, "main", prepared["operation_id"])
            self.assertEqual("committed", result["status"])
            self.assertTrue(result["needs_finish"])
            attempt = self.service.operation(self.p, "main", prepared["operation_id"], "archive", "stale", "用户想重开")
            self.assertEqual("committed", attempt["status"])
            version = self.version()
            self.service.operation(self.p, "main", prepared["operation_id"], "finish", version)
            self.service.operation(self.p, "main", prepared["operation_id"], "finish", version)
            self.assertFalse(self.service.pending(self.session))
            self.assertEqual(count, len(read_events(self.session)))

    def test_damage_and_lock_are_reported_without_deleting_files(self):
        path = self.session / ".runtime/current/transaction.json"
        path.write_text("{broken", encoding="utf-8")
        snapshot = self.service.snapshot(self.p, "main")
        self.assertEqual("damaged", snapshot["operations"][0]["status"])
        self.assertEqual("damaged", self.service.operation(self.p, "main")["status"])
        self.assertEqual("{broken", path.read_text(encoding="utf-8"))
        with session_lock(self.session):
            self.assertTrue(self.service.status(self.p, "main")["locked"])
            self.assertTrue(self.service.session_diagnostic(self.p, "main")["locked"])
            self.assertIn("需要诊断", self.service.recovery_ticket(self.p, "main")["instruction"])
        self.assertTrue(path.exists())

    def test_archive_and_late_commit_race_has_one_narrative_outcome(self):
        prepared = self.prepare()
        version = self.version()
        def execute(action):
            try:
                if action == "commit":
                    return self.commit(prepared)
                return self.service.operation(self.p, "main", prepared["operation_id"], "archive", version, "用户重开")
            except (StudioError, LockError):
                return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(execute, ["commit", "archive"]))
        self.assertTrue(any(results))
        events = [e for e in read_events(self.session) if e["type"] == "turn_committed"]
        state = self.service.operation(self.p, "main", prepared["operation_id"])
        self.assertEqual(1 if state["status"] == "committed" else 0, len(events))
        self.assertFalse(self.service.pending(self.session))

    def test_concurrent_saves_only_one_wins(self):
        def save(text):
            try:
                return self.rules(action="save", expected_version=0, items=[{"id":"r", "type":"guidance", "text":text}])
            except (StudioError, LockError):
                return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(save, ["A", "B"]))
        self.assertEqual(1, sum(r is not None for r in results))
        self.assertEqual(1, self.rules()["version"])
