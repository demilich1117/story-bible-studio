import json
import re

from support import StudioCase
from story_studio import build_parser, run
from studio_core import (StudioError, commit_turn, create_session, load_yaml, write_yaml,
                         normalize_agency, normalize_prose, normalize_status_bar, read_events)
from studio_policy import set_config
from studio_retrieval import build_context, write_context


class ConfigBehaviorsTest(StudioCase):

    def test_session_inherits_output_defaults(self):
        session = create_session(self.project, "会话")
        config = load_yaml(session / "会话配置.yaml")
        self.assertEqual("co-narrative", config["agency"])
        self.assertEqual({"min_chars": 1000, "max_chars": 1200}, config["prose"])
        self.assertEqual({"enabled": True, "fields": ["时间", "地点", "人物", "状态", "未决"]}, config["status_bar"])

    def test_session_inherits_project_overrides(self):
        from studio_core import write_yaml
        path = self.project / "项目配置.yaml"
        config = load_yaml(path)
        config["session_defaults"]["agency"] = "user-driven"
        config["session_defaults"]["prose"] = {"min_chars": 800}
        config["session_defaults"]["status_bar"] = False
        write_yaml(path, config)
        session = create_session(self.project, "会话")
        config = load_yaml(session / "会话配置.yaml")
        self.assertEqual("user-driven", config["agency"])
        self.assertEqual({"min_chars": 800, "max_chars": None}, config["prose"])
        self.assertEqual(False, config["status_bar"]["enabled"])

    def test_normalizers_reject_invalid_values(self):
        with self.assertRaises(StudioError):
            normalize_agency("puppet")
        with self.assertRaises(StudioError):
            normalize_prose({"min_chars": 1500, "max_chars": 1000})
        with self.assertRaises(StudioError):
            normalize_prose({"min_chars": "很多"})
        with self.assertRaises(StudioError):
            normalize_status_bar({"fields": []})
        self.assertEqual({"min_chars": None, "max_chars": None}, normalize_prose("off"))
        self.assertEqual("user-driven", normalize_agency("user-driven"))

    def test_set_config_session_scope_validates_and_writes(self):
        session = create_session(self.project, "会话")
        result = set_config(self.project, ["agency=user-driven", "status_bar.enabled=false",
                                           "prose.min_chars=800", "status_bar.fields=[时间,地点]"], session)
        self.assertEqual("session", result["scope"])
        config = load_yaml(session / "会话配置.yaml")
        self.assertEqual("user-driven", config["agency"])
        self.assertEqual(False, config["status_bar"]["enabled"])
        self.assertEqual(["时间", "地点"], config["status_bar"]["fields"])
        self.assertEqual(800, config["prose"]["min_chars"])
        with self.assertRaises(StudioError):
            set_config(self.project, ["agency=puppet"], session)
        with self.assertRaises(StudioError):
            set_config(self.project, ["context_budget_chars=1"], session)

    def test_set_config_project_scope_writes_session_defaults(self):
        result = set_config(self.project, ["agency=user-driven"])
        self.assertEqual("project", result["scope"])
        config = load_yaml(self.project / "项目配置.yaml")
        self.assertEqual("user-driven", config["session_defaults"]["agency"])
        session = create_session(self.project, "会话")
        self.assertEqual("user-driven", load_yaml(session / "会话配置.yaml")["agency"])

    def test_context_packet_carries_resolved_output_rules(self):
        session = create_session(self.project, "会话")
        packet, report = build_context(self.project, session, "敲门")
        self.assertEqual("ready", report["status"])
        block = re.search(r"## 写作与会话配置\n\n(.+?)(?:\n\n## |\Z)", packet, re.S).group(1)
        resolved = json.loads(block)
        self.assertEqual("co-narrative", resolved["agency"]["mode"])
        self.assertIn("代写", resolved["agency"]["rule"])
        self.assertEqual({"min_chars": 1000, "max_chars": 1200}, resolved["prose"])
        self.assertEqual(["时间", "地点", "人物", "状态", "未决"], resolved["status_bar"]["fields"])

    def test_spec_line_first_turn_has_no_change_marker(self):
        session = create_session(self.project, "会话")
        packet, report = build_context(self.project, session, "敲门")
        self.assertIn("回合规范：正文目标约 1000-1200 字 · agency co-narrative（可代写 user）· 状态栏 时间/地点/人物/状态/未决", packet)
        self.assertIn("不为差几十字", packet)
        self.assertNotIn("变更：", packet)
        self.assertEqual({"min_chars": 1000, "max_chars": 1200}, report["output_config"]["prose"])

    def test_spec_line_marks_config_change_after_commit(self):
        session = create_session(self.project, "会话")
        snapshot = {"agency": "co-narrative", "prose": {"min_chars": 1000, "max_chars": 1200},
                    "status_bar": {"enabled": True, "fields": ["时间", "地点", "人物", "状态", "未决"]}}
        source = read_events(session)[-1]["event_id"]
        commit_turn(session, "继续", "米拉检查了表盘。", operation_id="op-1",
                    expected_event_id=source, output_config=snapshot)
        config = load_yaml(session / "会话配置.yaml")
        config["prose"] = {"min_chars": 800, "max_chars": 1200}
        config["agency"] = "user-driven"
        write_yaml(session / "会话配置.yaml", config)
        packet, report = build_context(self.project, session, "继续")
        self.assertEqual("ready", report["status"])
        self.assertIn("变更：agency co-narrative → user-driven", packet)
        self.assertIn("prose 1000-1200 → 800-1200", packet)
        self.assertIn("回合规范：正文目标约 800-1200 字 · agency user-driven（不代写 user）", packet)

    def test_commit_receipt_reports_prose_chars_and_warning(self):
        session = create_session(self.project, "会话")
        args = lambda: build_parser().parse_args(["turn", "commit", "--project", str(self.project), "--session", session.name, "--operation-id", json.loads((session / ".runtime/current/transaction.json").read_text(encoding="utf-8"))["operation_id"]])
        write_context(self.project, session, "继续")
        (session / ".runtime/current/response.md").write_text("她把表放回桌上。", encoding="utf-8")
        short = run(args())
        self.assertEqual(8, short["prose_chars"])
        self.assertIn("prose_warning", short)
        self.assertIn("不要据此重写本轮", short["prose_warning"])
        write_context(self.project, session, "继续")
        (session / ".runtime/current/response.md").write_text("字" * 950, encoding="utf-8")
        within = run(args())
        self.assertEqual(950, within["prose_chars"])
        self.assertNotIn("prose_warning", within)
        events = read_events(session)
        self.assertEqual({"min_chars": 1000, "max_chars": 1200},
                         events[-1]["payload"]["output_config"]["prose"])
