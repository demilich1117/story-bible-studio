from unittest.mock import patch

from support import StudioCase, SCENARIO
from studio_core import (StudioError, apply_memory, commit_turn, create_session,
                         load_yaml, prepare_memory, transition_scene, write_yaml)
from studio_context import resolve_characters
from studio_policy import set_mode
from studio_retrieval import build_context, build_index, search_chunks, write_context
from studio_versions import canon_payload, character_registry


class ContextBehaviors(StudioCase):
    def new(self, mode=None):
        session = create_session(self.project, "巡钟", mode=mode)
        transition_scene(session, "门厅", SCENARIO["scene"])
        return session

    def test_continue_includes_named_character_core_and_voice(self):
        session = self.new()
        packet, report = build_context(self.project, session, "继续")
        self.assertIn("她先核对证据", packet)
        self.assertIn("先说具体证据", packet)
        self.assertEqual(["Mira Chen"], report["active_characters"])
        self.assertEqual([], report["missing_core_voice"])
        self.assertIn("送信人", report["unresolved_characters"])
        self.assertTrue(report["voice_retrieved"])

    def test_plain_action_does_not_drop_core(self):
        session = self.new()
        for text in ("她笑了笑", "Mira Chen 检查钟表", "米拉抬起头"):
            packet, _ = build_context(self.project, session, text)
            self.assertIn("先说具体证据", packet)

    def test_continue_does_not_retrieve_unrelated_career_by_name(self):
        path = self.project / "StoryBible/角色/Mira Chen.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n\n## 职业旧事\n\nMira Chen 的另一个职业是火山考察员。", encoding="utf-8")
        session = self.new()
        packet, _ = build_context(self.project, session, "继续")
        self.assertNotIn("火山考察员", packet)

    def test_ambiguous_alias_is_never_guessed(self):
        registry = character_registry(canon_payload(self.project)["files"])
        registry["另一位"] = {"aliases": ["米拉"]}
        active, unresolved = resolve_characters(registry, ["米拉"], "继续")
        self.assertEqual(set(), active)
        self.assertEqual(["米拉"], unresolved)

    def test_no_sibling_or_author_materials_enter_context(self):
        first, second = self.new(), create_session(self.project, "隔壁")
        commit_turn(first, "只属于此处", "银色灯塔密令")
        (self.project / "故事/章节/一.md").write_text("银色灯塔正式章节", encoding="utf-8")
        (self.project / "构筑/候选/备选.md").write_text("银色灯塔候选", encoding="utf-8")
        packet, _ = build_context(self.project, second, "寻找银色灯塔")
        self.assertNotIn("银色灯塔密令", packet)
        self.assertNotIn("银色灯塔正式章节", packet)
        self.assertNotIn("银色灯塔候选", packet)
        self.assertTrue(search_chunks(self.project, "银色灯塔", scope="story"))
        self.assertFalse(search_chunks(self.project, "银色灯塔", scope="canon"))

    def test_event_projection_wins_over_corrupt_markdown(self):
        session = self.new()
        (session / "当前场景.md").write_text("错误的海底城市", encoding="utf-8")
        packet, _ = build_context(self.project, session, "继续")
        self.assertNotIn("海底城市", packet)
        self.assertIn("钟楼门厅", packet)

    def test_presets_keep_core_while_changing_recent_window(self):
        session = self.new()
        for i in range(10):
            commit_turn(session, f"第{i}步", "检查钟摆。" * 100)
        apply_memory(session, SCENARIO["promise"], 8)
        quality, qr = build_context(self.project, session, "继续", mode="quality")
        economy, er = build_context(self.project, session, "继续", mode="economy")
        self.assertEqual(5, len(qr["recent_turns"]))
        self.assertEqual(3, len(er["recent_turns"]))
        self.assertLess(len(economy), len(quality))
        for packet in (quality, economy):
            self.assertIn(SCENARIO["promise"], packet)
            self.assertIn("先说具体证据", packet)

    def test_budget_blocks_instead_of_silently_dropping_raw_turn(self):
        session = self.new()
        commit_turn(session, "长内容", "钟摆" * 30000)
        packet, report = build_context(self.project, session, "继续", mode="economy")
        self.assertEqual("budget_blocked", report["status"])
        self.assertGreater(report["budget_overflow_chars"], 0)
        path, _ = write_context(self.project, session, "继续", mode="economy")
        self.assertIn("暂停起草", path.read_text(encoding="utf-8"))

    def test_economy_compression_preserves_recent_three_turns(self):
        session = self.new("economy")
        for i in range(12):
            commit_turn(session, str(i), "检查齿轮")
        _, report = build_context(self.project, session, "继续")
        self.assertEqual("needs_compaction", report["status"])
        candidate = prepare_memory(session)
        self.assertEqual(9, candidate["through_turn"])
        self.assertEqual(9, len(candidate["turns"]))
        apply_memory(session, "前九轮已完成校时，仍需核对信封。", 9)
        self.assertEqual("ready", build_context(self.project, session, "继续")[1]["status"])

    def test_custom_settings_preserved_until_explicit_switch(self):
        session = self.new()
        config = load_yaml(session / "会话配置.yaml")
        config["context_budget_chars"] = 123456
        write_yaml(session / "会话配置.yaml", config)
        self.assertEqual(123456, build_context(self.project, session, "继续")[1]["budget_chars"])
        set_mode(self.project, "economy", session)
        self.assertEqual(40000, build_context(self.project, session, "继续")[1]["budget_chars"])

    def test_each_assembly_loads_index_once(self):
        session = self.new()
        import studio_retrieval
        with patch.object(studio_retrieval, "load_or_build_index", wraps=studio_retrieval.load_or_build_index) as loader:
            build_context(self.project, session, "继续")
            self.assertEqual(1, loader.call_count)

    def test_wrong_project_is_rejected(self):
        session = self.new()
        with self.assertRaises(StudioError):
            build_context(self.root, session, "继续")

    def test_historical_recall_is_explicit_and_session_local(self):
        session = self.new()
        commit_turn(session, "保存线索", "藏好蓝色信封")
        apply_memory(session, SCENARIO["secret"], 1)
        commit_turn(session, "归还钥匙", "完成")
        apply_memory(session, SCENARIO["resolved"], 2)
        packet, _ = build_context(self.project, session, "蓝色信封藏在哪里？")
        self.assertIn("本会话旧事", packet)
        continued, _ = build_context(self.project, session, "继续")
        self.assertNotIn("本会话旧事", continued)
