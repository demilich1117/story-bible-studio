"""Session-local preparation inspection and explicit, non-destructive archival."""
import json
from pathlib import Path

from studio_core import StudioError, atomic_write_json, atomic_write_text, safe_name, session_lock, utc_now


def runtime_folder(session, regenerate=False):
    folder = session / ".runtime" / ("variant-current" if regenerate else "current")
    if folder.resolve() != session.resolve() / ".runtime" / folder.name:
        raise StudioError("运行目录超出会话边界")
    return folder


def preparations(session):
    result = []
    for regenerate in (False, True):
        folder = runtime_folder(session, regenerate)
        path = folder / "transaction.json"
        error = None
        try:
            txn = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            if txn is not None and (not isinstance(txn, dict) or not all(k in txn for k in
                    ("operation_id", "status", "source_event_id", "config_hash"))):
                raise ValueError("事务字段不完整")
        except (ValueError, OSError) as exc:
            txn, error = None, f"事务文件无法读取：{exc}"
        draft = (folder / "response.md").exists()
        if txn or draft or error:
            result.append({"regenerate": regenerate, "transaction": txn, "has_draft": draft,
                           "error": error or ("草稿缺少事务；请交接 Agent 保留文件并诊断" if not txn else None)})
    return result


def recoverable(session):
    active = preparations(session)
    return bool(active) and all(not row["has_draft"] and row["transaction"] and
                               row["transaction"]["status"] in {"budget_blocked", "needs_compaction"}
                               for row in active)


def archive_preparation(session, operation_id, reason, recovery=None):
    if not isinstance(reason, str) or not reason.strip():
        raise StudioError("归档需要明确原因；正文与事务将完整保留")
    safe_name(operation_id)
    with session_lock(session):
        target = session / ".runtime/archived" / operation_id
        if target.resolve() != session.resolve() / ".runtime/archived" / operation_id:
            raise StudioError("归档目录超出会话边界")
        if (target / "archive.json").is_file():
            return target
        row = next((row for row in preparations(session)
                    if (row["transaction"] or {}).get("operation_id") == operation_id), None)
        if row is None:
            raise StudioError("操作 ID 不属于当前待完成事务")
        folder = runtime_folder(session, row["regenerate"])
        # Ordinary prepare keeps its packet outside current; archive that evidence too.
        if not row["regenerate"]:
            for source, name in ((session / ".runtime/context-packet.md", "context-packet.md"),
                                 (session / ".runtime/retrieval-report.json", "report.json")):
                if source.exists():
                    atomic_write_text(folder / name, source.read_text(encoding="utf-8"))
        atomic_write_json(folder / "archive.json", {"operation_id": operation_id,
                          "reason": reason.strip(), "archived_at": utc_now(), "recovery": recovery})
        target.parent.mkdir(parents=True, exist_ok=True)
        folder.rename(target)
        return target
