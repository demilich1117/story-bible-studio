import json

from support import StudioCase
from studio_core import StudioError, create_session, load_yaml, read_events, output_config_snapshot, write_yaml, commit_turn
from studio_policy import set_config, config_view
from studio_output import resolve_output, prose_metrics
from studio_retrieval import write_context, build_context
from story_studio import build_parser, run


class OutputModulesTest(StudioCase):
    def session(self):
        return create_session(self.project, "output")

    def effective(self, session):
        return config_view(self.project, session)["effective"]

    def commit(self, session, text="她看向门口。"):
        (session / ".runtime/current/response.md").write_text(text, encoding="utf-8")
        args = build_parser().parse_args(["turn", "commit", "--project", str(self.project), "--session", session.name, "--operation-id", json.loads((session / ".runtime/current/transaction.json").read_text(encoding="utf-8"))["operation_id"]])
        return run(args)

    def test_preset_roundtrip_customizations_and_shared_modules(self):
        s = self.session()
        set_config(self.project, ["prose.min_chars=800", "status_bar.fields=[时间,着装]"], s)
        regular = self.effective(s)
        set_config(self.project, ["interaction_preset=short-rp"], s)
        short = self.effective(s)
        self.assertEqual({"min_chars": 150, "max_chars": 350}, short["prose"])
        self.assertEqual("short-rp", short["agency"])
        self.assertEqual("beat", short["span"])
        self.assertFalse(short["status_bar"]["enabled"])
        set_config(self.project, ["prose.max_chars=500", "person=second", "bilingual.enabled=true"], s)
        set_config(self.project, ["interaction_preset=regular"], s)
        restored = self.effective(s)
        for key in ("prose", "status_bar", "agency", "span"):
            self.assertEqual(regular[key], restored[key])
        self.assertEqual("second", restored["person"])
        self.assertTrue(restored["bilingual"]["enabled"])
        set_config(self.project, ["interaction_preset=short-rp"], s)
        self.assertEqual(500, self.effective(s)["prose"]["max_chars"])
        set_config(self.project, session=s, reset="short-rp")
        self.assertEqual(350, self.effective(s)["prose"]["max_chars"])

    def test_disable_enable_preserves_values_and_legacy_disabled(self):
        s = self.session()
        set_config(self.project, ["prose.min_chars=800", "status_bar.fields=[着装,时间]"], s)
        set_config(self.project, ["prose=off", "status_bar=false"], s)
        self.assertIsNone(self.effective(s)["prose"]["min_chars"])
        set_config(self.project, ["prose.enabled=true", "status_bar.enabled=true"], s)
        self.assertEqual(800, self.effective(s)["prose"]["min_chars"])
        self.assertEqual(["着装", "时间"], self.effective(s)["status_bar"]["fields"])
        config = load_yaml(s / "会话配置.yaml")
        config["status_bar"] = False
        config["prose"] = "off"
        write_yaml(s / "会话配置.yaml", config)
        set_config(self.project, ["status_bar.fields=[时间]", "prose.min_chars=700"], s)
        self.assertFalse(self.effective(s)["status_bar"]["enabled"])
        self.assertIsNone(self.effective(s)["prose"]["min_chars"])
        set_config(self.project, ["interaction_preset=short-rp"], s)
        self.assertEqual(150, self.effective(s)["prose"]["min_chars"])

    def test_invalid_batch_is_atomic(self):
        s = self.session()
        path = s / "会话配置.yaml"
        before = path.read_bytes()
        for invalid in ["status_bar.enabld=false", 'status_bar.enabled="false"',
                        "prose.max_chars=4", "person=typo", "span=random",
                        "bilingual.languages=[英语]", "pov.extra=x", "prose.foo.bar=1"]:
            with self.subTest(invalid=invalid), self.assertRaises(StudioError):
                set_config(self.project, ["person=second", invalid], s)
            self.assertEqual(before, path.read_bytes())

    def test_reset_and_undo_do_not_change_events(self):
        s = self.session()
        events = (s / "events.jsonl").read_bytes()
        set_config(self.project, ["person=second"], s)
        set_config(self.project, session=s, undo=True)
        self.assertEqual("third", self.effective(s)["person"])
        with self.assertRaises(StudioError):
            set_config(self.project, session=s, undo=True)
        set_config(self.project, ["prose.min_chars=900"], s)
        set_config(self.project, session=s, reset="prose")
        self.assertEqual(1000, self.effective(s)["prose"]["min_chars"])
        self.assertEqual(events, (s / "events.jsonl").read_bytes())

    def test_temporary_retry_expiry_and_stale_config(self):
        s = self.session()
        override = {"interaction_preset": "short-rp", "person": "second",
                    "prose": {"min_chars": 1, "max_chars": 100}}
        write_context(self.project, s, "敲门", config_override=override)
        transaction_path = s / ".runtime/current/transaction.json"
        original = json.loads(transaction_path.read_text(encoding="utf-8"))
        write_context(self.project, s, "敲门")
        self.assertEqual(original["config_override"], json.loads(transaction_path.read_text(encoding="utf-8"))["config_override"])
        (s / ".runtime/current/status.txt").write_text("时间：旧时间", encoding="utf-8")
        set_config(self.project, ["person=first"], s)
        with self.assertRaises(StudioError):
            self.commit(s)
        # Re-preparation explicitly archives both the old transaction and its draft.
        from studio_transactions import archive_preparation
        archived = archive_preparation(s, original["operation_id"], "配置已修改，复核原草稿")
        self.assertTrue((archived / "response.md").exists())
        write_context(self.project, s, "敲门", config_override=override)
        receipt = self.commit(s)
        self.assertNotIn("prose_warning", receipt)
        event = read_events(s)[-1]["payload"]
        self.assertEqual("", event["status"])
        self.assertEqual("second", event["output_config"]["person"])
        write_context(self.project, s, "继续")
        next_config = json.loads(transaction_path.read_text(encoding="utf-8"))["output_config"]
        self.assertEqual("regular", next_config["interaction_preset"])
        self.assertEqual("first", next_config["person"])

    def test_override_can_be_explicitly_cleared(self):
        s = self.session()
        write_context(self.project, s, "敲门", config_override={"person": "second"})
        from studio_transactions import archive_preparation
        operation = json.loads((s / ".runtime/current/transaction.json").read_text(encoding="utf-8"))
        archive_preparation(s, operation["operation_id"], "明确清除本轮覆盖")
        write_context(self.project, s, "敲门", config_override={})
        tx = json.loads((s / ".runtime/current/transaction.json").read_text(encoding="utf-8"))
        self.assertEqual("third", tx["output_config"]["person"])

    def test_writing_fallback_and_isolation(self):
        s = self.session()
        set_config(self.project, ["person=second", "prose.min_chars=200", "prose.max_chars=400"], s)
        self.assertEqual(1000, config_view(self.project, writing=True)["effective"]["prose"]["min_chars"])
        self.assertFalse(config_view(self.project, writing=True)["effective"]["status_bar"]["enabled"])
        set_config(self.project, ["prose.min_chars=600", "bilingual.enabled=true"], writing=True)
        view = config_view(self.project, writing=True, override={"prose": {"max_chars": 900}})
        self.assertEqual({"min_chars": 600, "max_chars": 900}, view["effective"]["prose"])
        self.assertEqual("temporary", view["sources"]["prose"])
        self.assertFalse(self.effective(s)["bilingual"]["enabled"])
        with self.assertRaises(StudioError):
            config_view(self.project, s, writing=True)

    def test_bilingual_rules_person_and_metrics(self):
        s = self.session()
        packet, report = build_context(self.project, s, "敲门", config_override={
            "person": "first", "bilingual": {"enabled": True, "languages": {"米拉": "英语"}}})
        self.assertIn("不按姓名", packet)
        self.assertIn("不改变 NPC 对白称呼", packet)
        self.assertIn("英语", packet)
        snapshot = report["output_config"]
        prose = "她说：“Wait.”（等等。）\n“好。”"
        result = prose_metrics(prose, snapshot)
        self.assertEqual(len("她说：等等。“好。”"), result["prose_chars"])
        self.assertEqual(len(prose.replace("\n", "")), result["prose_actual_chars"])
        ordinary = "她想（也许明天）。“好。”"
        self.assertEqual(len(ordinary), prose_metrics(ordinary, snapshot)["prose_chars"])

    def test_cli_show_and_temporary_file(self):
        s = self.session()
        file = self.root / "output.json"
        file.write_text(json.dumps({"person": "first"}), encoding="utf-8")
        args = build_parser().parse_args(["config", "--project", str(self.project), "--session", s.name,
                                         "--config-file", str(file)])
        self.assertEqual("first", run(args)["effective"]["person"])
        self.assertEqual("third", self.effective(s)["person"])

    def test_project_defaults_new_session_only(self):
        s = self.session()
        set_config(self.project, ["interaction_preset=short-rp", "person=second", "bilingual.enabled=true"])
        other = create_session(self.project, "new")
        self.assertEqual("short-rp", self.effective(other)["agency"])
        self.assertEqual("second", self.effective(other)["person"])
        self.assertEqual("regular", self.effective(s)["interaction_preset"])

    def test_idempotent_commit_keeps_metrics(self):
        s = self.session()
        snap = output_config_snapshot(resolve_output({"bilingual": True}))
        kwargs = {"operation_id": "metrics-retry", "output_config": snap}
        commit_turn(s, "继续", "“Wait.”（等等。）", **kwargs)
        before = json.loads((s / ".runtime/last-commit.json").read_text(encoding="utf-8"))
        commit_turn(s, "继续", "“Wait.”（等等。）", **kwargs)
        after = json.loads((s / ".runtime/last-commit.json").read_text(encoding="utf-8"))
        self.assertEqual(before, after)

    def test_disabled_display_still_updates_continuity(self):
        s = self.session()
        write_context(self.project, s, "去门口", config_override={"status_bar": False})
        current = s / ".runtime/current"
        (current / "scene-patch.json").write_text(json.dumps({"location": "门口"}), encoding="utf-8")
        (current / "state-updates.json").write_text(json.dumps({"当前局面.md": "# 当前局面\n\n信件已封好。"}), encoding="utf-8")
        self.commit(s)
        packet, report = build_context(self.project, s, "继续")
        self.assertTrue(report["output_config"]["status_bar"]["enabled"])
        self.assertIn("信件已封好", packet)
        self.assertIn("门口", packet)

    def test_bilingual_retrieves_declared_language_from_bound_dossier(self):
        role = self.project / "StoryBible/角色/Mira Chen.md"
        role.write_text(role.read_text(encoding="utf-8") + "\n\n## 其他履历\n\n曾住北岸。\n\n## 语言\n\n常用语言：英语。\n", encoding="utf-8")
        s = self.session()
        packet, report = build_context(self.project, s, "米拉", config_override={"bilingual": True})
        self.assertIn("常用语言：英语", packet)
        self.assertTrue(any(c["heading"] == "语言" for c in report["retrieved"]))

    def test_reviewed_probes_commit_with_effective_rules(self):
        from support import ROOT
        probes = json.loads((ROOT / "tests/fixtures/output-probes.json").read_text(encoding="utf-8"))
        for i, probe in enumerate(probes):
            with self.subTest(name=probe["name"]):
                s = create_session(self.project, f"probe-{i}")
                write_context(self.project, s, probe["user"], config_override=probe["config"])
                receipt = self.commit(s, probe["prose"])
                self.assertNotIn("prose_warning", receipt)
                self.assertEqual("", read_events(s)[-1]["payload"]["status"])
