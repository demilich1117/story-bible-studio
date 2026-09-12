"""Content-addressed canon snapshots and explicit session upgrades."""
import hashlib
import json
import re

from studio_core import (CONFIG_FILE, StudioError, atomic_write_json, atomic_write_text,
                         load_yaml, read_events, reduce_events, rebuild_views,
                         session_lock, _append_event_unlocked, write_yaml)


def canon_payload(project):
    files = {p.relative_to(project).as_posix(): p.read_text(encoding="utf-8").rstrip() + "\n"
             for p in sorted((project / "StoryBible").rglob("*.md"))}
    config = load_yaml(project / "项目配置.yaml")
    return {"files": files, "config": {k: config.get(k) for k in
            ("default_style", "time_display", "story_scope", "nsfw")}}


def digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def character_registry(files):
    registry = {}
    for path, content in files.items():
        if not path.startswith("StoryBible/角色/"):
            continue
        name = path.rsplit("/", 1)[-1][:-3]
        title = re.search(r"^#\s+(.+)$", content, re.M)
        aliases = {name}
        if title:
            heading = title.group(1).strip()
            aliases.add(heading)
            # Only explicit nicknames in the name heading, never story-derived guesses.
            aliases.update(re.findall(r'[“‘\"]([^”’\"]+)[”’\"]', heading))
            aliases.add(re.sub(r'[“‘\"][^”’\"]+[”’\"]', '', heading).replace('  ', ' ').strip())
        for match in re.finditer(r"(?m)^-\s*(?:别名|昵称|aliases)[：:]\s*(.+)$", content):
            aliases.update(x.strip() for x in re.split(r"[,，、;；|]", match.group(1)))
        registry[name] = {"id": name, "path": path, "aliases": sorted(aliases - {""})}
    return registry


def freeze_snapshot(project):
    payload = canon_payload(project)
    revision = digest(payload)
    root = project / "正史版本" / revision
    manifest = root / "manifest.json"
    if manifest.exists():
        old = json.loads(manifest.read_text(encoding="utf-8"))
        if old["revision"] != revision or canon_payload(root) != payload:
            raise StudioError("正史快照内容不一致，拒绝覆盖")
        return revision
    for relative, content in payload["files"].items():
        atomic_write_text(root / relative, content)
    write_yaml(root / "项目配置.yaml", payload["config"])
    atomic_write_json(manifest, {"revision": revision, "registry": character_registry(payload["files"])})
    return revision


def snapshot_root(project, revision):
    if not re.fullmatch(r"[0-9a-f]{20}", revision or ""):
        raise StudioError("非法正史版本")
    root = project / "正史版本" / revision
    if not (root / "manifest.json").exists():
        raise StudioError(f"正史版本不存在: {revision}")
    if digest(canon_payload(root)) != revision:
        raise StudioError(f"正史版本被修改: {revision}")
    return root


def bind_baseline(project, session):
    with session_lock(session):
        state = reduce_events(read_events(session))
        if state.get("bible_revision"):
            return state["bible_revision"]
        revision = freeze_snapshot(project)
        _append_event_unlocked(session, "bible_bound", {
            "revision": revision, "at_turn": state["current_turn"],
            "reason": "升级时可确认的基线；不声明为历史开局版本"})
        rebuild_views(session)
        return revision


def upgrade_bible(project, session, apply=False, resolution=None):
    baseline = bind_baseline(project, session)
    new = freeze_snapshot(project)
    before = canon_payload(snapshot_root(project, baseline))["files"]
    after = canon_payload(snapshot_root(project, new))["files"]
    changed = [p for p in sorted(before.keys() | after.keys()) if before.get(p) != after.get(p)]
    state = reduce_events(read_events(session))
    report = {"from": baseline, "to": new, "changed_files": changed,
              "config_changed": canon_payload(snapshot_root(project, baseline))["config"] != canon_payload(snapshot_root(project, new))["config"],
              "scene": state["scene"], "memory": state["memory"],
              "continuity_state": state["continuity_state"],
              "status": "review_required" if baseline != new else "unchanged"}
    if apply and baseline != new:
        if not resolution or resolution.get("from") != baseline or resolution.get("to") != new or not resolution.get("reviewed") or resolution.get("conflicts"):
            raise StudioError("升级需要匹配版本的 reviewed 审查结果及已清零的 conflicts")
        with session_lock(session):
            if reduce_events(read_events(session))["last_event_id"] != state["last_event_id"]:
                raise StudioError("升级审查期间会话已变化")
            _append_event_unlocked(session, "bible_bound", {
                "revision": new, "at_turn": state["current_turn"], "reason": resolution.get("notes", "显式升级")})
            rebuild_views(session)
        report["status"] = "applied"
    return report
