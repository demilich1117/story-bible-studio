import json
from unittest.mock import patch

from support import StudioCase, SCENARIO
from studio_core import (StudioError, add_variant, apply_memory, branch_session, commit_turn,
                         create_checkpoint, create_session, prepare_memory, read_events,
                         rebuild_views, reduce_events, select_variant, transition_scene, validate_session)
from studio_retrieval import build_context, write_context
from story_studio import build_parser, run


class SessionBehaviors(StudioCase):
    def new(self):
        return create_session(self.project, "巡钟")

    def test_retry_after_projection_failure_writes_exactly_one_turn(self):
        session = self.new()
        source = read_events(session)[-1]["event_id"]
        with patch("studio_core.rebuild_views", side_effect=OSError("disk interrupted")):
            with self.assertRaises(OSError):
                commit_turn(session, "继续", "米拉检查了表盘。", operation_id="retry", expected_event_id=source)
        first = commit_turn(session, "继续", "米拉检查了表盘。", operation_id="retry", expected_event_id=source)
        second = commit_turn(session, "继续", "米拉检查了表盘。", operation_id="retry", expected_event_id=source)
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(1, reduce_events(read_events(session))["current_turn"])
        self.assertEqual([], validate_session(session))
        with self.assertRaises(StudioError):
            commit_turn(session, "继续", "不同正文", operation_id="retry")

    def test_stale_prepare_cannot_commit(self):
        session = self.new()
        old = read_events(session)[-1]["event_id"]
        transition_scene(session, "门厅", SCENARIO["scene"])
        with self.assertRaises(StudioError):
            commit_turn(session, "继续", "回复", operation_id="stale", expected_event_id=old)
        self.assertEqual(0, reduce_events(read_events(session))["current_turn"])

    def test_invalid_payload_never_enters_event_log(self):
        session = self.new()
        before = len(read_events(session))
        with self.assertRaises(StudioError):
            commit_turn(session, "继续", "回复", scene_patch=["非法"])
        with self.assertRaises(StudioError):
            commit_turn(session, "继续", "回复", state_updates={"../外部.md": "非法"})
        self.assertEqual(before, len(read_events(session)))

    def test_cli_rejects_changed_configuration_and_input(self):
        from studio_core import load_yaml, write_yaml
        session = self.new()
        write_context(self.project, session, "继续")
        (session / ".runtime/current/response.md").write_text("答复", encoding="utf-8")
        config = load_yaml(session / "会话配置.yaml")
        config["pov"] = "changed"
        write_yaml(session / "会话配置.yaml", config)
        args = build_parser().parse_args(["turn", "commit", "--project", str(self.project), "--session", session.name, "--operation-id", json.loads((session / ".runtime/current/transaction.json").read_text(encoding="utf-8"))["operation_id"]])
        with self.assertRaises(StudioError):
            run(args)

    def test_cli_transaction_updates_state_and_repeated_commit_returns_receipt(self):
        session = self.new()
        write_context(self.project, session, "继续")
        current = session / ".runtime/current"
        (current / "response.md").write_text("她检查了邮戳。", encoding="utf-8")
        (current / "state-updates.json").write_text(json.dumps({"线索与未决问题.md": SCENARIO["promise"]}), encoding="utf-8")
        args = build_parser().parse_args(["turn", "commit", "--project", str(self.project), "--session", session.name, "--operation-id", json.loads((session / ".runtime/current/transaction.json").read_text(encoding="utf-8"))["operation_id"]])
        result = run(args)
        repeated = run(args)
        self.assertEqual(result["event_id"], repeated["event_id"])
        self.assertEqual([], list(current.iterdir()))
        self.assertIn(SCENARIO["promise"], (session / "状态/线索与未决问题.md").read_text(encoding="utf-8"))
        self.assertEqual([], validate_session(session))

    def test_prepare_does_not_overwrite_pending_prose(self):
        session = self.new()
        write_context(self.project, session, "继续")
        (session / ".runtime/current/response.md").write_text("尚未保存的正文", encoding="utf-8")
        with self.assertRaises(StudioError):
            write_context(self.project, session, "换个方向")

    def test_old_variant_is_applied_before_later_turn_and_transition(self):
        session = self.new()
        commit_turn(session, "去门口", "门口", scene_patch={"location": "门口"})
        transition_scene(session, "钟楼", {"location": "楼梯"})
        commit_turn(session, "上楼", "楼顶", scene_patch={"location": "楼顶"})
        variant = add_variant(session, 1, "庭院", scene_patch={"location": "庭院"})
        select_variant(session, 1, variant)
        state = reduce_events(read_events(session))
        self.assertEqual("楼顶", state["scene"]["location"])
        self.assertEqual("钟楼", state["current_scene"])

    def test_historical_change_invalidates_memory_and_old_state(self):
        session = self.new()
        commit_turn(session, "收下钥匙", "收下")
        apply_memory(session, "已收到钥匙", 1, {"当前局面.md": "钥匙在手"})
        variant = add_variant(session, 1, "拒绝钥匙", state_updates={"当前局面.md": "没有钥匙"})
        select_variant(session, 1, variant)
        state = reduce_events(read_events(session))
        self.assertEqual(0, state["last_compressed_turn"])
        self.assertNotIn("已收到", state["memory"])
        self.assertEqual("没有钥匙", state["continuity_state"]["当前局面.md"])
        self.assertEqual("needs_compaction", build_context(self.project, session, "继续")[1]["status"])

    def test_two_compactions_carry_previous_memory_and_current_state(self):
        session = self.new()
        commit_turn(session, "接任务", "答应", state_updates={"线索与未决问题.md": SCENARIO["promise"]})
        apply_memory(session, SCENARIO["promise"], 1)
        commit_turn(session, "调查", "发现信封")
        candidate = prepare_memory(session, 2)
        self.assertEqual(SCENARIO["promise"], candidate["previous_memory"])
        self.assertEqual(SCENARIO["promise"], candidate["current_state"]["线索与未决问题.md"])
        self.assertEqual([2], [t["turn"] for t in candidate["turns"]])
        apply_memory(session, SCENARIO["promise"] + SCENARIO["secret"], 2, expected_event_id=candidate["source_event_id"])
        commit_turn(session, "归还", SCENARIO["resolved"])
        candidate = prepare_memory(session, 3)
        self.assertIn(SCENARIO["secret"], candidate["previous_memory"])
        apply_memory(session, SCENARIO["resolved"] + SCENARIO["secret"], 3, {"线索与未决问题.md": "调查蓝色信封"}, candidate["source_event_id"])
        state = reduce_events(read_events(session))
        self.assertNotIn(SCENARIO["promise"], state["memory"])
        self.assertIn(SCENARIO["secret"], state["memory"])

    def test_memory_candidate_becomes_stale_after_new_turn(self):
        session = self.new()
        commit_turn(session, "一", "一")
        candidate = prepare_memory(session, 1)
        commit_turn(session, "二", "二")
        with self.assertRaises(StudioError):
            apply_memory(session, "摘要", 1, expected_event_id=candidate["source_event_id"])

    def test_checkpoint_is_immutable_against_later_variants(self):
        session = self.new()
        commit_turn(session, "继续", "原版", scene_patch={"location": "门厅"})
        create_checkpoint(session, "原版检查点")
        variant = add_variant(session, 1, "未来改写", scene_patch={"location": "街道"})
        select_variant(session, 1, variant)
        branch = branch_session(self.project, session.name, "原版检查点", "实验")
        state = reduce_events(read_events(branch))
        self.assertEqual("v1", state["selected_variants"]["1"])
        self.assertEqual("门厅", state["scene"]["location"])
        self.assertEqual(reduce_events(read_events(session))["bible_revision"], state["bible_revision"])

    def test_event_views_can_be_rebuilt_without_losing_state(self):
        session = self.new()
        commit_turn(session, "继续", "正文", state_updates={"当前局面.md": "钟停了"})
        (session / "当前场景.md").write_text("陈旧视图", encoding="utf-8")
        self.assertTrue(validate_session(session))
        rebuild_views(session)
        self.assertEqual([], validate_session(session))
