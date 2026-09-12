import json
from copy import deepcopy
from unittest.mock import patch

from support import StudioCase
from studio_bible import finalize_bible
from studio_construction import read_ledger
from studio_construction_history import append_event, make_event, read_state
from studio_core import StudioError, create_session, read_events, reduce_events, load_yaml
from studio_workbench import StudioService


class BibleWorkbenchTests(StudioCase):
    def setUp(self):
        super().setUp()
        self.service = StudioService(self.root, self.root)
        self.p = self.project.name

    def topic(self, title="信件中的矛盾", text="两人的动机还在讨论。", module="核心概念.md"):
        return self.service.bible_edit(self.p, "create", title=title, text=text, module=module)["topic"]

    def prepare(self, topic, text="我们比较两种方向。", **kwargs):
        return self.service.bible_prepare(self.p, topic["id"], text, **kwargs)

    def commit(self, request, decision="米拉更相信证据。", **kwargs):
        payload = {"operation_id": request["operation_id"], "decision": decision,
                   "assistant_text": "这是展示给用户的完整回答。", "status": "open", **kwargs}
        return self.service.bible_commit(self.p, payload)

    def adopt(self, topic):
        request = self.prepare(topic)
        return self.commit(request, status="complete", edits={topic["module"]: "# 核心概念\n\n米拉更相信证据。"})

    def test_full_exchange_options_and_choice_survive_restart(self):
        topic = self.topic()
        first = self.prepare(topic, "先比较，然后再决定。")
        one = self.commit(first, decision="两条方向暂作比较。", status="proposal",
            assistant_text="1. 检查邮戳\n2. 先保护送信人", next_prompt={"question": "这一次她先做什么？", "options": {"1": "检查邮戳", "2": "先保护送信人"}})
        fresh = StudioService(self.root, self.root)
        second = fresh.bible_prepare(self.p, topic["id"], "1+2", prompt_id=one["prompt_id"])
        self.assertEqual("先保护送信人", second["context"]["prompt"]["options"]["2"])
        fresh.bible_commit(self.p, {"operation_id": second["operation_id"], "choice": "12", "status": "complete",
            "assistant_text": "她检查邮戳，同时保护送信人。", "edits": {"核心概念.md": "# 核心概念\n\n证据与承诺。"}})
        view = fresh.bible_view(self.p, topic["id"], first["operation_id"])
        self.assertEqual("先比较，然后再决定。", view["event"]["data"]["transcript"]["user"])
        self.assertIn("先保护送信人", view["event"]["data"]["transcript"]["assistant"])
        self.assertEqual("complete", view["topic"]["status"])
        self.assertEqual(["1", "2"], fresh.bible_view(self.p)["prompts"][0]["selected"])
        self.assertNotIn("transcript", json.dumps(fresh.bible_view(self.p)))

    def test_prepare_is_idempotent_and_new_request_cannot_replace_it(self):
        topic = self.topic()
        prepared = self.prepare(topic)
        self.assertEqual(prepared, self.prepare(topic))
        with self.assertRaises(StudioError):
            self.prepare(topic, "更换要求")
        self.assertEqual(prepared["operation_id"], self.service.bible_operation(self.p)["operation_id"])

    def test_unrelated_draft_does_not_invalidate_prepared_context(self):
        topic = self.topic()
        prepared = self.prepare(topic)
        unrelated = self.topic("世界中的日常", module="世界设定.md")
        self.service.bible_edit(self.p, "draft", topic_id=unrelated["id"], expected_version=1, text="记下一个无关灵感。")
        self.assertEqual("committed", self.commit(prepared)["status"])

    def test_selected_draft_change_invalidates_preparation_without_overwrite(self):
        topic = self.topic()
        prepared = self.prepare(topic)
        self.service.bible_edit(self.p, "draft", topic_id=topic["id"], expected_version=1, text="用户自己的修改")
        with self.assertRaises(StudioError):
            self.commit(prepared)
        self.assertEqual("stale", self.service.bible_operation(self.p)["status"])

    def test_wrong_question_version_is_rejected(self):
        topic = self.topic()
        self.commit(self.prepare(topic), next_prompt={"question": "选择哪边？", "options": {"1": "北岸"}})
        with self.assertRaises(StudioError):
            self.prepare(topic, "1", prompt_id="old-question")

    def test_drafts_candidates_and_rejected_edits_never_enter_canon(self):
        before = (self.project / "StoryBible/核心概念.md").read_bytes()
        topic = self.topic(text="候选：飞行钟楼")
        request = self.prepare(topic)
        with self.assertRaises(StudioError):
            self.commit(request, status="proposal", edits={"核心概念.md": "候选正文"})
        self.commit(request, status="proposal", decision="飞行钟楼暂时保留为候选。")
        self.assertEqual(before, (self.project / "StoryBible/核心概念.md").read_bytes())

    def test_complete_requires_its_own_prepared_module(self):
        request = self.prepare(self.topic())
        with self.assertRaises(StudioError):
            self.commit(request, status="complete", edits={"世界设定.md": "不能代替当前主题"})

    def test_revision_preserves_old_decision_and_pinned_session(self):
        topic = self.topic()
        self.adopt(topic)
        finalize_bible(self.project, apply=True)
        session = create_session(self.project, "修订前开局")
        pinned = reduce_events(read_events(session))["bible_revision"]
        current = read_state(self.project)["topics"][topic["id"]]
        ticket = self.service.bible_edit(self.p, "request", topic_id=topic["id"], expected_version=current["version"],
                                         kind="revise", text="改为更相信承诺。")
        self.assertEqual("frozen", load_yaml(self.project / "项目配置.yaml")["bible_status"])
        with self.assertRaises(StudioError):
            finalize_bible(self.project, apply=True)
        prepared = self.service.ticket(ticket["ticket_path"])
        self.commit(prepared, decision="她更相信承诺。", status="complete", edits={"核心概念.md": "# 核心概念\n\n米拉更相信承诺。"})
        state = read_state(self.project)
        self.assertEqual("superseded", state["topics"][topic["id"]]["status"])
        self.assertIn("证据", state["topics"][topic["id"]]["decision"])
        self.assertEqual(pinned, reduce_events(read_events(session))["bible_revision"])
        self.assertEqual("revising", load_yaml(self.project / "项目配置.yaml")["bible_status"])

    def test_adopted_topic_cannot_be_edited_as_draft(self):
        topic = self.topic()
        self.adopt(topic)
        current = read_state(self.project)["topics"][topic["id"]]
        with self.assertRaises(StudioError):
            self.service.bible_edit(self.p, "draft", topic_id=topic["id"], expected_version=current["version"], text="偷偷替换")

    def test_dependency_review_blocks_freeze_only_when_relevant(self):
        source, target = self.topic("核心欲望"), self.topic("关系张力", module="基础关系.md")
        self.adopt(source)
        self.commit(self.prepare(target), depends_on=[source["id"]])
        old = read_state(self.project)["topics"][source["id"]]
        ticket = self.service.bible_edit(self.p, "request", topic_id=source["id"], expected_version=old["version"], kind="revise", text="改变核心欲望")
        self.commit(self.service.ticket(ticket["ticket_path"]), status="complete", edits={"核心概念.md": "# 核心概念\n\n新的核心欲望。"})
        state = read_state(self.project)
        self.assertEqual([source["id"]], state["topics"][target["id"]]["needs_review"])
        with self.assertRaises(StudioError):
            finalize_bible(self.project, apply=True)
        self.commit(self.prepare(target), reviewed_dependencies=[source["id"]])
        self.assertEqual([], read_state(self.project)["topics"][target["id"]]["needs_review"])

    def test_exact_pending_payload_recovers_and_duplicate_submit_is_harmless(self):
        request = self.prepare(self.topic())
        payload = {"operation_id": request["operation_id"], "decision": "明确采用证据方向。", "status": "complete",
                   "assistant_text": "确认采用。", "edits": {"核心概念.md": "# 核心概念\n\n证据方向。"}}
        with patch("studio_construction.atomic_write_text", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.service.bible_commit(self.p, payload)
        self.assertEqual("pending", read_ledger(self.project)["operations"][request["operation_id"]]["status"])
        self.service.bible_operation(self.p, "resume", request["operation_id"])
        self.service.bible_commit(self.p, payload)
        records = [r for r in read_state(self.project)["records"] if r["id"] == request["operation_id"]]
        self.assertEqual(1, len(records))

    def test_torn_event_append_can_resume_exact_bytes(self):
        from studio_construction_history import _append
        request = self.prepare(self.topic())
        def interrupted(project, event):
            if event["kind"] != "discussion":
                return _append(project, event)
            wire = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            with (project / "构筑/events.jsonl").open("ab") as handle:
                handle.write(wire[:47])
            raise OSError("power loss")
        with patch("studio_construction_history._append", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.commit(request)
        self.service.bible_operation(self.p, "resume", request["operation_id"])
        self.assertEqual("committed", self.service.bible_operation(self.p, operation_id=request["operation_id"])["status"])

    def test_rebuild_projection_from_history(self):
        topic = self.topic()
        self.commit(self.prepare(topic))
        before = read_state(self.project)
        (self.project / "构筑/decisions.json").unlink()
        self.assertEqual(before, read_state(self.project))

    def test_legacy_view_is_read_only_and_migration_keeps_original(self):
        original = (self.project / "构筑/构筑状态.md").read_bytes()
        graph = self.service.bible_view(self.p)
        self.assertTrue(graph["topics"])
        self.assertFalse((self.project / "构筑/events.jsonl").exists())
        self.topic()
        self.assertEqual(original, (self.project / "构筑/原台账.md").read_bytes())

    def test_history_does_not_expand_normal_context_or_read_sessions(self):
        topic = self.topic()
        before = self.prepare(topic)
        self.service.bible_operation(self.p, "archive", before["operation_id"], "验证历史成本")
        other = self.topic("旁支")
        append_event(self.project, make_event("discussion", {"record": {"topic_id": other["id"], "summary": "无关旧讨论", "status": "open"},
            "transcript": {"user": "旧问题" * 10000, "assistant": "不应自动召回" * 10000}}, "unrelated-history"))
        with patch("studio_core.read_events", side_effect=AssertionError("不能读 RP 会话")):
            after = self.prepare(topic)
        self.assertEqual(before["context"], after["context"])
        self.assertNotIn("materials", {k: v for k, v in after.items() if k != "context"})

    def test_required_budget_overflow_has_no_hidden_prepare_or_model_work(self):
        topic = self.topic()
        (self.project / "StoryBible/核心概念.md").write_text("必要设定" * 2000, encoding="utf-8")
        result = self.prepare(topic, budget=1000)
        self.assertEqual("budget_blocked", result["status"])
        self.assertFalse((self.project / ".runtime/bible/prepare.json").exists())
        self.assertEqual("ready", self.prepare(topic, budget=20000)["status"])

    def test_short_ticket_consumes_once_and_rejects_foreign_file(self):
        topic = self.topic()
        ticket = self.service.bible_edit(self.p, "request", topic_id=topic["id"], expected_version=1, text="继续比较")
        self.assertLess(len(ticket["instruction"]), len(ticket["full_instruction"]))
        prepared = self.service.ticket(ticket["ticket_path"])
        self.assertEqual(prepared, self.service.ticket(ticket["ticket_path"]))
        self.commit(prepared)
        self.assertEqual("committed", self.service.ticket(ticket["ticket_path"])["status"])
        with self.assertRaises(StudioError):
            self.service.ticket(str(self.project / "项目配置.yaml"))

    def test_stale_ticket_and_archived_operation_never_change_target(self):
        topic = self.topic()
        ticket = self.service.bible_edit(self.p, "request", topic_id=topic["id"], expected_version=1, text="旧要求")
        self.service.bible_edit(self.p, "draft", topic_id=topic["id"], expected_version=1, text="新要求")
        with self.assertRaises(StudioError):
            self.service.ticket(ticket["ticket_path"])
        prepared = self.prepare(topic)
        self.service.bible_operation(self.p, "archive", prepared["operation_id"], "用户决定另起方向")
        self.assertEqual("archived", self.service.bible_operation(self.p, operation_id=prepared["operation_id"])["status"])

    def test_freeze_requires_prepared_discussion_to_finish(self):
        self.prepare(self.topic())
        with self.assertRaises(StudioError):
            finalize_bible(self.project, apply=True)

    def test_discussing_an_adopted_topic_keeps_its_canonical_conclusion(self):
        topic = self.topic()
        self.adopt(topic)
        original = deepcopy(read_state(self.project)["topics"][topic["id"]])
        self.commit(self.prepare(topic, "只是再比较另一条方向。"), status="proposal", decision="候选：另一种可能。")
        current = read_state(self.project)["topics"][topic["id"]]
        self.assertEqual(original["decision"], current["decision"])
        self.assertEqual("complete", current["status"])

    def test_pending_topic_edit_is_blocked_without_losing_recovery(self):
        topic = self.topic()
        request = self.prepare(topic)
        with patch("studio_construction.atomic_write_text", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.commit(request, status="complete", edits={"核心概念.md": "# 核心概念\n\n待恢复写入。"})
        with self.assertRaises(StudioError):
            self.service.bible_edit(self.p, "draft", topic_id=topic["id"], expected_version=1, text="另一个草稿")
        self.assertEqual("pending", self.service.bible_operation(self.p)["status"])

    def test_committed_cleanup_does_not_reapply_edits_after_external_change(self):
        topic = self.topic()
        request = self.prepare(topic)
        with patch("studio_construction._cleanup", side_effect=OSError("cleanup interrupted")):
            with self.assertRaises(OSError):
                self.commit(request, status="complete", edits={"核心概念.md": "# 核心概念\n\n原提交。"})
        path = self.project / "StoryBible/核心概念.md"
        path.write_text("后来明确进行的文件修改", encoding="utf-8")
        self.service.bible_operation(self.p, "resume", request["operation_id"])
        self.assertEqual("后来明确进行的文件修改", path.read_text(encoding="utf-8"))

    def test_legacy_pending_commit_recovers_exact_before_images(self):
        from studio_versions import digest
        payload = {"operation_id": "old-operation", "decision": "旧版已确认结论", "edits": {"核心概念.md": "# 核心概念\n\n旧版已确认结论。"}}
        path = self.project / "StoryBible/核心概念.md"
        journal = {"schema_version": 1, "modules": {}, "operations": {"old-operation": {
            "status": "pending", "request_hash": digest(payload), "module": "核心概念.md", "module_status": "complete",
            "decision": payload["decision"], "edits": payload["edits"], "before": {"核心概念.md": path.read_text(encoding="utf-8")},
            "open_questions": [], "intentional_blanks": []}}}
        (self.project / "构筑/decisions.json").write_text(json.dumps(journal, ensure_ascii=False), encoding="utf-8")
        self.assertEqual("committed", self.service.bible_commit(self.p, payload)["status"])
        self.assertIn("旧版已确认", path.read_text(encoding="utf-8"))
        self.assertEqual(1, len([r for r in read_state(self.project)["records"] if r["id"] == "old-operation"]))

    def test_legacy_ready_request_can_commit_without_inventing_transcript(self):
        from studio_versions import canon_payload, digest
        runtime = self.project / ".runtime/bible"
        runtime.mkdir(parents=True)
        request = {"operation_id": "old-ready", "module": "核心概念.md", "options": {"1": "已选方向"},
                   "source_signature": digest(canon_payload(self.project)), "materials": {"核心概念.md": "原始材料"}}
        (runtime / "prepare.json").write_text(json.dumps(request), encoding="utf-8")
        self.service.bible_commit(self.p, {"operation_id": "old-ready", "choice": "1", "edits": {"核心概念.md": "# 核心概念\n\n已选方向。"}})
        from studio_construction_history import legacy_topic, read_event
        self.assertIsNone(read_event(self.project, "old-ready")["data"]["transcript"]["user"])
        self.assertEqual("complete", read_state(self.project)["topics"][legacy_topic("核心概念.md")]["status"])

    def test_a_thousand_historical_topics_do_not_enter_preparation(self):
        topic = self.topic()
        before = self.prepare(topic)
        self.service.bible_operation(self.p, "archive", before["operation_id"], "加入大型历史样本")
        topics = {f"past-{i}": {"id": f"past-{i}", "title": f"旧想法 {i}", "module": "世界设定.md",
            "status": "parked", "version": 1, "decision": "旧讨论正文不应自动进入准备材料。" * 20,
            "depends_on": [], "open_questions": [], "intentional_blanks": []} for i in range(1000)}
        append_event(self.project, make_event("fixture_history", {"topics": topics}, "large-history"))
        self.assertGreaterEqual(len(self.service.bible_view(self.p)["topics"]), 1001)
        after = self.prepare(topic)
        self.assertEqual(before["context"], after["context"])
        self.assertEqual(before["context_chars"], after["context_chars"])

    def test_historical_question_keeps_its_own_options_in_revision_context(self):
        topic = self.topic()
        first = self.prepare(topic)
        self.commit(first, next_prompt={"question": "去哪一岸？", "options": {"1": "北岸", "2": "南岸"}})
        self.commit(self.prepare(topic, "我们先讨论另一个问题。"), next_prompt={"question": "去哪座建筑？", "options": {"1": "钟楼", "2": "码头"}})
        current = read_state(self.project)["topics"][topic["id"]]
        req = self.service.bible_edit(self.p, "request", topic_id=topic["id"], expected_version=current["version"],
            kind="revise", text="回到旧问题的北岸方向。", history_ids=[first["operation_id"]])
        ready = self.service.ticket(req["ticket_path"])
        self.assertIsNone(ready["context"]["prompt"])
        self.assertEqual("北岸", ready["context"]["history"][0]["prompt"]["options"]["1"])
        with self.assertRaises(StudioError):
            self.commit(ready, choice="1")
        self.commit(ready, decision="回到北岸方向，保留为修订候选。", status="proposal")
        self.assertEqual(current, read_state(self.project)["topics"][topic["id"]])
