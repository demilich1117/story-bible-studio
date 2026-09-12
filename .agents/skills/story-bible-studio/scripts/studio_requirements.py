"""Session-local author requirements, independent of narrative/config versions."""
import json
import re

from studio_core import StudioError, atomic_write_json, session_lock

FILE = "创作要求.json"


def validate_items(items):
    if not isinstance(items, list):
        raise StudioError("创作要求必须为列表")
    result, ids = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"id", "type", "text"}:
            raise StudioError("每条要求包含 id、type、text")
        ident, kind, text = item["id"], item["type"], item["text"]
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ident) or ident in ids:
            raise StudioError("要求 ID 无效或重复")
        if kind not in ("banned", "guidance") or not isinstance(text, str) or not text.strip():
            raise StudioError("要求类型为 banned/guidance，文本不能为空")
        ids.add(ident)
        result.append({"id": ident, "type": kind, "text": text.strip()})
    return result


def read_requirements(session):
    path = session / FILE
    if path.resolve() != session.resolve() / FILE:
        raise StudioError("创作要求路径超出会话边界")
    if not path.exists():
        return {"version": 0, "items": [], "undo": None}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] < 0:
            raise ValueError("无效版本")
        return {"version": value["version"], "items": validate_items(value["items"]),
                "undo": None if value.get("undo") is None else validate_items(value["undo"])}
    except (ValueError, KeyError, TypeError) as exc:
        raise StudioError("创作要求文件损坏；请保留原文件并修复") from exc


def requirements_snapshot(session):
    value = read_requirements(session)
    return {k: value[k] for k in ("version", "items")}


def save_requirements(session, expected_version, items=None, undo=False):
    with session_lock(session):
        current = read_requirements(session)
        if type(expected_version) is not int or expected_version != current["version"]:
            raise StudioError("创作要求已变化，请读取最新版本后合并；未覆盖已保存内容")
        if undo:
            if current["undo"] is None:
                raise StudioError("没有可撤销的创作要求修改")
            next_items, previous = current["undo"], None
        else:
            next_items, previous = validate_items(items), current["items"]
            if next_items == current["items"]:
                return current
        value = {"version": current["version"] + 1, "items": next_items, "undo": previous}
        atomic_write_json(session / FILE, value)
        return value


def render_requirements(snapshot):
    if not snapshot["items"]:
        return ""
    lines = ["以下是本会话作者要求；用户本轮明确要求优先。仅用于起草与冷复核，不进入剧情、状态或记忆。"]
    for item in snapshot["items"]:
        label = "禁词／短语（正文含对白按字面避开）" if item["type"] == "banned" else "自然语言要求"
        lines.append(f"- {label}：{json.dumps(item['text'], ensure_ascii=False)}")
    return "\n".join(lines)


def check_requirements(snapshot, prose):
    if not isinstance(prose, str):
        raise StudioError("检查正文必须为字符串")
    hits = [{"id": item["id"], "text": item["text"], "count": prose.count(item["text"])}
            for item in snapshot.get("items", []) if item["type"] == "banned" and item["text"] in prose]
    return {"requirements_version": snapshot.get("version", 0), "hits": hits,
            "warning": "正文命中禁词；仅供复核，不自动替换或重写已提交正文。" if hits else None}
