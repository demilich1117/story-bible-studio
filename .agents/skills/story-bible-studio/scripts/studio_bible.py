from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from studio_core import StudioError, atomic_write_text, load_yaml
from studio_retrieval import save_index
from studio_profiles import voice_errors


REQUIRED_BIBLE_FILES = (
    "核心概念.md",
    "世界设定.md",
    "基础关系.md",
    "基础时间线.md",
    "故事发动机.md",
    "NSFW基础设定.md",
    "连续性与留白.md",
)

PROFILE_CURRENT_FILES = ("角色.md", "关系接口.md", "NSFW.md", "语料.md", "开场候选.md")

STALE_MARKERS = (
    ("待构筑", re.compile(r"待构筑")),
    ("构筑中残留", re.compile(r"构筑中")),
    ("TODO/TBD", re.compile(r"\b(?:TODO|TBD)\b", re.I)),
    (
        "模板说明残留",
        re.compile(
            r"按需记录：怎么勾上|每条标注标准名|记录真正稳定的欲望开关|"
            r"只保存关键指针与一行摘要"
        ),
    ),
)

BACKGROUND_LEAKS = (
    ("通用成年／自愿声明", re.compile(r"(?:所有|双方|参与者).{0,18}(?:成年|自愿)|成年.{0,12}自愿")),
    ("通用停止声明", re.compile(r"可(?:随时)?停止|可沟通.{0,12}可停止")),
    ("现实免责声明", re.compile(r"仅为作品内连续性|不延伸为现实(?:医学|法律|财务)结论")),
)

DIRECT_NSFW_TERMS = re.compile(r"鸡巴|逼|屄|小穴|龟头|乳头|精液|内射|足交|手交|口交|性交")
GENITAL_SUBJECT = re.compile(r"性器官|生殖器|私处|外阴|阴唇|阴蒂|阴道|小穴|穴口|阴茎|鸡巴|龟头|阴囊|睾丸")
GENITAL_STABLE_DESCRIPTION = re.compile(
    r"外观|形态|颜色|色泽|肤色|肉色|饱满|柔软|细嫩|圆润|厚实|薄软|"
    r"合拢|肉缝|露出|藏在|包皮|阴毛|耻毛|体毛|修短|剃净"
)
GENITAL_INTENTIONAL_BLANK = re.compile(
    r"(?:性器官|生殖器|外阴|阴茎|鸡巴|阴蒂|阴道|小穴|私处).{0,24}(?:有意留白|未设定|未确定)|"
    r"(?:有意留白|未设定|未确定).{0,24}(?:性器官|生殖器|外阴|阴茎|鸡巴|阴蒂|阴道|小穴|私处)"
)


def _body_after_title(text: str) -> str:
    lines = [line for line in text.splitlines() if not line.startswith("#") and line.strip()]
    return "\n".join(lines).strip()


def _section_body(text: str, heading_pattern: str) -> str | None:
    match = re.search(rf"(?m)^##\s+(?:{heading_pattern})\s*$", text)
    if not match:
        return None
    tail = text[match.end():]
    next_heading = re.search(r"(?m)^##\s+", tail)
    return (tail[:next_heading.start()] if next_heading else tail).strip()


def _active_markdown_files(project: Path) -> list[Path]:
    return sorted((project / "StoryBible").rglob("*.md"))


def _relative(project: Path, path: Path) -> str:
    return path.relative_to(project).as_posix()


def _duplicate_warnings(project: Path, files: list[Path]) -> list[str]:
    occurrences: dict[str, list[str]] = defaultdict(list)
    for path in files:
        if "StoryBible" not in path.parts:
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = re.sub(r"\s+", " ", raw.strip())
            if len(line) < 30 or line.startswith("#"):
                continue
            if "｜来源：" in line or line.startswith(("- 状态：", "- 来源：")):
                continue
            occurrences[line].append(_relative(project, path))
    warnings = []
    for line, paths in occurrences.items():
        unique_paths = list(dict.fromkeys(paths))
        if len(unique_paths) > 1:
            preview = line[:70] + ("…" if len(line) > 70 else "")
            warnings.append(f"跨文件重复：{preview}（{'、'.join(unique_paths)}）")
    return warnings


