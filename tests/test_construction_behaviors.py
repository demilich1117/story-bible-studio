import json
from unittest.mock import patch

from support import StudioCase
from studio_bible import finalize_bible
from studio_construction import commit_bible, prepare_bible, read_ledger, revise_bible
from studio_core import StudioError, create_session, load_yaml, read_events, reduce_events, write_yaml
from studio_profiles import apply_profile
from studio_retrieval import build_context
from studio_versions import freeze_snapshot, snapshot_root, upgrade_bible


class ConstructionBehaviors(StudioCase):
    def request(self):
        return prepare_bible(self.project, "核心概念.md", {"1": "修表师重证据", "2": "送信人重承诺"})

    def payload(self, request):
        return {"operation_id": request["operation_id"], "choice": "1+2", "edits": {
            "核心概念.md": "# 核心概念\n\n证据与承诺让两人合作。"}, "intentional_blanks": ["信封来源"]}

    def test_combined_answer_is_expanded_persisted_and_module_closed(self):
        request = self.request()
        result = commit_bible(self.project, self.payload(request))
        self.assertEqual("修表师重证据；送信人重承诺", result["decision"])
        module = read_ledger(self.project)["modules"]["核心概念.md"]
        self.assertEqual("complete", module["status"])
        self.assertEqual(["信封来源"], module["intentional_blanks"])
        with self.assertRaises(StudioError):
            self.request()

    def test_explicit_revision_replaces_old_conclusion(self):
        commit_bible(self.project, self.payload(self.request()))
        revise_bible(self.project, "核心概念.md")
        request = self.request()
        payload = {"operation_id": request["operation_id"], "decision": "两人首先保护钟楼居民", "edits": {
            "核心概念.md": "# 核心概念\n\n两人首先保护居民。"}}
        commit_bible(self.project, payload)
        module = read_ledger(self.project)["modules"]["核心概念.md"]
        self.assertEqual("两人首先保护钟楼居民", module["decision"])
        self.assertNotIn("证据与承诺", (self.project / "StoryBible/核心概念.md").read_text(encoding="utf-8"))

    def test_candidate_cannot_write_canon(self):
        payload = self.payload(self.request())
        payload["status"] = "proposal"
        with self.assertRaises(StudioError):
            commit_bible(self.project, payload)

    def test_unknown_choice_and_stale_request_are_rejected(self):
        request = self.request()
        payload = self.payload(request)
        payload["choice"] = "8"
        with self.assertRaises(StudioError):
            commit_bible(self.project, payload)
        payload["choice"] = "1"
        (self.project / "StoryBible/世界设定.md").write_text("新的世界", encoding="utf-8")
        with self.assertRaises(StudioError):
            commit_bible(self.project, payload)

    def test_interrupted_construction_can_resume_exact_payload(self):
        payload = self.payload(self.request())
        with patch("studio_construction.atomic_write_text", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                commit_bible(self.project, payload)
        self.assertEqual("pending", read_ledger(self.project)["operations"][payload["operation_id"]]["status"])
        commit_bible(self.project, payload)
        commit_bible(self.project, payload)
        self.assertEqual("complete", read_ledger(self.project)["operations"][payload["operation_id"]]["status"])

    def test_path_escape_rejected(self):
        with self.assertRaises(StudioError):
            prepare_bible(self.project, "../用户档案/秘密.md")
        payload = self.payload(self.request())
        payload["edits"] = {"../会话/hack.md": "不能写"}
        with self.assertRaises(StudioError):
            commit_bible(self.project, payload)

    def test_pending_recovery_does_not_overwrite_external_edit(self):
        payload = self.payload(self.request())
        with patch("studio_construction.atomic_write_text", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                commit_bible(self.project, payload)
        path = self.project / "StoryBible/核心概念.md"
        path.write_text("用户另行修改的内容", encoding="utf-8")
        with self.assertRaises(StudioError):
            commit_bible(self.project, payload)
        self.assertEqual("用户另行修改的内容", path.read_text(encoding="utf-8"))

    def test_freeze_creates_revision_and_prevents_direct_edit(self):
        result = finalize_bible(self.project, apply=True)
        self.assertTrue(snapshot_root(self.project, result["bible_revision"]).exists())
        self.assertEqual("frozen", load_yaml(self.project / "项目配置.yaml")["bible_status"])
        with self.assertRaises(StudioError):
            self.request()

    def test_failed_freeze_keeps_building_status(self):
        (self.project / "StoryBible/核心概念.md").write_text("# 核心概念", encoding="utf-8")
        with self.assertRaises(StudioError):
            finalize_bible(self.project, apply=True)
        self.assertEqual("building", load_yaml(self.project / "项目配置.yaml")["bible_status"])

    def test_session_pins_canon_until_explicit_reviewed_upgrade(self):
        session = create_session(self.project, "巡钟")
        original = reduce_events(read_events(session))["bible_revision"]
        (self.project / "StoryBible/核心概念.md").write_text("# 核心概念\n\n全新规则：紫色钟声。", encoding="utf-8")
        self.assertNotIn("紫色钟声", build_context(self.project, session, "继续")[0])
        preview = upgrade_bible(self.project, session)
        self.assertIn("StoryBible/核心概念.md", preview["changed_files"])
        with self.assertRaises(StudioError):
            upgrade_bible(self.project, session, True)
        review = {"from": original, "to": preview["to"], "reviewed": True, "conflicts": [], "notes": "无已有剧情冲突"}
        upgrade_bible(self.project, session, True, review)
        self.assertIn("紫色钟声", build_context(self.project, session, "继续")[0])

    def test_snapshot_tampering_detected(self):
        revision = freeze_snapshot(self.project)
        root = snapshot_root(self.project, revision)
        (root / "StoryBible/核心概念.md").write_text("被改写", encoding="utf-8")
        with self.assertRaises(StudioError):
            snapshot_root(self.project, revision)

    def test_profile_revision_is_pinned_after_import_update(self):
        first = self.player("旧身份：北岸送信人")
        session = create_session(self.project, "巡钟", profile_id="courier", opening_id="opening-01")
        second = self.player("新身份：南岸领航员")
        apply_profile(self.project, "courier", second["revision"])
        packet, report = build_context(self.project, session, "继续")
        self.assertIn("旧身份", packet)
        self.assertNotIn("新身份", packet)
        self.assertEqual(first["revision"], report["profile"]["profile_revision"])
        self.assertIn("敲了三下门", packet)
        config = load_yaml(session / "会话配置.yaml")
        config["profile_revision"] = second["revision"]
        write_yaml(session / "会话配置.yaml", config)
        self.assertIn("旧身份", build_context(self.project, session, "继续")[0])
