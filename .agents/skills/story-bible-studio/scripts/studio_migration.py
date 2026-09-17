from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any

from studio_core import StudioError, atomic_write_json, atomic_write_text, load_yaml, load_project_config, write_yaml
from studio_profiles import (
    PROFILE_CONFIG,
    PROFILE_CONFIG_SCHEMA,
    PROFILE_FILES,
    archive_current,
    normalize_openings,
    revision_for,
)


SESSION_FILE_RE = re.compile(
    r"^(?:user-turn|assistant-turn|turn|response|status|scene-patch|scene-transition|memory|timeline-updates)[-_]",
    re.I,
)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _merge_tree(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.iterdir()):
        destination = target / item.name
        if destination.exists():
            if item.is_dir() and destination.is_dir():
                _merge_tree(item, destination)
                item.rmdir()
            elif item.is_file() and destination.is_file() and _hash_file(item) == _hash_file(destination):
                item.unlink()
            else:
                raise StudioError(f"迁移目标冲突: {destination}")
        else:
            item.rename(destination)
    source.rmdir()


def _profile_source_names(project: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    root = project / "用户档案"
    if not root.exists():
        return result
    for profile in root.iterdir():
        if not profile.is_dir():
            continue
        config = load_yaml(profile / PROFILE_CONFIG)
        source = config.get("source", {})
        if isinstance(source, dict) and source.get("name"):
            result[str(source["name"])] = profile.name
    return result


def inspect_project_v3(project: Path) -> dict[str, Any]:
    project = project.resolve()
    config = load_project_config(project, allow_legacy=True)
    locks = [path.relative_to(project).as_posix() for path in project.rglob(".session.lock")]
    legacy_input = project / "输入"
    source_names = _profile_source_names(project)
    preserved: list[dict[str, str]] = []
    deleted: list[str] = []
    unknown: list[str] = []
    if legacy_input.exists():
        for path in sorted(item for item in legacy_input.rglob("*") if item.is_file()):
            rel = path.relative_to(project).as_posix()
            input_rel = path.relative_to(legacy_input)
            if input_rel.as_posix() in {"原始StoryBible.md", "构筑状态.md"}:
                preserved.append({"source": rel, "target": f"构筑/{path.name}"})
            elif input_rel.parts and input_rel.parts[0] == "用户档案" and len(input_rel.parts) >= 3:
                preserved.append({"source": rel, "target": (Path("用户档案") / Path(*input_rel.parts[1:])).as_posix()})
            elif input_rel.parts and input_rel.parts[0] == "Profile草稿" and len(input_rel.parts) >= 3:
                preserved.append({"source": rel, "target": (Path("用户档案") / input_rel.parts[1] / "工作区" / Path(*input_rel.parts[2:])).as_posix()})
            elif input_rel.parts and input_rel.parts[0] in {"语料候选", "profile-migration"}:
                preserved.append({"source": rel, "target": (Path("构筑/候选") / input_rel).as_posix()})
            elif len(input_rel.parts) == 1 and path.name == "User开场候选草案.md" and len(source_names) == 1:
                only_profile = next(iter(source_names.values()))
                preserved.append({"source": rel, "target": f"用户档案/{only_profile}/工作区/{path.name}"})
            elif len(input_rel.parts) == 2 and input_rel.parts[0] == "用户档案" and path.suffix.lower() in {".json", ".yaml", ".yml", ".xyaml", ".md"}:
                matching = [profile_id for profile_id in set(source_names.values()) if profile_id in path.stem or load_yaml(project / "用户档案" / profile_id / PROFILE_CONFIG).get("display_name") in path.stem]
                if len(matching) == 1:
                    preserved.append({"source": rel, "target": f"用户档案/{matching[0]}/来源/{path.name}"})
                else:
                    unknown.append(rel)
            elif path.suffix.lower() == ".xyaml":
                preserved.append({"source": rel, "target": f"构筑/候选/{path.name}"})
            elif path.name in source_names:
                preserved.append({"source": rel, "target": f"用户档案/{source_names[path.name]}/来源/{path.name}"})
            elif SESSION_FILE_RE.match(path.name):
                deleted.append(rel)
            elif path.name == ".gitkeep":
                deleted.append(rel)
            else:
                unknown.append(rel)
    timeline = project / "故事" / "时间线"
    if timeline.exists():
        deleted.extend(path.relative_to(project).as_posix() for path in timeline.rglob("*") if path.is_file())
    for legacy in (project / "故事" / "独立测试", project / "故事" / "待编排片段"):
        if legacy.exists():
            deleted.extend(path.relative_to(project).as_posix() for path in legacy.rglob("*") if path.is_file())
    profiles = []
    root = project / "用户档案"
    if root.exists():
        for profile in sorted(item for item in root.iterdir() if item.is_dir() and (item / PROFILE_CONFIG).exists()):
            opening = profile / PROFILE_FILES["开场候选"]
            if opening.is_file():
                profile_config = load_yaml(profile / PROFILE_CONFIG)
                before = opening.read_text(encoding="utf-8")
                normalized = normalize_openings(before, str(profile_config.get("display_name", profile.name)))
                if normalized != before.strip() or profile_config.get("schema_version") != PROFILE_CONFIG_SCHEMA:
                    profiles.append(profile.name)
    legacy_roots = [project / "输入", project / "故事" / "时间线", project / "故事" / "独立测试", project / "故事" / "待编排片段"]
    legacy_directories = [path.relative_to(project).as_posix() for path in legacy_roots if path.exists()]
    changes_needed = config["schema_version"] != 3 or bool(legacy_directories or profiles) or "default_timeline" in config
    return {
        "status": "blocked" if locks or unknown else "ready" if changes_needed else "already_v3",
        "project": str(project),
        "source_schema_version": config["schema_version"],
        "target_schema_version": 3,
        "changes_needed": changes_needed,
        "legacy_directories": legacy_directories,
        "locks": locks,
        "unknown_input_files": unknown,
        "preserve_moves": preserved,
        "delete_files": sorted(set(deleted)),
        "profiles_to_normalize": profiles,
        "no_backup": True,
    }


def _normalize_profile(project: Path, profile: Path) -> dict[str, str]:
    config = load_yaml(profile / PROFILE_CONFIG)
    display_name = str(config.get("display_name", profile.name))
    opening_path = profile / PROFILE_FILES["开场候选"]
    before = opening_path.read_text(encoding="utf-8")
    normalized = normalize_openings(before, display_name)
    old_revision = str(config.get("current_revision", ""))
    if normalized == before.strip() and int(config.get("schema_version", 0)) == PROFILE_CONFIG_SCHEMA:
        return {"profile_id": profile.name, "from_revision": old_revision, "to_revision": old_revision}
    if old_revision:
        archive_current(project, profile.name)
    sections = {key: (profile / filename).read_text(encoding="utf-8").strip() for key, filename in PROFILE_FILES.items()}
    sections["开场候选"] = normalized
    payload = {
        "schema_version": 1,
        "profile_id": profile.name,
        "display_name": display_name,
        "aliases": config.get("aliases", []),
        "anchors": config.get("anchors", []),
        "sections": sections,
        "conflicts": [],
    }
    new_revision = revision_for(payload)
    atomic_write_text(opening_path, normalized)
    config["schema_version"] = PROFILE_CONFIG_SCHEMA
    config["current_revision"] = new_revision
    write_yaml(profile / PROFILE_CONFIG, config)
    return {"profile_id": profile.name, "from_revision": old_revision, "to_revision": new_revision}


def migrate_project_v3(project: Path, apply: bool = False) -> dict[str, Any]:
    project = project.resolve()
    report = inspect_project_v3(project)
    if not apply or report["status"] == "already_v3":
        return report
    if report["status"] == "blocked":
        details = report["locks"] + report["unknown_input_files"]
        raise StudioError("v3 迁移被阻塞:\n" + "\n".join(f"- {item}" for item in details))
    project = project.resolve()
    legacy_input = project / "输入"
    for move in report["preserve_moves"]:
        source = project / move["source"]
        target = project / move["target"]
        if not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _hash_file(source) != _hash_file(target):
                raise StudioError(f"迁移目标冲突: {target}")
            source.unlink()
        else:
            source.rename(target)
    if legacy_input.exists():
        shutil.rmtree(legacy_input)
    for legacy in (project / "故事" / "时间线", project / "故事" / "独立测试", project / "故事" / "待编排片段"):
        if legacy.exists():
            shutil.rmtree(legacy)
    for directory in (project / "构筑" / "候选", project / "会话", project / "故事" / "章节", project / "故事" / "片段", project / "素材" / "研究", project / "导出"):
        directory.mkdir(parents=True, exist_ok=True)
    normalized_profiles = []
    profiles_root = project / "用户档案"
    if profiles_root.exists():
        for name in report["profiles_to_normalize"]:
            normalized_profiles.append(_normalize_profile(project, profiles_root / name))
    config_path = project / "项目配置.yaml"
    config = load_yaml(config_path)
    config["schema_version"] = 3
    config.pop("default_timeline", None)
    write_yaml(config_path, config)
    status_label = {"building": "构筑中", "frozen": "已冻结", "revising": "修订中"}.get(config.get("bible_status"), "构筑中")
    construction = project / "构筑" / "构筑状态.md"
    if construction.exists():
        construction_text = construction.read_text(encoding="utf-8")
        if re.search(r"^- 阶段：.*$", construction_text, re.M):
            construction_text = re.sub(r"^- 阶段：.*$", f"- 阶段：{status_label}", construction_text, count=1, flags=re.M)
        else:
            construction_text = f"# 构筑状态\n\n## 当前阶段\n\n- 阶段：{status_label}\n\n" + construction_text
    else:
        construction_text = f"# 构筑状态\n\n## 当前阶段\n\n- 阶段：{status_label}\n\n## 已确认模块\n\n## 下一批问题\n\n## 有意留白"
    atomic_write_text(construction, construction_text)
    index_path = project / "00-项目索引.md"
    atomic_write_text(index_path, f"# {config.get('title', project.name)}\n\n## 当前入口\n\n- Story Bible：{status_label}\n- 构筑台账：[构筑/构筑状态.md](构筑/构筑状态.md)\n- 互动会话：[会话/](会话/)\n- 正式创作：[故事/](故事/)\n")
    manifest = {
        **report,
        "status": "applied",
        "normalized_profiles": normalized_profiles,
        "deleted_file_count": len(report["delete_files"]),
    }
    atomic_write_json(project / "构筑" / "迁移记录-v3.json", manifest)
    from studio_retrieval import save_index
    save_index(project)
    return manifest