def audit_bible(project: Path, for_finalize: bool = False) -> dict[str, Any]:
    project = project.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    config_path = project / "项目配置.yaml"
    config = load_yaml(config_path)
    bible_status = str(config.get("bible_status", ""))

    for filename in REQUIRED_BIBLE_FILES:
        path = project / "StoryBible" / filename
        if not path.exists():
            errors.append(f"缺少 StoryBible/{filename}")
        elif for_finalize and not _body_after_title(path.read_text(encoding="utf-8")):
            errors.append(f"StoryBible/{filename} 仍为空骨架")

    scope = config.get("story_scope", {})
    for anchor in scope.get("anchors", []) if isinstance(scope, dict) else []:
        character = project / "StoryBible" / "角色" / f"{anchor}.md"
        if for_finalize and not character.exists():
            errors.append(f"锚点角色缺少档案：StoryBible/角色/{anchor}.md")
        voice = project / "StoryBible" / "语料" / f"{anchor}.md"
        if for_finalize and not voice.exists():
            errors.append(f"锚点角色缺少完整声线文件：StoryBible/语料/{anchor}.md")

    voice_root = project / "StoryBible" / "语料"
    if for_finalize and voice_root.exists():
        for voice in sorted(voice_root.glob("*.md")):
            errors.extend(voice_errors(voice.read_text(encoding="utf-8"), _relative(project, voice)))

    active_files = [path for path in _active_markdown_files(project) if path.exists()]
    for path in active_files:
        text = path.read_text(encoding="utf-8")
        rel = _relative(project, path)
        if for_finalize:
            for label, pattern in STALE_MARKERS:
                if pattern.search(text):
                    errors.append(f"{rel} 含{label}；冻结前应确认或改写为有意留白")
        for label, pattern in BACKGROUND_LEAKS:
            if pattern.search(text):
                errors.append(f"{rel} 含{label}，应改为人物／世界事实或留在技能后台")

    nsfw_path = project / "StoryBible" / "NSFW基础设定.md"
    nsfw = nsfw_path.read_text(encoding="utf-8") if nsfw_path.exists() else ""
    nsfw_config = config.get("nsfw", {}) if isinstance(config.get("nsfw", {}), dict) else {}
    if nsfw_config.get("enabled"):
        erotic_body = _section_body(nsfw, "情色身体")
        if for_finalize and erotic_body is not None:
            visible_body = erotic_body.strip()
            if not visible_body:
                errors.append("NSFW基础设定的“情色身体”仍为空；应写入人物身体事实或明确有意留白")
            elif not (
                (GENITAL_SUBJECT.search(visible_body) and GENITAL_STABLE_DESCRIPTION.search(visible_body))
                or GENITAL_INTENTIONAL_BLANK.search(visible_body)
            ):
                errors.append(
                    "NSFW基础设定的“情色身体”未包含性器官的稳定情色身体描写，也未明确标为有意留白"
                )
        if nsfw_config.get("explicitness") == "explicit" and nsfw and not DIRECT_NSFW_TERMS.search(nsfw):
            warnings.append("NSFW explicitness 为 explicit，但正文缺少直接器官或动作词；需人工确认是否仍偏抽象／医学化")
        if not nsfw_config.get("primary_kinks"):
            warnings.append("NSFW 已启用但 primary_kinks 为空；若作品确实不以成人内容为主，可关闭 nsfw.enabled")

    empty_limits = _section_body(nsfw, "用户明确指定的特殊禁区")
    if empty_limits == "":
        errors.append("NSFW基础设定保留了空的“用户明确指定的特殊禁区”章节；无特殊禁区时应删除")

    warnings.extend(_duplicate_warnings(project, active_files))

    index = project / "00-项目索引.md"
    index_text = index.read_text(encoding="utf-8") if index.exists() else ""
    expected_label = {"building": "构筑中", "revising": "修订中", "frozen": "已冻结"}.get(bible_status)
    if expected_label and f"Story Bible：{expected_label}" not in index_text:
        errors.append(f"00-项目索引.md 与 bible_status={bible_status} 不同步，应显示“Story Bible：{expected_label}”")

    construction = project / "构筑" / "构筑状态.md"
    if construction.exists() and expected_label:
        construction_text = construction.read_text(encoding="utf-8")
        if f"阶段：{expected_label}" not in construction_text:
            errors.append(f"构筑/构筑状态.md 与 bible_status={bible_status} 不同步")

    return {
        "status": "blocked" if errors else ("needs-review" if warnings else "ready"),
        "bible_status": bible_status,
        "for_finalize": for_finalize,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "active_file_count": len(active_files),
    }


def _sync_status_view(path: Path, label: str, prefix: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f"{prefix}{label}"
    if re.search(rf"(?m)^{re.escape(prefix)}.*$", text):
        text = re.sub(rf"(?m)^{re.escape(prefix)}.*$", line, text, count=1)
    else:
        text = text.rstrip() + "\n\n" + line + "\n"
    atomic_write_text(path, text)


def finalize_bible(project: Path, apply: bool = False) -> dict[str, Any]:
    project = project.resolve()
    report = audit_bible(project, for_finalize=True)
    report["action"] = "dry-run"
    if not apply:
        return report
    if report["errors"]:
        raise StudioError("Story Bible 尚不能冻结：\n- " + "\n- ".join(report["errors"]))

    from studio_validate_v3 import validate
    from studio_construction import read_ledger
    if any(v["status"] == "pending" for v in read_ledger(project)["operations"].values()):
        raise StudioError("存在未完成构筑事务，不能冻结")
    if (project / ".runtime/bible/prepare.json").exists():
        raise StudioError("有准备中的构筑讨论，请先完成或显式归档")
    blocking = [t["title"] for t in read_ledger(project).get("topics", {}).values()
                if t.get("status") not in {"parked", "superseded"}
                and (t.get("needs_review") or (t.get("revises") and t.get("status") != "complete"))]
    if blocking:
        raise StudioError("修订或关联影响尚未处理：" + "、".join(blocking))
    before_errors = validate(project)
    if before_errors:
        raise StudioError("冻结前结构验证失败：\n- " + "\n- ".join(before_errors))

    config_path = project / "项目配置.yaml"
    config_text = config_path.read_text(encoding="utf-8")
    if not re.search(r"(?m)^bible_status:\s*", config_text):
        raise StudioError("项目配置缺少 bible_status")
    config_text = re.sub(r'(?m)^bible_status:\s*.*$', 'bible_status: "frozen"', config_text, count=1)
    atomic_write_text(config_path, config_text)
    _sync_status_view(project / "00-项目索引.md", "已冻结", "- Story Bible：")
    construction = project / "构筑" / "构筑状态.md"
    if construction.exists():
        _sync_status_view(construction, "已冻结", "- 阶段：")
    index_path = save_index(project)

    from studio_validate_v3 import validate

    validation_errors = validate(project)
    if validation_errors:
        raise StudioError("冻结后验证失败：\n- " + "\n- ".join(validation_errors))
    final_report = audit_bible(project, for_finalize=True)
    from studio_versions import freeze_snapshot
    revision = freeze_snapshot(project)
    final_report.update({"action": "applied", "index": str(index_path), "bible_revision": revision})
    return final_report
