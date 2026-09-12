from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from studio_core import ensure_new, safe_name, write


WEEKDAYS = "一二三四五六日"


def local_date_anchor() -> str:
    now = datetime.now().astimezone()
    return f"{now:%Y %m %d} 星期{WEEKDAYS[now.weekday()]} {now:%H}：{now:%M}"


def create_project(
    root: Path,
    name: str,
    mode: str = "character-hub",
    anchors: list[str] | None = None,
    date_anchor: str | None = None,
) -> Path:
    name = safe_name(name)
    if mode not in {"character-hub", "world-hub"}:
        raise ValueError(f"非法项目模式: {mode}")
    anchors = [safe_name(value) for value in (anchors or [])]
    anchors_yaml = "[]" if not anchors else "\n" + "\n".join(f'    - "{value}"' for value in anchors)
    project = root / name
    ensure_new(project)
    anchor_value = date_anchor.strip() if date_anchor else local_date_anchor()

    write(project / "项目配置.yaml", f'''schema_version: 3
title: "{name}"
bible_status: "building"
construction:
  context_budget_chars: 20000
default_style: "自然叙事"
time_display:
  mode: "modern"
  format: "YYYY MM DD 星期X HH：MM"
  anchor: "{anchor_value}"
story_scope:
  mode: "{mode}"
  anchors: {anchors_yaml}
session_defaults:
  mode: quality
  interaction_preset: regular
  person: third
  initiative: inherit
  span: inherit
  bilingual:
    enabled: false
    strategy: world
    languages: {{}}
  agency: "co-narrative"
  prose:
    min_chars: 1000
    max_chars: 1200
  status_bar:
    enabled: true
    fields: ["时间", "地点", "人物", "状态", "未决"]
  recent_turns: 5
  compression_cadence: 16
  context_budget_chars: 80000
  memory_policy:
    soft_context_ratio: 0.75
    hard_context_ratio: 0.90
    max_uncompressed_turns: 16
    min_uncompressed_turns: 6
    compact_on_scene_transition: false
nsfw:
  enabled: true
  explicitness: "explicit"
  organ_terms: "direct"
  dirty_talk: "character-driven"
  primary_kinks: []
  hard_limits: []
  contraception_or_fertility: "follow-canon"
  aftercare: "character-specific"
subagents:
  enabled: true
  model: "gpt-5.6-luna"
  reasoning_effort: "medium"
  cadence: "milestones"
  write_policy: "read-only"
  allow_nsfw_context: true''')
    write(
        project / "00-项目索引.md",
        f"# {name}\n\n## 当前入口\n\n- Story Bible：构筑中\n- 构筑台账：[构筑/构筑状态.md](构筑/构筑状态.md)\n- 互动会话：[会话/](会话/)\n- 正式创作：[故事/](故事/)\n",
    )
    write(project / "构筑" / "原始StoryBible.md", "# 原始 Story Bible\n\n在此保存导入原稿，不将本文件作为分类后的正式正史。")
    write(project / "构筑" / "构筑状态.md", """# 构筑状态

## 当前阶段

- 阶段：构筑中
- 问答粒度：整套预设优先；只有会改变核心方向的分歧才拆开追问。

## 已确认模块

- 在每批问答后记录已经稳定的结论，不复制完整 Story Bible 正文。

## 下一批问题

- 只保留真正阻碍下一步构筑的问题；能由既有核心自然推出的细节直接并入预设套装。

## 有意留白

- 记录允许留给正式故事发现、且不会妨碍人物稳定选择的细节。
""")

    bible = {
        "核心概念.md": "# 核心概念",
        "世界设定.md": "# 世界设定",
        "基础关系.md": "# 基础关系",
        "基础时间线.md": "# 基础时间线",
        "故事发动机.md": "# 故事发动机\n\n## 核心循环\n\n## 可持续支线\n\n## 节奏与回流点",
        "NSFW基础设定.md": "# NSFW 基础设定\n\n## 情色身体\n\n待构筑：用符合人物与作品语感的情色描写补足胸部、乳头与乳晕、性器官、皮肤与体毛；以整体观感、关键触感和自然反应为主，不写成解剖清单或参数表。未明确项在定稿时改为有意留白。\n\n## 亲密取向与互动方式\n\n## 偏好与具体表现",
        "连续性与留白.md": "# 连续性与留白\n\n## 写作时必须守住的连续性\n\n## 有意留白",
    }
    for filename, heading in bible.items():
        write(project / "StoryBible" / filename, heading)
    for dirname in ("角色", "语料", "地点与势力"):
        write(project / "StoryBible" / dirname / ".gitkeep", "")
    write(project / "用户档案" / ".gitkeep", "")

    for dirname in ("构筑/候选", "会话", "故事/章节", "故事/片段", "素材/研究", "导出"):
        write(project / dirname / ".gitkeep", "")
    from studio_styles import set_style
    set_style(project, request={"preset": "自然叙事"})
    return project


def main() -> None:
    parser = argparse.ArgumentParser(description="创建 Story Bible Studio 作品骨架")
    parser.add_argument("--name", required=True, help="作品名")
    parser.add_argument("--root", default="作品", help="作品根目录，默认 ./作品")
    parser.add_argument("--mode", choices=("character-hub", "world-hub"), default="character-hub")
    parser.add_argument("--anchor", action="append", default=[])
    parser.add_argument("--date-anchor", help="显式日期锚点；省略时按本地创建时间自动建立候选锚点")
    args = parser.parse_args()
    print("[deprecated] 请改用 story_studio.py project new", file=__import__("sys").stderr)
    print(create_project(Path(args.root).resolve(), args.name, args.mode, args.anchor, args.date_anchor))


if __name__ == "__main__":
    main()
