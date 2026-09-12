"""Append-only construction history and compact, rebuildable projections.

This log belongs to the project, never to an RP session. Raw exchanges are
loaded by ID only; normal preparation reads the compact projection.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

from studio_core import StudioError, atomic_write_json, atomic_write_text, load_yaml, utc_now
from studio_versions import digest


def confined(project: Path, relative: str) -> Path:
    root = project.resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise StudioError("构筑路径超出作品边界")
    return path


def module_path(project, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise StudioError("模块必须是 StoryBible 内的 Markdown 相对路径")
    root = confined(project, "StoryBible").resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.suffix != ".md":
        raise StudioError("构筑修改必须位于 StoryBible 内的 Markdown 文件")
    return path


def legacy_topic(module):
    return "legacy-" + digest(module)


def baseline(project):
    path = confined(project, "构筑/decisions.json")
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"modules": {}, "operations": {}}
    if state.get("schema_version") == 2:
        return state
    state = deepcopy(state)
    state.update(schema_version=2, topics={}, records=[], prompts={})
    frozen = load_yaml(project / "项目配置.yaml").get("bible_status") == "frozen"
    files = [p.relative_to(project / "StoryBible").as_posix()
             for p in confined(project, "StoryBible").rglob("*.md")]
    for module in sorted(set(files) | state["modules"].keys()):
        module_path(project, module)
        old = state["modules"].get(module, {})
        tid = legacy_topic(module)
        state["topics"][tid] = {"id": tid, "module": module, "title": Path(module).stem,
            "status": old.get("status", "complete" if frozen else "open"),
            "decision": old.get("decision", "现有设定；原始讨论未记录。"),
            "open_questions": old.get("open_questions", []),
            "intentional_blanks": old.get("intentional_blanks", []),
            "version": 0, "source": "legacy", "depends_on": []}
    for oid, operation in state["operations"].items():
        if operation.get("status") != "complete":
            continue
        state["records"].append({"id": oid, "topic_id": legacy_topic(operation["module"]),
            "summary": operation.get("decision", "")[:180], "created_at": None,
            "kind": "legacy", "legacy_decision": operation.get("decision", ""),
            "status": operation.get("module_status", "open")})
    return state


def events(project):
    path = confined(project, "构筑/events.jsonl")
    if not path.exists():
        return
    with path.open("rb") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError) as exc:
                raise StudioError(f"构筑历史第 {number} 行不完整；保留文件并恢复原提交") from exc
            if event.get("schema_version") != 1 or not event.get("id"):
                raise StudioError("无法识别的构筑历史格式")
            yield event


def fold(state, event):
    data = event["data"]
    if event["kind"] == "baseline":
        state = deepcopy(data["state"])
    else:
        for key in ("topics", "modules", "operations", "prompts"):
            state.setdefault(key, {}).update(deepcopy(data.get(key, {})))
        if data.get("record"):
            record = deepcopy(data["record"])
            record.update(id=event["id"], created_at=event["created_at"], kind=event["kind"])
            state.setdefault("records", []).append(record)
    state["last_event_id"] = event["id"]
    return state


def read_state(project):
    log = confined(project, "构筑/events.jsonl")
    projection = confined(project, "构筑/decisions.json")
    if not log.exists() or not log.stat().st_size:
        return baseline(project)
    state = json.loads(projection.read_text(encoding="utf-8")) if projection.exists() else {}
    if (state.get("schema_version") == 2 and state.get("log_size") == log.stat().st_size
            and state.get("log_mtime_ns") == log.stat().st_mtime_ns):
        return state
    state = {}
    seen = set()
    for event in events(project):
        if event["id"] in seen:
            raise StudioError("构筑历史出现重复事件 ID")
        seen.add(event["id"])
        state = fold(state, event)
    state["log_size"] = log.stat().st_size
    state["log_mtime_ns"] = log.stat().st_mtime_ns
    return state


def read_event(project, event_id):
    for event in events(project):
        if event["id"] == event_id:
            return event
    raise StudioError("讨论记录不存在")


def render(project, state):
    status = load_yaml(project / "项目配置.yaml").get("bible_status")
    label = {"frozen": "已冻结", "revising": "修订中"}.get(status, "构筑中")
    lines = ["# 构筑状态", f"- 阶段：{label}", "- 问答粒度：关键选择互动，其余成组补全。"]
    for topic in state.get("topics", {}).values():
        if topic["status"] in {"superseded", "parked"}:
            continue
        lines.extend([f"\n## {topic['title']}｜{topic['status']}",
                      f"- 模块：{topic['module']}", topic.get("decision", ""),
                      "待决：" + "；".join(topic.get("open_questions", [])),
                      "有意留白：" + "；".join(topic.get("intentional_blanks", []))])
    atomic_write_json(confined(project, "构筑/decisions.json"), state)
    atomic_write_text(confined(project, "构筑/构筑状态.md"), "\n".join(lines))


def _append(project, event):
    """Retry an exact event, including a torn final write, without duplicating it."""
    path = confined(project, "构筑/events.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    wire = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if path.exists():
        with path.open("rb") as handle:
            for line in handle:
                try:
                    old = json.loads(line)
                except (ValueError, UnicodeError):
                    if handle.read(1) or not wire.startswith(line):
                        raise StudioError("构筑历史尾行损坏且与原提交不匹配；拒绝改写")
                    with path.open("ab") as output:
                        output.write(wire[len(line):])
                        output.flush()
                        os.fsync(output.fileno())
                    return
                if old["id"] == event["id"]:
                    if old != event:
                        raise StudioError("事件 ID 已被不同内容使用")
                    # A crash can leave valid JSON without its final newline.
                    if not line.endswith(b"\n"):
                        with path.open("ab") as output:
                            output.write(b"\n")
                            output.flush()
                            os.fsync(output.fileno())
                    return
            if path.stat().st_size and not line.endswith(b"\n"):
                raise StudioError("历史末行缺少换行，请先恢复原提交")
    with path.open("ab") as handle:
        handle.write(wire)
        handle.flush()
        os.fsync(handle.fileno())


def initialize(project):
    log = confined(project, "构筑/events.jsonl")
    if log.exists() and log.stat().st_size:
        return read_state(project)
    state = baseline(project)
    for name, backup in (("decisions.json", "decisions-v1.backup.json"), ("构筑状态.md", "原台账.md")):
        source = confined(project, "构筑/" + name)
        target = confined(project, "构筑/" + backup)
        if source.exists() and not target.exists():
            atomic_write_text(target, source.read_text(encoding="utf-8"))
    event = {"schema_version": 1, "id": "baseline-" + digest(state), "kind": "baseline",
            "created_at": None, "data": {"state": state}}
    _append(project, event)
    state = fold(state, event)
    state["log_size"] = log.stat().st_size
    state["log_mtime_ns"] = log.stat().st_mtime_ns
    render(project, state)
    return state


def append_event(project, event):
    log = confined(project, "构筑/events.jsonl")
    if not log.exists() or not log.stat().st_size:
        initialize(project)
    _append(project, event)
    # Always fold the authoritative log if the projection was interrupted.
    state = read_state(project)
    render(project, state)
    return state


def make_event(kind, data, event_id):
    return {"schema_version": 1, "id": event_id, "kind": kind, "created_at": utc_now(), "data": data}
