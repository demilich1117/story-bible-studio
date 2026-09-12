from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment dependent
    raise RuntimeError("Story Bible Studio v3 requires PyYAML>=6.0") from exc


SESSION_SCHEMA = 3
EVENT_FILE = "events.jsonl"
SNAPSHOT_FILE = "session_state.json"
CONFIG_FILE = "会话配置.yaml"
LOG_FILE = "逐轮记录.md"
SCENE_FILE = "当前场景.md"
MEMORY_FILE = "压缩记忆.md"
OPENING_FILE = "开场.md"

DEFAULT_TIME_DISPLAY = {
    "mode": "modern",
    "format": "YYYY MM DD 星期X HH：MM",
    "anchor": None,
}

STATE_FILES = [
    "当前局面.md",
    "累计剧情摘要.md",
    "人物与关系状态.md",
    "身体物品与环境连续性.md",
    "线索与未决问题.md",
]

AGENCY_RULES = {
    "short-rp": "不新增 user 的动作、对白、内心或决定；只承接用户明确写出的行为及外部结果，在需要 user 回应前自然停笔",
    "co-narrative": "可合理代写 user 角色的行动、对白与反应以推进剧情；不得违背已确立人设与用户明确禁区，用户最新输入永远优先",
    "user-driven": "只扮演 user 以外的角色与世界；不代写 user 角色的决定性言行、内心或长期决定，每轮结尾留给 user 反应空间",
}
AGENCY_BRIEF = {"co-narrative": "可代写 user", "user-driven": "不代写 user", "short-rp": "短 RP，不新增 user 言行"}

DEFAULT_PROSE = {"min_chars": 1000, "max_chars": 1200}

DEFAULT_STATUS_BAR = {
    "enabled": True,
    "fields": ["时间", "地点", "人物", "状态", "未决"],
}


def normalize_agency(value: Any) -> str:
    agency = str(value if value is not None else "co-narrative").strip()
    if agency not in AGENCY_RULES:
        raise StudioError(f"非法 agency: {agency!r}（可选: {', '.join(sorted(AGENCY_RULES))}）")
    return agency


