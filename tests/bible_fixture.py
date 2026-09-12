"""Neutral, disposable construction fixture for browser QA."""
import argparse
import json
from pathlib import Path

from support import SCENARIO, VOICE
from new_story_project import create_project
from studio_core import create_session, commit_turn, load_yaml, write_yaml
from studio_workbench import StudioService


def create_demo(workspace):
    project = create_project(workspace / "作品", "雾港钟楼 · 构筑验证", anchors=["米拉"])
    config = load_yaml(project / "项目配置.yaml")
    config["nsfw"]["enabled"] = False
    write_yaml(project / "项目配置.yaml", config)
    for path in (project / "StoryBible").glob("*.md"):
        path.write_text(f"# {path.stem}\n\n钟楼每天校时，河岸的居民依靠钟声安排生活。\n", encoding="utf-8")
    (project / "StoryBible/角色/米拉.md").write_text("# 米拉\n\n她是一位修表师，习惯先核对具体证据。", encoding="utf-8")
    (project / "StoryBible/语料/米拉.md").write_text(VOICE, encoding="utf-8")
    service = StudioService(workspace)
    name = project.name
    character = service.bible_edit(name, "create", module="角色/米拉.md", title="米拉为什么守着钟楼？", text="她留下，究竟是因为责任，还是因为一封迟来的信？")["topic"]
    prepared = service.bible_prepare(name, character["id"], "先从责任和承诺两条方向比较。")
    first = service.bible_commit(name, {"operation_id": prepared["operation_id"], "status": "proposal",
        "decision": "比较守时人的责任与对故人的承诺。", "assistant_text": "这两条方向会带来不同的选择。\n\n1. 守时人的责任：钟声是居民生活的坐标。\n2. 对故人的承诺：她在等一封永远没有寄到的信。",
        "next_prompt": {"question": "她留下来，最核心的理由是什么？", "options": {"1": "守时人的责任", "2": "对故人的承诺"}}})
    prepared = service.bible_prepare(name, character["id"], "主 1，承诺作为她不肯说出口的私心。", prompt_id=first["prompt_id"])
    service.bible_commit(name, {"operation_id": prepared["operation_id"], "status": "complete", "decision": "责任在前，等待藏在心里。", "assistant_text": "她守着钟楼，是因为这座城仍然需要准时的钟声。那封信只是她没有说出来的理由。",
        "edits": {"角色/米拉.md": "# 米拉\n\n## 人格核心\n\n修表师，先核对证据。她守着钟楼，因为这座城仍然需要准时的钟声。她也在等一封迟来的信。"}})
    mystery = service.bible_edit(name, "create", module="故事发动机.md", title="没有邮戳的信，从哪里来？", text="一封信把日常校时变成了选择。让谜团贴近人物的日常。")["topic"]
    prepared = service.bible_prepare(name, mystery["id"], "给我几个可以持续展开的方向。")
    service.bible_commit(name, {"operation_id": prepared["operation_id"], "status": "open", "decision": "留下三种信件来历，尚未决定。", "assistant_text": "信件可以从近处开始，把代价留在人物身边。\n\n1. 北岸渡船：送信人每天经过，却从未进城。\n2. 旧钟楼：信件出现在封闭多年的夹层。\n3. 熟悉的字迹：米拉认得笔迹，但日期晚了十年。",
        "next_prompt": {"question": "这封信从哪里来？", "options": {"1": "北岸渡船上的送信人", "2": "旧钟楼的封闭夹层", "3": "晚了十年的熟悉字迹"}}, "intentional_blanks": ["寄信人的最终身份"]})
    service.bible_edit(name, "create", module="世界设定.md", title="城里的人怎样依靠钟声生活？", text="市场开门、渡船起航、孩子放学。先从三种日常联系开始。")
    session = create_session(project, "雨中的第一封信")
    commit_turn(session, "我把信放到工作台上。", "米拉先擦干手，才伸手去拿那封信。\n\n窗外的钟声比约定晚了一拍。她抬起头，眉间那道细纹慢慢收紧。")
    return {"project": name, "character": character["id"], "topic": mystery["id"], "session": session.name}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(create_demo(args.workspace), ensure_ascii=False))
