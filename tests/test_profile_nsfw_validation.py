from support import StudioCase, VOICE
from studio_core import StudioError
from studio_profiles import canonical_payload, validate_profile


class ProfileNsfwValidationTests(StudioCase):
    def payload(self, nsfw=None):
        sections = {
            "角色": "# 送信人\n\n北岸来的送信人。",
            "关系接口": "# 关系\n\n与 Mira Chen 合作校对旧信。",
            "语料": VOICE,
            "开场候选": "# 开场\n\n## opening-01｜门厅\n\n送信人敲了三下门。",
        }
        if nsfw is not None:
            sections["NSFW"] = nsfw
        return {
            "schema_version": 1,
            "profile_id": "courier",
            "display_name": "送信人",
            "aliases": [],
            "anchors": ["Mira Chen"],
            "sections": sections,
            "conflicts": [],
        }

    def test_payload_requires_nsfw_body_or_intentional_blank(self):
        with self.assertRaisesRegex(StudioError, "Profile NSFW 不能为空"):
            canonical_payload(self.payload())
        with self.assertRaisesRegex(StudioError, "Profile NSFW 不能为空"):
            canonical_payload(self.payload("# 私人生活"))
        result = canonical_payload(self.payload("# 私人生活\n\n有意留白。"))
        self.assertIn("有意留白", result["sections"]["NSFW"])

    def test_validate_profile_rejects_heading_only_nsfw(self):
        self.player()
        nsfw = self.project / "用户档案/courier/NSFW.md"
        nsfw.write_text("# 私人生活\n", encoding="utf-8")
        self.assertTrue(any("NSFW.md 没有有效正文" in error for error in validate_profile(self.project, "courier")))
