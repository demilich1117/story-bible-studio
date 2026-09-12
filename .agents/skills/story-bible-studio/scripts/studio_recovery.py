"""Read-only recovery metadata. Decisions and mutations live in StudioService."""
import json

from studio_core import StudioError
from studio_transactions import runtime_folder, preparations


def recovery_input(session, row):
    txn = row["transaction"]
    payload, options = txn.get("request_payload") or {}, txn.get("prepare_options") or {}
    folder = runtime_folder(session, row["regenerate"])
    user_path = folder / "user.md"
    return {"operation_id": txn["operation_id"], "kind": "regenerate" if row["regenerate"] else "continue",
            "user_text": payload.get("user_text", options.get("query", "") if row["regenerate"] else
                                     (user_path.read_text(encoding="utf-8") if user_path.exists() else "")),
            "override": payload.get("override", txn.get("config_override")),
            "style_override": payload.get("style_override", txn.get("style_override")),
            "mode": payload.get("mode", options.get("mode")), "query": payload.get("query", options.get("query", ""))}


def last_restart(session):
    root = session / ".runtime/archived"
    if root.resolve() != session.resolve() / ".runtime/archived":
        raise StudioError("归档路径超出会话边界")
    for path in sorted(root.glob("*/archive.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True):
        if not path.resolve().is_relative_to(root.resolve()):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("recovery"):
                return data["recovery"]
        except (ValueError, OSError):
            continue
    return None


def operation_summary(service, p, s, events):
    result = []
    for row in preparations(s):
        txn = row["transaction"]
        if not txn:
            result.append({"status": "damaged", "error": row["error"], "has_draft": row["has_draft"],
                           "kind": "regenerate" if row["regenerate"] else "continue"})
            continue
        event = next((e for e in events if e["type"] in ("turn_committed", "variant_added")
                      and e["payload"].get("operation_id") == txn["operation_id"]), None)
        status = "committed" if event else txn["status"] if service.transaction_current(p, s, txn, events) else "stale"
        result.append({"status": status, "operation_id": txn["operation_id"], "has_draft": row["has_draft"],
                       "kind": "regenerate" if row["regenerate"] else "continue",
                       "requirements_version": (txn.get("requirements") or {}).get("version", 0)})
    return result
