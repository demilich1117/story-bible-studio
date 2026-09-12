from __future__ import annotations

from pathlib import Path

from studio_core import CONFIG_FILE, SESSION_SCHEMA, StudioError, load_yaml, validate_session
from studio_profiles import PROFILE_CONFIG, PROFILE_DIR, parse_openings, profile_snapshot, validate_profile


def validate(project: Path) -> list[str]:
    errors: list[str] = []
    required = ["项目配置.yaml", "00-项目索引.md", "构筑/原始StoryBible.md", "构筑/构筑状态.md", "StoryBible", "会话", "故事/章节", "故事/片段"]
    for rel in required:
        if not (project / rel).exists():
            errors.append(f"缺少 {rel}")
    config = load_yaml(project / "项目配置.yaml")
    from studio_styles import resolve_style
    try:
        resolve_style(project)
    except StudioError as exc:
        errors.append(str(exc))
    if int(config.get("schema_version", 0)) != 3:
        errors.append("项目 schema_version 必须为 3")
    if "default_timeline" in config:
        errors.append("项目配置不得包含 default_timeline")
    status = str(config.get("bible_status", ""))
    if status not in {"building", "frozen", "revising"}:
        errors.append(f"非法 bible_status: {status or '空'}")
    status_label = {"building": "构筑中", "frozen": "已冻结", "revising": "修订中"}.get(status)
    index = project / "00-项目索引.md"
    if status_label and index.exists() and f"Story Bible：{status_label}" not in index.read_text(encoding="utf-8"):
        errors.append("00-项目索引.md 与 bible_status 不同步")
    construction = project / "构筑" / "构筑状态.md"
    if status_label and construction.exists() and f"阶段：{status_label}" not in construction.read_text(encoding="utf-8"):
        errors.append("构筑/构筑状态.md 与 bible_status 不同步")
    time_display = config.get("time_display")
    if time_display is not None and (not isinstance(time_display, dict) or time_display.get("mode") not in {"modern", "in-world"}):
        errors.append("time_display.mode 必须是 modern 或 in-world")
    scope = config.get("story_scope", {})
    if scope and (not isinstance(scope, dict) or scope.get("mode") not in {"character-hub", "world-hub"}):
        errors.append("story_scope.mode 必须是 character-hub 或 world-hub")
    for forbidden in ("输入", "开场预设", "故事/时间线", "故事/独立测试", "故事/待编排片段"):
        if (project / forbidden).exists():
            errors.append(f"v3 项目不得包含 {forbidden}")
    profiles = project / PROFILE_DIR
    if profiles.exists():
        for profile in [path for path in profiles.iterdir() if path.is_dir() and (path / PROFILE_CONFIG).exists()]:
            errors.extend(validate_profile(project, profile.name))
            opening_path = profile / "开场候选.md"
            if opening_path.exists() and not parse_openings(opening_path.read_text(encoding="utf-8"))["candidates"]:
                errors.append(f"Profile {profile.name} 的开场候选尚未规范化")
    sessions = project / "会话"
    if sessions.exists():
        for session in [path for path in sessions.iterdir() if path.is_dir()]:
            session_config = load_yaml(session / CONFIG_FILE)
            try:
                resolve_style(project, session)
            except StudioError as exc:
                errors.append(f"会话 {session.name}: {exc}")
            if int(session_config.get("schema_version", 0)) != SESSION_SCHEMA:
                errors.append(f"会话 {session.name} schema_version 非法")
                continue
            profile_id = session_config.get("profile_id")
            revision = session_config.get("profile_revision")
            if bool(profile_id) != bool(revision):
                errors.append(f"会话 {session.name} 的 profile_id/profile_revision 必须同时存在")
            elif profile_id:
                try:
                    profile_snapshot(project, str(profile_id), str(revision))
                except StudioError as exc:
                    errors.append(f"会话 {session.name}: {exc}")
            errors.extend(f"会话 {session.name}: {item}" for item in validate_session(session))
    return errors