def _optional_bound(value: Any, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StudioError(f"prose.{key} 必须是正整数或 null: {value!r}")
    return value


def normalize_prose(value: Any) -> dict[str, Any]:
    """正文字数目标；off/false 表示不限，映射中缺失的边界表示该方向不限。"""
    if value is None:
        return dict(DEFAULT_PROSE)
    if value is False or (isinstance(value, str) and value.strip().lower() in {"off", "none", "不限"}):
        return {"min_chars": None, "max_chars": None}
    if not isinstance(value, dict):
        raise StudioError("prose 必须是映射或 off")
    if set(value) - {"enabled", "min_chars", "max_chars"}:
        raise StudioError("未知 prose 子字段")
    if "enabled" in value and not isinstance(value["enabled"], bool):
        raise StudioError("prose.enabled 必须是布尔值")
    prose = {"min_chars": _optional_bound(value.get("min_chars"), "min_chars"),
             "max_chars": _optional_bound(value.get("max_chars"), "max_chars")}
    low, high = prose["min_chars"], prose["max_chars"]
    if low is not None and high is not None and low > high:
        raise StudioError(f"prose.min_chars 不能大于 max_chars: {low} > {high}")
    if value.get("enabled") is False:
        return {"min_chars": None, "max_chars": None}
    return prose


def normalize_status_bar(value: Any) -> dict[str, Any]:
    """正文后状态栏；enabled=false 时不写 status.txt。"""
    if value is None or isinstance(value, bool):
        return {"enabled": DEFAULT_STATUS_BAR["enabled"] if value is None else bool(value),
                "fields": list(DEFAULT_STATUS_BAR["fields"])}
    if not isinstance(value, dict):
        raise StudioError("status_bar 必须是映射或布尔值")
    if set(value) - {"enabled", "fields"}:
        raise StudioError("未知 status_bar 子字段")
    if "enabled" in value and not isinstance(value["enabled"], bool):
        raise StudioError("status_bar.enabled 必须是布尔值")
    fields = value.get("fields", DEFAULT_STATUS_BAR["fields"])
    if not isinstance(fields, list) or not fields or not all(isinstance(item, str) and item.strip() for item in fields):
        raise StudioError("status_bar.fields 必须是非空字符串列表")
    return {"enabled": bool(value.get("enabled", True)),
            "fields": [str(item).strip() for item in fields]}


def prose_range_text(prose: dict[str, Any]) -> str:
    low, high = prose.get("min_chars"), prose.get("max_chars")
    if low and high:
        return f"{low}-{high}"
    if low:
        return f"≥{low}"
    if high:
        return f"≤{high}"
    return "不限"


def status_bar_text(status_bar: dict[str, Any]) -> str:
    if not status_bar.get("enabled", True):
        return "关闭"
    return "/".join(status_bar["fields"])


def output_config_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    """当前解析后的回合输出配置；存进事件供下一轮 prepare 做变更对比。"""
    from studio_output import normalize_bilingual
    return {"agency": normalize_agency(config.get("agency")),
            "prose": normalize_prose(config.get("prose")),
            "status_bar": normalize_status_bar(config.get("status_bar")),
            "interaction_preset": config.get("interaction_preset", "regular"),
            "person": config.get("person"),
            "initiative": config.get("initiative", "inherit"),
            "span": config.get("span", "inherit"),
            "bilingual": normalize_bilingual(config.get("bilingual"))}


def render_output_spec(snapshot: dict[str, Any]) -> str:
    range_text = prose_range_text(snapshot["prose"])
    prose_part = f"正文目标约 {range_text} 字" if range_text != "不限" else "正文字数不限"
    agency = snapshot["agency"]
    return (f"{prose_part} · agency {agency}（{AGENCY_BRIEF[agency]}）· "
            f"状态栏 {status_bar_text(snapshot['status_bar'])}"
            f" · 预设 {snapshot.get('interaction_preset', 'regular')} · 人称 {snapshot.get('person') or '沿用'}"
            f" · 主动性 {snapshot.get('initiative', 'inherit')} · 跨度 {snapshot.get('span', 'inherit')}"
            f" · 双语 {'开' if snapshot.get('bilingual', {}).get('enabled') else '关'}")


def diff_output_config(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    changes = []
    if previous.get("agency") != current["agency"]:
        changes.append(f"agency {previous.get('agency')} → {current['agency']}")
    if previous.get("prose") != current["prose"]:
        changes.append(f"prose {prose_range_text(previous.get('prose') or {})} → {prose_range_text(current['prose'])}")
    if previous.get("status_bar") != current["status_bar"]:
        changes.append(f"状态栏 {status_bar_text(previous.get('status_bar') or {})} → {status_bar_text(current['status_bar'])}")
    for key in ("interaction_preset", "person", "initiative", "span", "bilingual"):
        if key in previous and previous[key] != current.get(key):
            changes.append(f"{key} {previous[key]} → {current.get(key)}")
    return changes

class StudioError(RuntimeError):
    pass


class LockError(StudioError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    value = value.strip()
    if not value or value in {".", ".."} or re.search(r'[<>:"/\\|?*]', value):
        raise ValueError(f"非法名称: {value!r}")
    return value


def ensure_new(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"目标已存在: {path}")


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content.rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise StudioError(f"配置必须是映射: {path}")
    return loaded


def resolve_time_display(project_config: dict[str, Any], session_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve project defaults and an optional session-specific override."""
    resolved = dict(DEFAULT_TIME_DISPLAY)
    project_value = project_config.get("time_display")
    if isinstance(project_value, dict):
        resolved.update(project_value)
    session_value = (session_config or {}).get("time_display")
    if isinstance(session_value, dict):
        override = dict(session_value)
        mode = override.pop("mode", None)
        resolved.update(override)
        if mode and mode != "inherit":
            resolved["mode"] = mode
    return resolved


def write_yaml(path: Path, value: dict[str, Any]) -> None:
    atomic_write_text(path, yaml.safe_dump(value, allow_unicode=True, sort_keys=False))


def write(path: Path, content: str) -> None:
    atomic_write_text(path, content)


def session_path(project: Path, session_id: str) -> Path:
    return project / "会话" / safe_name(session_id)


def parse_scalar(path: Path, key: str, default: str = "") -> str:
    data = load_yaml(path)
    value = data.get(key, default)
    return str(value) if value is not None else default


_held_session_locks: ContextVar[frozenset[str]] = ContextVar("studio_locks", default=frozenset())


@contextmanager
def session_lock(session: Path) -> Iterator[None]:
    # A transport may validate and call a core operation under the same lock.
    # Context-local ownership does not bypass locks held by other threads/processes.
    key = str(session.resolve())
    held = _held_session_locks.get()
    if key in held:
        yield
        return
    lock = session / ".session.lock"
    payload = json.dumps({"pid": os.getpid(), "created_at": utc_now()}, ensure_ascii=False)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise LockError(f"会话正被其他写入者占用: {lock}；确认无写入任务后可运行 session unlock --force") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        token = _held_session_locks.set(held | {key})
        try:
            yield
        finally:
            _held_session_locks.reset(token)
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def unlock_session(session: Path, force: bool = False) -> None:
    lock = session / ".session.lock"
    if not lock.exists():
        return
    if not force:
        raise LockError("拒绝移除锁；只有确认没有活动写入者后才能使用 --force")
    lock.unlink()


def read_events(session: Path) -> list[dict[str, Any]]:
    path = session / EVENT_FILE
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StudioError(f"事件日志第 {line_number} 行损坏: {exc}") from exc
        if not isinstance(event, dict):
            raise StudioError(f"事件日志第 {line_number} 行不是对象")
        expected = len(events) + 1
        if event.get("seq") != expected:
            raise StudioError(f"事件序号不连续: 期望 {expected}，得到 {event.get('seq')}")
        events.append(event)
    return events


def repair_trailing_event(session: Path) -> bool:
    path = session / EVENT_FILE
    raw_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if not raw_lines:
        return False
    try:
        json.loads(raw_lines[-1])
        return False
    except json.JSONDecodeError:
        valid = raw_lines[:-1]
        for index, raw in enumerate(valid, start=1):
            event = json.loads(raw)
            if event.get("seq") != index:
                raise StudioError("损坏不只发生在最后一行，拒绝自动修复")
        atomic_write_text(path, "\n".join(valid))
        rebuild_views(session)
        return True


def _append_event_unlocked(session: Path, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    events = read_events(session)
    event = {
        "schema_version": SESSION_SCHEMA,
        "seq": len(events) + 1,
        "event_id": str(uuid.uuid4()),
        "type": event_type,
        "created_at": utc_now(),
        "payload": payload,
    }
    path = session / EVENT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return event


def merge_scene(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_scene(merged[key], value)
        else:
            merged[key] = value
    return merged


def reduce_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "schema_version": SESSION_SCHEMA,
        "event_count": len(events),
        "current_turn": 0,
        "last_compressed_turn": 0,
        "current_scene": "opening",
        "scene": {},
        "motif_ledger": {},
        "memory": "# 压缩记忆",
        "continuity_state": {},
        "turns": {},
        "selected_variants": {},
        "checkpoints": {},
        "branch_from": None,
        "bible_revision": None,
        "memory_history": [],
        "memory_invalidated": False,
        "history_changes": [],
        "last_event_id": events[-1]["event_id"] if events else None,
    }
    for event in events:
        event_type = event.get("type")
        payload = event.get("payload", {})
        if event_type == "session_started":
            state.update({
                "project": payload.get("project"),
                "session_id": payload.get("session_id"),
                "profile_id": payload.get("profile_id"),
                "profile_revision": payload.get("profile_revision"),
                "opening": payload.get("opening"),
                "branch_from": payload.get("branch_from"),
                "bible_revision": payload.get("bible_revision"),
            })
        elif event_type == "turn_committed":
            turn = int(payload["turn"])
            variant_id = payload.get("variant_id", "v1")
            state["turns"][turn] = {
                "user": payload.get("user", ""),
                "variants": {
                    variant_id: {
                        "prose": payload.get("prose", ""),
                        "status": payload.get("status", ""),
                        "scene_patch": payload.get("scene_patch", {}),
                        "motifs_used": payload.get("motifs_used", []),
                        "state_updates": payload.get("state_updates", {}),
                        "output_config": payload.get("output_config"),
                    }
                },
            }
            state["selected_variants"][turn] = variant_id
            state["current_turn"] = max(state["current_turn"], turn)
        elif event_type == "variant_added":
            turn = int(payload["turn"])
            state["turns"][turn]["variants"][payload["variant_id"]] = {
                "prose": payload.get("prose", ""),
                "status": payload.get("status", ""),
                "scene_patch": payload.get("scene_patch", {}),
                "motifs_used": payload.get("motifs_used", []),
                "state_updates": payload.get("state_updates", {}),
                "output_config": payload.get("output_config"),
            }
        elif event_type == "variant_selected":
            changed_turn = int(payload["turn"])
            previous = state["selected_variants"].get(changed_turn)
            state["selected_variants"][changed_turn] = payload["variant_id"]
            if previous != payload["variant_id"]:
                if changed_turn < state["current_turn"]:
                    state["history_changes"].append({"turn": changed_turn, "selected_variant": payload["variant_id"],
                                                     "review_later_turns_through": state["current_turn"]})
                valid = [m for m in state["memory_history"] if m["through_turn"] < changed_turn]
                if len(valid) != len(state["memory_history"]):
                    state["memory_invalidated"] = True
                    state["memory_history"] = valid
                    state["last_compressed_turn"] = valid[-1]["through_turn"] if valid else 0
                    state["memory"] = valid[-1]["memory_text"] if valid else "# 压缩记忆"
        elif event_type == "memory_compacted":
            state["memory_history"].append({**payload, "event_id": event["event_id"]})
            state["memory_invalidated"] = False
            state["last_compressed_turn"] = int(payload["through_turn"])
            state["memory"] = payload["memory_text"]
            for filename, content in payload.get("state_updates", payload.get("timeline_updates", {})).items():
                if filename in STATE_FILES:
                    state["continuity_state"][filename] = content
        elif event_type == "scene_transition":
            state["current_scene"] = payload["scene_id"]
            state["scene"] = dict(payload.get("scene", {}))
        elif event_type == "bible_bound":
            state["bible_revision"] = payload["revision"]
        elif event_type == "checkpoint_created":
            state["checkpoints"][payload["checkpoint_id"]] = {
                "event_count": payload["event_count"],
                "through_turn": payload["through_turn"],
            }

    scene: dict[str, Any] = {}
    current_scene = "opening"
    motif_ledger: dict[str, dict[str, Any]] = {}
    state["continuity_state"] = {}
    valid_memory_ids = {m["event_id"] for m in state["memory_history"]}
    for event in events:
        payload = event.get("payload", {})
        if event.get("type") == "scene_transition":
            current_scene = payload["scene_id"]
            scene = dict(payload.get("scene", {}))
        elif event.get("type") == "memory_compacted" and event["event_id"] in valid_memory_ids:
            state["continuity_state"].update(payload.get("state_updates", payload.get("timeline_updates", {})))
        elif event.get("type") == "turn_committed":
            turn = int(payload["turn"])
            # Apply the selected replacement at its logical turn, not its append time.
            payload = state["turns"][turn]["variants"][state["selected_variants"][turn]]
            state["continuity_state"].update(payload.get("state_updates", {}))
            scene = merge_scene(scene, payload.get("scene_patch", {}))
            for raw_motif_id in payload.get("motifs_used", []):
                motif_id = str(raw_motif_id).strip()
                if not motif_id:
                    continue
                previous = motif_ledger.get(motif_id, {})
                uses_in_scene = int(previous.get("uses_in_scene", 0)) + 1 if previous.get("last_scene") == current_scene else 1
                motif_ledger[motif_id] = {
                    "last_turn": turn,
                    "last_scene": current_scene,
                    "uses_in_scene": uses_in_scene,
                }

    current_turn = int(state["current_turn"])
    for entry in motif_ledger.values():
        entry["status"] = (
            "cooling"
            if entry["last_scene"] == current_scene or int(entry["last_turn"]) == current_turn
            else "available"
        )
    state["scene"] = scene
    state["current_scene"] = current_scene
    state["motif_ledger"] = motif_ledger
    state["turns"] = {str(key): value for key, value in sorted(state["turns"].items())}
    state["selected_variants"] = {str(key): value for key, value in sorted(state["selected_variants"].items())}
    return state


def selected_turns(state: dict[str, Any]) -> list[dict[str, Any]]:
    selected = []
    for turn_key, turn_data in state["turns"].items():
        variant_id = state["selected_variants"][turn_key]
        variant = turn_data["variants"][variant_id]
        selected.append({
            "turn": int(turn_key),
            "user": turn_data["user"],
            "variant_id": variant_id,
            **variant,
        })
    return selected


def render_log(state: dict[str, Any]) -> str:
    lines = [f"# 会话记录：{state.get('session_id', '')}"]
    for turn_key, turn_data in state["turns"].items():
        lines += ["", render_turn_block(turn_key, turn_data, state["selected_variants"][turn_key])]
    return "\n".join(lines)


def render_turn_block(turn_key: str | int, turn_data: dict[str, Any], selected: str) -> str:
    lines = [f"## Turn {turn_key}", f"selected_variant: {selected}", "", "### User", turn_data["user"]]
    for variant_id, variant in turn_data["variants"].items():
        lines += ["", f"### Assistant {variant_id}", variant["prose"]]
        if variant.get("status"):
            lines += ["", "#### Status", variant["status"]]
    return "\n".join(lines)


SCENE_LABELS = [
    ("time", "时间"),
    ("location", "地点"),
    ("characters", "在场角色"),
    ("tension", "关系/张力"),
    ("positions", "姿态与位置"),
    ("continuity", "衣物、物品、伤势"),
    ("open_event", "即时未决事件"),
]


def display_value(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value or "")


def render_scene(state: dict[str, Any]) -> str:
    lines = ["# 当前场景", "", f"- 场景 ID：{state.get('current_scene', 'opening')}"]
    scene = state.get("scene", {})
    for key, label in SCENE_LABELS:
        lines.append(f"- {label}：{display_value(scene.get(key, ''))}")
    extras = {key: value for key, value in scene.items() if key not in {item[0] for item in SCENE_LABELS}}
    if extras:
        lines += ["", "## 其他状态", "", "```json", json.dumps(extras, ensure_ascii=False, indent=2), "```"]
    return "\n".join(lines)


def compact_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key != "turns"}


def rebuild_views(session: Path) -> dict[str, Any]:
    events = read_events(session)
    state = reduce_events(events)
    atomic_write_json(session / SNAPSHOT_FILE, compact_snapshot(state))
    atomic_write_text(session / LOG_FILE, render_log(state))
    atomic_write_text(session / SCENE_FILE, render_scene(state))
    atomic_write_text(session / MEMORY_FILE, state.get("memory", "# 压缩记忆"))
    opening = state.get("opening") or {}
    opening_text = opening.get("text", "") if isinstance(opening, dict) else ""
    atomic_write_text(session / OPENING_FILE, opening_text or "# 空白开场")
    for filename in STATE_FILES:
        content = state.get("continuity_state", {}).get(filename, f"# {filename[:-3]}")
        atomic_write_text(session / "状态" / filename, content)
    return state


def sync_committed_turn(session: Path, state: dict[str, Any], turn: int) -> None:
    atomic_write_json(session / SNAPSHOT_FILE, compact_snapshot(state))
    log_path = session / LOG_FILE
    block = render_turn_block(str(turn), state["turns"][str(turn)], state["selected_variants"][str(turn)])
    with log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n" + block + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    atomic_write_text(session / SCENE_FILE, render_scene(state))


def create_session(
    project: Path,
    session_id: str,
    player: str = "由用户指定",
    profile_id: str | None = None,
    opening_id: str | None = None,
    mode: str | None = None,
) -> Path:
    session_id = safe_name(session_id)
    session = session_path(project, session_id)
    project_config = load_yaml(project / "项目配置.yaml")
    if int(project_config.get("schema_version", 0)) != 3:
        raise StudioError("项目必须先迁移到 schema v3")
    profile_revision = None
    opening = None
    if profile_id:
        profile_id = safe_name(profile_id)
        profile_config = load_yaml(project / "用户档案" / profile_id / "档案配置.yaml")
        if not profile_config:
            raise StudioError(f"Profile 不存在: {profile_id}")
        if profile_config.get("status") != "ready":
            raise StudioError(f"Profile 尚未 ready: {profile_id}")
        profile_revision = str(profile_config.get("current_revision", ""))
        if not profile_revision:
            raise StudioError(f"Profile 缺少 current_revision: {profile_id}")
        player = str(profile_config.get("display_name", profile_id))
        if opening_id:
            from studio_profiles import resolve_opening
            opening = resolve_opening(project, profile_id, profile_revision, opening_id)
    elif opening_id:
        raise StudioError("--opening 必须与 --profile 一起使用")
    ensure_new(session)
    session.mkdir(parents=True)
    default_subagents = {
        "enabled": True,
        "model": "gpt-5.6-luna",
        "reasoning_effort": "medium",
        "cadence": "milestones",
        "write_policy": "read-only",
        "allow_nsfw_context": True,
    }
    default_memory_policy = {
        "soft_context_ratio": 0.75,
        "hard_context_ratio": 0.90,
        "max_uncompressed_turns": 16,
        "min_uncompressed_turns": 6,
        "compact_on_scene_transition": False,
    }
    raw_session_defaults = project_config.get("session_defaults", {})
    session_defaults = raw_session_defaults if isinstance(raw_session_defaults, dict) else {}
    raw_memory_policy = session_defaults.get("memory_policy", {})
    project_memory_policy = raw_memory_policy if isinstance(raw_memory_policy, dict) else {}
    from studio_policy import resolve_policy
    session_defaults = resolve_policy(session_defaults, mode)
    from studio_versions import freeze_snapshot
    bible_revision = freeze_snapshot(project)
    config = {
        "schema_version": SESSION_SCHEMA,
        "project": project.name,
        "session_id": session_id,
        "status": "active",
        "mode": session_defaults["mode"],
        "player_role": player,
        "profile_id": profile_id,
        "profile_revision": profile_revision,
        "agency": normalize_agency(session_defaults.get("agency")),
        "pov": "scene",
        "person": "third",
        "tense": "past",
        "style": {"mode": "inherit"},
        "nsfw_overlay": "inherit",
        "prose": normalize_prose(session_defaults.get("prose")),
        "status_bar": normalize_status_bar(session_defaults.get("status_bar")),
        "opening_id": opening_id,
        "opening_sha256": opening.get("sha256") if opening else None,
        "current_scene": "opening",
        "recent_turns": int(session_defaults.get("recent_turns", 5)),
        "compression_cadence": int(session_defaults.get("compression_cadence", 16)),
        "context_budget_chars": int(session_defaults.get("context_budget_chars", 80000)),
        "memory_policy": session_defaults["memory_policy"],
        "retrieval_top_k": session_defaults["retrieval_top_k"],
        "subagents": project_config.get("subagents", default_subagents),
    }
    from copy import deepcopy
    from studio_output import CONFIG_KEYS, resolve_output
    for key in CONFIG_KEYS | {"short_rp_overrides"}:
        if key in session_defaults:
            config[key] = deepcopy(session_defaults[key])
    if isinstance(config.get("prose"), dict):
        config["prose"].setdefault("min_chars", None)
        config["prose"].setdefault("max_chars", None)
    config["status_bar"] = normalize_status_bar(config.get("status_bar"))
    resolve_output(config)
    write_yaml(session / CONFIG_FILE, config)
    for dirname in ("回复变体", "检查点", ".runtime/current", "状态"):
        (session / dirname).mkdir(parents=True, exist_ok=True)
    with session_lock(session):
        _append_event_unlocked(session, "session_started", {
            "project": project.name,
            "session_id": session_id,
            "profile_id": profile_id,
            "profile_revision": profile_revision,
            "opening": opening,
            "branch_from": None,
            "bible_revision": bible_revision,
        })
        rebuild_views(session)
    return session


def normalize_motif_ids(values: list[str] | None) -> list[str]:
    result = []
    for raw_value in values or []:
        motif_id = str(raw_value).strip()
        if not motif_id:
            continue
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", motif_id):
            raise StudioError(f"回环梗 ID 必须使用 stable-kebab-case: {motif_id}")
        if motif_id not in result:
            result.append(motif_id)
    return result


def commit_turn(
    session: Path,
    user: str,
    prose: str,
    status: str = "",
    scene_patch: dict[str, Any] | None = None,
    motifs_used: list[str] | None = None,
    state_updates: dict[str, str] | None = None,
    operation_id: str | None = None,
    expected_event_id: str | None = None,
    writing_style: dict[str, Any] | None = None,
    output_config: dict[str, Any] | None = None,
    requirements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not user.strip() or not prose.strip():
        raise StudioError("用户输入和正文均不能为空")
    if scene_patch is not None and not isinstance(scene_patch, dict):
        raise StudioError("场景补丁必须为对象")
    validate_state_updates(state_updates)
    if output_config is not None and not output_config.get("status_bar", {}).get("enabled", True):
        status = ""
    request = {"user": user.rstrip(), "prose": prose.rstrip(), "status": status.rstrip(),
               "scene_patch": scene_patch or {}, "motifs_used": normalize_motif_ids(motifs_used),
               "state_updates": state_updates or {}}
    if writing_style is not None:
        request["writing_style"] = writing_style
    if output_config is not None:
        request["output_config"] = output_config
    if requirements is not None:
        request["requirements"] = requirements
    request_hash = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with session_lock(session):
        events = read_events(session)
        if operation_id:
            previous = next((e for e in events if e["type"] == "turn_committed" and e["payload"].get("operation_id") == operation_id), None)
            if previous:
                if previous["payload"].get("request_hash") != request_hash:
                    raise StudioError("同一 operation_id 的提交内容发生变化")
                rebuild_views(session)
                recovery_receipt = {
                    "status": "committed", "operation_id": operation_id,
                    "turn": previous["payload"]["turn"], "event_id": previous["event_id"]}
                if output_config is not None:
                    from studio_output import prose_metrics
                    recovery_receipt.update(prose_metrics(request["prose"], output_config))
                from studio_requirements import check_requirements
                recovery_receipt["requirements_check"] = check_requirements(requirements or {}, request["prose"])
                atomic_write_json(session / ".runtime/last-commit.json", recovery_receipt)
                reset_current_runtime(session)
                return previous
        state = reduce_events(events)
        if expected_event_id is not None and state["last_event_id"] != expected_event_id:
            raise StudioError("上下文已过期，请重新 prepare")
        turn = int(state["current_turn"]) + 1
        event = _append_event_unlocked(session, "turn_committed", {
            **request,
            "operation_id": operation_id,
            "request_hash": request_hash,
            "turn": turn,
            "variant_id": "v1",
        })
        rebuild_views(session)
        receipt = {"status": "committed", "operation_id": operation_id,
                   "turn": turn, "event_id": event["event_id"]}
        if output_config is not None:
            from studio_output import prose_metrics
            receipt.update(prose_metrics(request["prose"], output_config))
        from studio_requirements import check_requirements
        receipt["requirements_check"] = check_requirements(requirements or {}, request["prose"])
        atomic_write_json(session / ".runtime/last-commit.json", receipt)
        reset_current_runtime(session)
    return event


def reset_current_runtime(session: Path) -> None:
    current = (session / ".runtime" / "current").resolve()
    expected = (session.resolve() / ".runtime" / "current")
    if current != expected:
        raise StudioError("当前回合运行目录越界")
    if current.exists():
        shutil.rmtree(current)
    current.mkdir(parents=True)


def add_variant(
    session: Path,
    turn: int,
    prose: str,
    status: str = "",
    scene_patch: dict[str, Any] | None = None,
    motifs_used: list[str] | None = None,
    state_updates: dict[str, str] | None = None,
    operation_id: str | None = None,
    output_config: dict[str, Any] | None = None,
    requirements: dict[str, Any] | None = None,
) -> str:
    validate_state_updates(state_updates)
    if not prose.strip() or (scene_patch is not None and not isinstance(scene_patch, dict)):
        raise StudioError("变体正文不能为空且场景补丁必须为对象")
    with session_lock(session):
        events = read_events(session)
        if operation_id:
            previous = next((e for e in events if e["type"] == "variant_added" and e["payload"].get("operation_id") == operation_id), None)
            if previous:
                rebuild_views(session)
                return previous["payload"]["variant_id"]
        state = reduce_events(events)
        turn_data = state["turns"].get(str(turn))
        if not turn_data:
            raise StudioError(f"回合不存在: {turn}")
        existing = turn_data["variants"]
        variant_id = f"v{max(int(key[1:]) for key in existing) + 1}"
        _append_event_unlocked(session, "variant_added", {
            "turn": turn,
            "variant_id": variant_id,
            "prose": prose.rstrip(),
            "status": status.rstrip(),
            "scene_patch": scene_patch or {},
            "motifs_used": normalize_motif_ids(motifs_used),
            "state_updates": state_updates or {},
            "operation_id": operation_id,
            "output_config": output_config,
            "requirements": requirements,
        })
        rebuild_views(session)
    return variant_id


def select_variant(session: Path, turn: int, variant_id: str) -> None:
    with session_lock(session):
        state = reduce_events(read_events(session))
        turn_data = state["turns"].get(str(turn))
        if not turn_data or variant_id not in turn_data["variants"]:
            raise StudioError(f"回合 {turn} 不存在变体 {variant_id}")
        _append_event_unlocked(session, "variant_selected", {"turn": turn, "variant_id": variant_id})
        rebuild_views(session)


def create_checkpoint(session: Path, checkpoint_id: str, through_turn: int | None = None) -> Path:
    checkpoint_id = safe_name(checkpoint_id)
    with session_lock(session):
        target = session / "检查点" / f"{checkpoint_id}.json"
        if target.exists():
            raise StudioError("检查点名称已存在；请使用新名称保留原恢复点")
        events = read_events(session)
        state = reduce_events(events)
        target_turn = int(state["current_turn"]) if through_turn is None else int(through_turn)
        if target_turn < 0 or target_turn > int(state["current_turn"]):
            raise StudioError(f"检查点回合超出范围: {target_turn}")
        event = _append_event_unlocked(session, "checkpoint_created", {
            "checkpoint_id": checkpoint_id,
            "source_event_count": len(events),
            "event_count": len(events),
            "through_turn": target_turn,
        })
        checkpoint = {
            "schema_version": SESSION_SCHEMA,
            "checkpoint_id": checkpoint_id,
            "source_session": state.get("session_id"),
            "source_event_count": len(events),
            "through_turn": target_turn,
            "created_by_event": event["event_id"],
        }
        atomic_write_json(target, checkpoint)
        rebuild_views(session)
    return target


def project_events_through_turn(events: list[dict[str, Any]], through_turn: int) -> list[dict[str, Any]]:
    projected = []
    cursor = 0
    for event in events:
        event_type = event.get("type")
        payload = event.get("payload", {})
        include = False
        if event_type == "session_started":
            include = True
        elif event_type == "turn_committed":
            cursor = max(cursor, int(payload["turn"]))
            include = int(payload["turn"]) <= through_turn
        elif event_type in {"variant_added", "variant_selected"}:
            include = int(payload["turn"]) <= through_turn
        elif event_type == "memory_compacted":
            include = int(payload["through_turn"]) <= through_turn
        elif event_type in {"scene_transition", "bible_bound"}:
            include = int(payload.get("at_turn", cursor)) <= through_turn
        if include:
            projected.append(event)
    return projected


def branch_session(project: Path, source_id: str, checkpoint_id: str, new_session_id: str) -> Path:
    source = session_path(project, source_id)
    checkpoint_path = source / "检查点" / f"{safe_name(checkpoint_id)}.json"
    if not checkpoint_path.exists():
        raise StudioError(f"检查点不存在: {checkpoint_path}")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    source_events = project_events_through_turn(read_events(source)[:int(checkpoint["source_event_count"])], int(checkpoint["through_turn"]))
    target = session_path(project, new_session_id)
    ensure_new(target)
    target.mkdir(parents=True)
    config = load_yaml(source / CONFIG_FILE)
    config["session_id"] = safe_name(new_session_id)
    config["status"] = "active"
    write_yaml(target / CONFIG_FILE, config)
    from studio_requirements import requirements_snapshot, FILE as REQUIREMENTS_FILE
    with session_lock(source):
        requirements = requirements_snapshot(source)
    if requirements["items"]:
        atomic_write_json(target / REQUIREMENTS_FILE, {"version": 1, "items": requirements["items"], "undo": None})
    for dirname in ("回复变体", "检查点", ".runtime/current", "状态"):
        (target / dirname).mkdir(parents=True, exist_ok=True)
    copied = []
    for index, event in enumerate(source_events, start=1):
        clone = json.loads(json.dumps(event, ensure_ascii=False))
        clone["seq"] = index
        if clone["type"] == "session_started":
            clone["payload"]["session_id"] = new_session_id
            clone["payload"]["branch_from"] = {
                "session": source_id,
                "checkpoint": checkpoint_id,
                "through_turn": checkpoint["through_turn"],
            }
        copied.append(json.dumps(clone, ensure_ascii=False, separators=(",", ":")))
    atomic_write_text(target / EVENT_FILE, "\n".join(copied))
    with session_lock(target):
        _append_event_unlocked(target, "branch_created", {
            "source_session": source_id,
            "checkpoint_id": checkpoint_id,
            "through_turn": checkpoint["through_turn"],
        })
        rebuild_views(target)
    return target


def prepare_memory(session: Path, through_turn: int | None = None, mode: str | None = None) -> dict[str, Any]:
    state = reduce_events(read_events(session))
    start = int(state["last_compressed_turn"]) + 1
    from studio_policy import resolve_policy
    config = resolve_policy(load_yaml(session / CONFIG_FILE), mode)
    end = int(state["current_turn"]) - int(config["recent_turns"]) if through_turn is None else through_turn
    if end < start or end > int(state["current_turn"]):
        raise StudioError("没有可压缩的较早回合；必要时显式指定 through-turn，勿反复压缩相同范围")
    turns = [item for item in selected_turns(state) if start <= item["turn"] <= end]
    candidate = {
        "schema_version": SESSION_SCHEMA,
        "task": "memory_and_continuity_patch",
        "mode": "advisory_only",
        "session": state.get("session_id"),
        "from_turn": start,
        "through_turn": end,
        "turns": turns,
        "source_event_id": state["last_event_id"],
        "selected_variants": {str(t["turn"]): t["variant_id"] for t in turns},
        "previous_memory": state["memory"],
        "current_state": state["continuity_state"],
        "scene": state["scene"],
        "output_contract": "返回完整记忆；区分保留、更新与已解决事实，保留未决承诺及来源；不得把当前状态回填为较早回合事实",
        "required_memory_sections": ["已发生因果", "关系变化与承诺", "知情范围与秘密", "未决目标", "连续性"],
        "compression_rules": [
            "只保存已经发生的事实、选择、承诺、后果和仍会影响后续的连续性",
            "最近几轮的重复频率、文风、粗口、烈度、体位或情绪不得自动升格为稳定人格与长期偏好",
            "单场变化与跨场景稳定机制分开；没有两个以上独立场景证据时，保留为本场状态或不记",
            "成人内容不把露骨度、动作力度、权力压迫、语言粗度和失控程度捆成同一个趋势",
        ],
        "forbidden_decisions": ["修改冻结正史", "选择回复变体", "决定用户角色意图", "直接写入文件"],
    }
    runtime = session / ".runtime"
    runtime.mkdir(exist_ok=True)
    cached = runtime / "memory-candidate.json"
    if cached.exists():
        existing = json.loads(cached.read_text(encoding="utf-8"))
        if existing == candidate:
            return existing
    atomic_write_json(runtime / "memory-candidate.json", candidate)
    return candidate


def validate_state_updates(state_updates):
    if state_updates is not None and not isinstance(state_updates, dict):
        raise StudioError("状态补丁必须是对象")
    for filename in (state_updates or {}):
        if filename not in STATE_FILES:
            raise StudioError(f"非法会话状态文件: {filename}")
        if not isinstance(state_updates[filename], str):
            raise StudioError("状态补丁必须是完整 Markdown 字符串")


def apply_memory(session: Path, memory_text: str, through_turn: int, state_updates: dict[str, str] | None = None, expected_event_id: str | None = None) -> None:
    validate_state_updates(state_updates)
    if not memory_text.strip():
        raise StudioError("压缩记忆不能为空")
    with session_lock(session):
        state = reduce_events(read_events(session))
        if expected_event_id and state["last_event_id"] != expected_event_id:
            raise StudioError("压缩候选已过期，请重新准备")
        if through_turn <= int(state["last_compressed_turn"]) or through_turn > int(state["current_turn"]):
            raise StudioError("压缩终点超出有效回合范围")
        _append_event_unlocked(session, "memory_compacted", {
            "through_turn": through_turn,
            "memory_text": memory_text.rstrip(),
            "state_updates": state_updates or {},
        })
        rebuild_views(session)


def transition_scene(session: Path, scene_id: str, scene: dict[str, Any]) -> None:
    with session_lock(session):
        state = reduce_events(read_events(session))
        _append_event_unlocked(session, "scene_transition", {"scene_id": safe_name(scene_id), "scene": scene, "at_turn": state["current_turn"]})
        rebuild_views(session)


def validate_session(session: Path) -> list[str]:
    errors: list[str] = []
    try:
        events = read_events(session)
        state = reduce_events(events)
    except Exception as exc:
        return [str(exc)]
    if not events or events[0].get("type") != "session_started":
        errors.append("首个事件必须是 session_started")
    event_ids = [event.get("event_id") for event in events]
    if len(event_ids) != len(set(event_ids)):
        errors.append("事件 ID 重复")
    allowed_types = {"session_started", "turn_committed", "variant_added", "variant_selected", "memory_compacted", "scene_transition", "checkpoint_created", "branch_created", "bible_bound"}
    for event in events:
        if event.get("schema_version") != SESSION_SCHEMA:
            errors.append(f"事件 {event.get('seq')} schema_version 非法")
        if event.get("type") not in allowed_types:
            errors.append(f"事件 {event.get('seq')} 类型未知: {event.get('type')}")
        if event.get("type") in {"turn_committed", "variant_added"}:
            motifs_used = event.get("payload", {}).get("motifs_used", [])
            invalid_motifs = (
                not isinstance(motifs_used, list)
                or any(not isinstance(item, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item) for item in motifs_used)
            )
            if invalid_motifs:
                errors.append(f"事件 {event.get('seq')} motifs_used 非法")
    turn_numbers = [int(key) for key in state["turns"]]
    if turn_numbers and turn_numbers != list(range(1, max(turn_numbers) + 1)):
        errors.append(f"回合不连续: {turn_numbers}")
    for turn_key, turn in state["turns"].items():
        selected = state["selected_variants"].get(turn_key)
        if selected not in turn["variants"]:
            errors.append(f"Turn {turn_key} 选择了不存在的变体 {selected}")
    snapshot_path = session / SNAPSHOT_FILE
    if snapshot_path.exists():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if snapshot.get("event_count") != len(events) or snapshot.get("last_event_id") != state.get("last_event_id"):
                errors.append("session_state.json 已过期，可运行 render 重建")
        except json.JSONDecodeError:
            errors.append("session_state.json 损坏，可运行 render 重建")
    else:
        errors.append("缺少 session_state.json")
    expected_views = {
        LOG_FILE: render_log(state).rstrip() + "\n",
        SCENE_FILE: render_scene(state).rstrip() + "\n",
        MEMORY_FILE: state.get("memory", "# 压缩记忆").rstrip() + "\n",
        OPENING_FILE: ((state.get("opening") or {}).get("text", "") or "# 空白开场").rstrip() + "\n",
    }
    for filename, expected in expected_views.items():
        path = session / filename
        if not path.exists():
            errors.append(f"缺少可读视图 {filename}")
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != expected:
            errors.append(f"{filename} 与事件日志不一致，可运行 render 重建")
    config = load_yaml(session / CONFIG_FILE)
    if int(config.get("schema_version", 0)) != SESSION_SCHEMA:
        errors.append(f"会话配置 schema_version 必须为 {SESSION_SCHEMA}")
    if config.get("project") and config.get("project") != session.parent.parent.name:
        errors.append("会话配置 project 与所在项目不一致")
    if config.get("session_id") != session.name:
        errors.append("会话配置 session_id 与目录名不一致")
    if bool(config.get("profile_id")) != bool(config.get("profile_revision")):
        errors.append("profile_id/profile_revision 必须同时存在")
    for filename in STATE_FILES:
        path = session / "状态" / filename
        expected = state.get("continuity_state", {}).get(filename, f"# {filename[:-3]}").rstrip() + "\n"
        if not path.exists():
            errors.append(f"会话缺少状态/{filename}")
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != expected:
            errors.append(f"状态/{filename} 与本会话事件日志不一致，可运行 render 重建")
    return errors


def git_milestone(project: Path, message: str) -> str:
    project = project.resolve()
    repo = next((candidate for candidate in (project, *project.parents) if (candidate / ".git").exists()), None)
    if repo is None:
        raise StudioError("未找到 Git 仓库")
    repo = repo.resolve()
    git_prefix = ["git", "-c", f"safe.directory={repo.as_posix()}"]
    try:
        staged = subprocess.run(git_prefix + ["diff", "--cached", "--name-only"], cwd=repo, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise StudioError(f"无法检查 Git 暂存区：{detail}") from exc
    if staged.stdout.strip():
        raise StudioError("检测到已有暂存内容；为避免混入用户改动，本次里程碑未提交")
    relative = project.relative_to(repo)
    try:
        subprocess.run(git_prefix + ["add", "--all", "--", relative.as_posix()], cwd=repo, capture_output=True, text=True, check=True)
        staged_after = subprocess.run(git_prefix + ["diff", "--cached", "--name-only"], cwd=repo, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        if "index.lock" in detail.lower() or "permission denied" in detail.lower():
            raise StudioError("Git 索引不可写；请取得 .git 写权限后重试里程碑") from exc
        raise StudioError(f"无法暂存项目里程碑：{detail}") from exc
    if not staged_after.stdout.strip():
        return "no_changes"
    commit = subprocess.run(git_prefix + ["commit", "-m", message, "--", relative.as_posix()], cwd=repo, capture_output=True, text=True, check=False)
    if commit.returncode:
        subprocess.run(git_prefix + ["restore", "--staged", "--", relative.as_posix()], cwd=repo, check=False)
        raise StudioError(commit.stderr.strip() or commit.stdout.strip())
    return commit.stdout.strip().splitlines()[0]
