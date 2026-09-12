import json
from copy import deepcopy
from unittest.mock import patch

from support import StudioCase
from story_studio import build_parser, run
from studio_core import StudioError, create_session, load_yaml, write_yaml, read_events
from studio_retrieval import build_context, write_context
from studio_versions import freeze_snapshot, snapshot_root
from studio_styles import (compile_profile, library, list_styles, resolve_style,
                           set_style, show_style, validate_profile)


class StyleBehaviors(StudioCase):
    def cli(self, *args):
        return run(build_parser().parse_args(list(args)))

    def session(self):
        return create_session(self.project, "文风试验")

    def draft(self, session, override=None):
        write_context(self.project, session, "继续", style_override=override)
        (session / ".runtime/current/response.md").write_text("她把表放回桌上。", encoding="utf-8")

    def commit(self, session):
        path = session / ".runtime/current/transaction.json"
        if path.exists():
            self.operation_id = json.loads(path.read_text(encoding="utf-8"))["operation_id"]
        return self.cli("turn", "commit", "--project", str(self.project), "--session", session.name, "--operation-id", self.operation_id)

    def test_all_presets_expand_complete_mechanisms_without_examples(self):
        self.assertEqual(8, len(list_styles()["presets"]))
        for preset in list_styles()["presets"]:
            profile = compile_profile({"preset": preset["name"]})
            self.assertEqual(profile, validate_profile(profile))
            self.assertEqual(6, len(profile["rules"]))
        self.assertEqual("西幻", compile_profile({"preset": "西式奇幻"})["register"])

    def test_setting_style_does_not_change_canon_or_existing_legacy_session(self):
        session = self.session()
        cfg = load_yaml(session / "会话配置.yaml")
        cfg["style"] = "inherit"
        write_yaml(session / "会话配置.yaml", cfg)
        before = freeze_snapshot(self.project)
        set_style(self.project, request={"preset": "西式奇幻"})
        self.assertEqual(before, freeze_snapshot(self.project))
        self.assertTrue(snapshot_root(self.project, before).exists())
        legacy = resolve_style(self.project, session)
        self.assertTrue(legacy["legacy"])
        self.assertEqual("自然叙事", legacy["value"])
        self.assertIn("旧式继承", legacy["source"])
        set_style(self.project, session, inherit=True)
        self.assertEqual("西式奇幻", resolve_style(self.project, session)["value"])

    def test_inherit_follows_project_and_fixed_does_not(self):
        session = self.session()
        set_style(self.project, request={"preset": "西式奇幻"})
        self.assertEqual("西式奇幻", resolve_style(self.project, session)["value"])
        set_style(self.project, session, {"preset": "冷白悬疑"})
        fixed = resolve_style(self.project, session)
        set_style(self.project, request={"preset": "黑暗史诗"})
        self.assertEqual(fixed, resolve_style(self.project, session))

    def test_library_changes_do_not_mutate_saved_profiles(self):
        before = resolve_style(self.project)
        changed = deepcopy(library())
        changed["base_rules"]["rhythm"]["prefer"] = "更新后的节奏规则"
        with patch("studio_styles.library", return_value=changed):
            self.assertEqual(before, resolve_style(self.project))
            set_style(self.project, request={"preset": "自然叙事"})
            self.assertNotEqual(before["fingerprint"], resolve_style(self.project)["fingerprint"])

    def test_micro_adjustments_replace_dimension_and_preset_resets_them(self):
        profile = compile_profile({"preset": "西式奇幻"})
        rule = deepcopy(profile["rules"]["dialogue"])
        rule["prefer"] = "在熟人面前采用简短直接的问答。"
        adjusted = compile_profile({"rules": {"dialogue": rule}}, profile)
        self.assertEqual(rule, adjusted["rules"]["dialogue"])
        self.assertEqual(profile["rules"]["detail"], adjusted["rules"]["detail"])
        reset = compile_profile({"preset": "自然叙事"}, adjusted)
        self.assertNotEqual(rule, reset["rules"]["dialogue"])

    def test_invalid_new_style_is_rejected_without_writing(self):
        before = (self.project / "项目配置.yaml").read_bytes()
        for request in ({"preset": "不存在"}, {"presets": ["自然叙事"]},
                        {"register": "不存在"}, {"rules": {"rhythm": {"prefer": "短"}}},
                        {"rules": {"unknown": {}}}, {}):
            with self.subTest(request=request), self.assertRaises(StudioError):
                set_style(self.project, request=request)
        self.assertEqual(before, (self.project / "项目配置.yaml").read_bytes())

    def test_legacy_custom_text_remains_available_and_diagnostic(self):
        session = self.session()
        cfg = load_yaml(session / "会话配置.yaml")
        cfg["style"] = "克制、留白多，动作写清楚"
        write_yaml(session / "会话配置.yaml", cfg)
        packet, report = build_context(self.project, session, "继续")
        self.assertIn(cfg["style"], packet)
        self.assertTrue(report["writing_style"]["warnings"])
        self.assertIn("尚未结构化", packet)

    def test_style_rules_are_mandatory_and_budgeted_in_both_modes(self):
        session = self.session()
        for mode in ("quality", "economy"):
            packet, report = build_context(self.project, session, "继续", mode=mode)
            self.assertEqual(1, packet.count("## 有效文风规则"))
            self.assertGreater(report["section_chars"]["有效文风规则"], 0)
            self.assertNotIn("有效文风规则", report["excluded_for_budget"])
        _, report = build_context(self.project, session, "继续", budget_chars=100)
        self.assertEqual("budget_blocked", report["status"])

    def test_temporary_override_is_recorded_and_next_turn_restores_base(self):
        session = self.session()
        base = resolve_style(self.project, session)
        self.draft(session, {"preset": "西式奇幻"})
        self.commit(session)
        metadata = read_events(session)[-1]["payload"]["writing_style"]
        self.assertEqual("西式奇幻", metadata["profile"]["preset"])
        self.assertEqual(base, resolve_style(self.project, session))
        packet, report = build_context(self.project, session, "继续")
        self.assertEqual(base, report["writing_style"])
        self.assertNotIn(metadata["fingerprint"], packet)

    def test_changed_inherited_style_blocks_commit_and_preserves_draft(self):
        session = self.session()
        self.draft(session)
        set_style(self.project, request={"preset": "西式奇幻"})
        with self.assertRaisesRegex(StudioError, "有效文风已变化"):
            self.commit(session)
        self.assertTrue((session / ".runtime/current/response.md").exists())
        self.assertFalse(any(e["type"] == "turn_committed" for e in read_events(session)))

    def test_fixed_and_full_override_do_not_depend_on_project_changes(self):
        session = self.session()
        set_style(self.project, session, {"preset": "冷白悬疑"})
        self.draft(session)
        set_style(self.project, request={"preset": "黑暗史诗"})
        self.commit(session)
        set_style(self.project, session, inherit=True)
        self.draft(session, {"preset": "西式奇幻"})
        set_style(self.project, request={"preset": "硬朗行动"})
        self.commit(session)

    def test_prepared_override_survives_library_change(self):
        session = self.session()
        self.draft(session, {"preset": "西式奇幻"})
        with patch("studio_styles.library", side_effect=AssertionError("提交不能重新展开预设")):
            self.commit(session)

    def test_partial_override_tracks_only_unmodified_dimensions(self):
        session = self.session()
        rule = deepcopy(resolve_style(self.project)["profile"]["rules"]["dialogue"])
        rule["prefer"] = "私下使用简洁问答。"
        self.draft(session, {"rules": {"dialogue": rule}})
        different = {**rule, "prefer": "项目另行修改的对白机制。"}
        set_style(self.project, request={"rules": {"dialogue": different}})
        self.commit(session)
        self.draft(session, {"rules": {"dialogue": rule}})
        rhythm = deepcopy(resolve_style(self.project)["profile"]["rules"]["rhythm"])
        rhythm["prefer"] = "在休息中放慢观察。"
        set_style(self.project, request={"rules": {"rhythm": rhythm}})
        with self.assertRaisesRegex(StudioError, "有效文风已变化"):
            self.commit(session)

    def test_bad_saved_profile_and_mode_are_reported(self):
        session = self.session()
        cfg = load_yaml(session / "会话配置.yaml")
        for style in ({"mode": "typo"}, {"mode": "inherit", "profile": {}},
                      {"mode": "fixed", "profile": {"schema_version": 2}}, None):
            cfg["style"] = style
            write_yaml(session / "会话配置.yaml", cfg)
            with self.assertRaises(StudioError):
                show_style(self.project, session)

    def test_standalone_style_does_not_read_any_session(self):
        session = self.session()
        set_style(self.project, session, {"preset": "黑暗史诗"})
        with patch("studio_styles.read_events", side_effect=AssertionError("单篇不得读会话")):
            self.assertEqual("自然叙事", show_style(self.project)["value"])

    def test_retry_after_event_written_ignores_later_style_change(self):
        session = self.session()
        self.draft(session)
        with patch("studio_core.rebuild_views", side_effect=OSError("中断")):
            with self.assertRaises(OSError):
                self.commit(session)
        set_style(self.project, request={"preset": "西式奇幻"})
        self.commit(session)
        self.commit(session)
        self.assertEqual(1, sum(e["type"] == "turn_committed" for e in read_events(session)))

    def test_cli_files_support_standalone_show_and_one_turn_prepare(self):
        session = self.session()
        request = self.root / "style.yaml"
        write_yaml(request, {"preset": "西式奇幻", "register": "中式古典"})
        result = self.cli("style", "show", "--project", str(self.project), "--style-file", str(request))
        self.assertEqual("中式古典", result["profile"]["register"])
        self.assertEqual("自然叙事", resolve_style(self.project)["value"])
        user = self.root / "user.md"
        user.write_text("继续", encoding="utf-8")
        result = self.cli("turn", "prepare", "--project", str(self.project), "--session", session.name,
                          "--input-file", str(user), "--style-file", str(request))
        self.assertEqual("西式奇幻", result["writing_style"]["value"])
        self.cli("style", "set", "--project", str(self.project), "--profile-file", str(request))
        self.assertEqual("中式古典", resolve_style(self.project)["profile"]["register"])
