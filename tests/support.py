import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents/skills/story-bible-studio/scripts"
sys.path.insert(0, str(SCRIPTS))

from new_story_project import create_project
from studio_core import load_yaml, write_yaml
from studio_profiles import import_profile

SCENARIO = json.loads((ROOT / "tests/fixtures/scenario.json").read_text(encoding="utf-8"))
VOICE = "# 声线\n\n## 核心声线\n\n先说具体证据，再提出一个简短问题。\n\n## 对象覆盖：送信人\n\n熟悉但不替对方作决定。\n\n## 场景覆盖：钟楼\n\n谈钟楼时间时先核对表盘。"


class StudioCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = create_project(self.root, SCENARIO["title"], anchors=["Mira Chen"])
        cfg = load_yaml(self.project / "项目配置.yaml")
        cfg["nsfw"]["enabled"] = False
        write_yaml(self.project / "项目配置.yaml", cfg)
        bible = self.project / "StoryBible"
        for path in bible.glob("*.md"):
            path.write_text(f"# {path.stem}\n\n钟楼每日校时，居民会为迟到付出代价。\n", encoding="utf-8")
        (bible / "角色/Mira Chen.md").write_text('# Mira “米拉” Chen\n\n## 基本身份\n\n钟楼修表师。\n\n## 人格核心\n\n她先核对证据，再决定相信谁。\n\n## 工作细节\n\n蓝色信封必须检查邮戳。', encoding="utf-8")
        (bible / "语料/Mira Chen.md").write_text(VOICE, encoding="utf-8")

    def player(self, marker="来自北岸的送信人"):
        payload = {"schema_version": 1, "profile_id": "courier", "display_name": "送信人",
                   "aliases": [], "anchors": ["Mira Chen"], "sections": {
                       "角色": "# 送信人\n\n## 核心身份\n\n" + marker,
                       "关系接口": "# 关系\n\n## Mira Chen\n\n合作校对旧信件。",
                       "NSFW": "# 私人生活\n\n有意留白。", "语料": VOICE,
                       "开场候选": "# 开场\n\n## opening-01｜门厅\n\n送信人敲了三下门。"}, "conflicts": []}
        path = self.root / "profile.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return import_profile(self.project, path)
