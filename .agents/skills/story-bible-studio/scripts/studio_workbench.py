"""Shared, workspace-confined application boundary for CLI, HTTP and MCP.

No model calls here. All narrative writes go through the existing event engine.
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

from studio_core import (CONFIG_FILE, EVENT_FILE, StudioError, LockError, add_variant,
                         atomic_write_json, atomic_write_text, create_session,
                         read_events, reduce_events, session_lock, safe_name,
                         load_yaml, project_events_through_turn, utc_now, normalize_motif_ids)
from studio_context import build_context, write_context
from studio_policy import config_view
from studio_profiles import list_profiles, list_openings
from studio_styles import list_styles, show_style, style_dependency
from studio_transactions import preparations, recoverable, runtime_folder, archive_preparation
from studio_requirements import read_requirements, save_requirements, check_requirements
from studio_recovery import recovery_input, operation_summary, last_restart
from studio_bible_workbench import BibleWorkbench, ticket_result


class Conflict(StudioError):
    pass


class StudioService(BibleWorkbench):
    def __init__(self, workspace: Path, projects_root: Path | None = None):
        self.workspace = workspace.resolve()
        self.projects_root = (projects_root or self.workspace / "作品").resolve()

    def child(self, root: Path, name: str) -> Path:
        if not isinstance(name, str) or not name or name in {".", ".."} or any(c in name for c in '/\\:'):
            raise StudioError("名称必须是单个目录名")
        path = (root / name).resolve()
        if path.parent != root.resolve() or not path.is_relative_to(self.workspace):
            raise StudioError("路径超出工作区边界")
        return path

    def locate(self, project: str, session: str | None = None):
        p = self.child(self.projects_root, project)
        if not (p / "项目配置.yaml").is_file():
            raise StudioError("作品不存在")
        if session is None:
            return p, None
        s = self.child(p / "会话", session)
        self.contained(s, p)
        self.contained(s / ".runtime", s)
        self.contained(s / ".workbench", s)
        for relative in (".runtime/current", ".runtime/variant-current", ".runtime/completed", ".workbench/requests", "检查点", "状态", "回复变体"):
            self.contained(s / relative, s)
        if not (s / CONFIG_FILE).is_file():
            raise StudioError("会话不存在")
        return p, s

    def version(self, p, s=None):
        paths = [p / "项目配置.yaml"]
        if s:
            paths += [s / CONFIG_FILE, s / EVENT_FILE,
                      s / ".runtime/current/transaction.json", s / ".runtime/variant-current/transaction.json",
                      s / ".runtime/current/response.md", s / ".runtime/variant-current/response.md"]
        stamp = []
        for path in paths:
            try:
                stat = path.stat()
                stamp.append((str(path), stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                stamp.append((str(path), None))
        return hashlib.sha256(json.dumps(stamp).encode()).hexdigest()

    def catalog_version(self, p):
        # Metadata only: no sibling event contents are opened.
        stamps = [(s.name, self.version(p, s)) for s in (p / "会话").glob("*")
                  if s.is_dir() and s.resolve().parent == (p / "会话").resolve() and (s / CONFIG_FILE).is_file()]
        return hashlib.sha256(json.dumps(sorted(stamps)).encode()).hexdigest()

    @staticmethod
    def pending(s):
        # Project navigation must never open sibling transaction inputs.
        return any((s / f".runtime/{folder}/{file}").exists()
                   for folder in ("current", "variant-current")
                   for file in ("transaction.json", "response.md"))

    def guard(self, p, s, expected_version, idle=True):
        if not expected_version or expected_version != self.version(p, s):
            raise Conflict("内容或配置已变化，请刷新后重试")
        if s and idle and self.pending(s):
            raise Conflict("本会话有待完成回合；请回到 Agent 完成处理后再修改")

    def cli(self, argv):
        from story_studio import build_parser, run
        try:
            return run(build_parser().parse_args(argv))
        except SystemExit as exc:
            raise StudioError("命令参数无效，请检查选项") from exc

    def projects(self):
        result = []
        if self.projects_root.is_dir():
            for path in self.projects_root.iterdir():
                if path.is_dir() and path.resolve().parent == self.projects_root and (path / "项目配置.yaml").is_file():
                    result.append({"id": path.name, "name": path.name})
        return sorted(result, key=lambda p: p["name"])

    def project(self, project):
        p, _ = self.locate(project)
        with session_lock(p):
            sessions = []
            for s in (p / "会话").glob("*"):
                if not s.is_dir() or s.resolve().parent != (p / "会话").resolve() or not (s / CONFIG_FILE).is_file():
                    continue
                # Navigation reads metadata only, never sibling story text.
                cfg = load_yaml(s / CONFIG_FILE)
                event = s / EVENT_FILE
                sessions.append({"id": s.name, "profile_id": cfg.get("profile_id"),
                                 "updated": event.stat().st_mtime if event.exists() else 0,
                                 "pending": self.pending(s)})
            profiles = [{k: row.get(k) for k in ("profile_id", "display_name", "status")}
                        for row in list_profiles(p)]
            return {"id": project, "version": self.version(p), "catalog_version": self.catalog_version(p), "sessions": sorted(sessions, key=lambda r: -r["updated"]),
                    "profiles": profiles, "config": config_view(p), "styles": list_styles(), "style": show_style(p),
                    "mode": load_yaml(p / "项目配置.yaml").get("session_defaults", {}).get("mode", "quality")}

    def openings(self, project, profile):
        p, _ = self.locate(project)
        self.child(p / "用户档案", profile)
        return list_openings(p, profile)

    def snapshot(self, project, session, before=None, limit=20):
        p, s = self.locate(project, session)
        with session_lock(s):
            state = reduce_events(read_events(s))
            turns = [int(k) for k in state["turns"] if before is None or int(k) < int(before)]
            keys = sorted(turns)[-max(1, min(int(limit), 50)):]
            rows = [{"turn": k, **state["turns"][str(k)], "selected": state["selected_variants"][str(k)]} for k in keys]
            cfg = load_yaml(s / CONFIG_FILE)
            return {"project": project, "session": session, "version": self.version(p, s),
                    "current_turn": state["current_turn"], "pending": self.pending(s), "can_configure": not self.pending(s) or recoverable(s),
                    "operations": operation_summary(self, p, s, read_events(s)), "last_restart": last_restart(s),
                    "requirements": self.requirements(project, session),
                    "turns": rows, "has_older": bool(keys and min(turns) < keys[0]),
                    "opening": state.get("opening"), "scene": state["scene"],
                    "checkpoints": state["checkpoints"], "config": config_view(p, s), "style": show_style(p, s),
                    "mode": cfg.get("mode", "quality"), "bible_revision": state.get("bible_revision"),
                    "profile_id": state.get("profile_id"), "last_event_id": state["last_event_id"]}

    def status(self, project, session):
        p, s = self.locate(project, session)
        if (s / ".session.lock").exists() or (p / ".session.lock").exists():
            return {"version": self.version(p, s), "pending": self.pending(s), "locked": True,
                    "can_configure": False, "catalog_version": self.catalog_version(p)}
        return {"version": self.version(p, s), "pending": self.pending(s), "locked": (s / ".session.lock").exists(),
                "requirements_version": read_requirements(s)["version"],
                "can_configure": not self.pending(s) or recoverable(s), "catalog_version": self.catalog_version(p)}

    def requirements(self, project, session, action="show", expected_version=None, items=None, prose=None, operation_id=None):
        p, s = self.locate(project, session)
        if action == "check":
            if not operation_id:
                raise StudioError("检查必须绑定 prepare 返回的操作 ID")
            operation = self.operation(project, session, operation_id)
            if operation["status"] != "ready":
                raise StudioError("只能检查有效 ready 事务的正文")
            return check_requirements(operation["requirements"], prose)
        with session_lock(s):
            if action in {"save", "undo"}:
                save_requirements(s, expected_version, items, undo=action == "undo")
            elif action != "show":
                raise StudioError("要求操作只能为 show/save/undo/check")
            value = read_requirements(s)
            ready = any((r["transaction"] or {}).get("status") == "ready" for r in preparations(s))
            return {"version": value["version"], "items": value["items"], "can_undo": value["undo"] is not None,
                    "after_current": ready}

    def requirements_view(self, project, session):
        return self.requirements(project, session)

    def operation_view(self, project, session, operation_id=None):
        return self.operation(project, session, operation_id)

    def session_diagnostic(self, project, session):
        p, s = self.locate(project, session)
        return {"version": self.version(p, s), "locked": (s / ".session.lock").exists() or (p / ".session.lock").exists(),
                "pending": self.pending(s), "next": "请交接 Agent 检查写入者及事务；保留文件，不按等待时长移除锁。"}

    def operation_edit(self, project, session, operation_id, action, expected_version, reason=None):
        if action not in {"archive", "finish"}:
            raise StudioError("面板管理操作只能为 archive/finish")
        return self.operation(project, session, operation_id, action, expected_version, reason)

    def request_status(self, project, session, request_id):
        p, s = self.locate(project, session)
        with session_lock(s):
            path = self.child(s / ".workbench/requests", request_id + ".json")
            req = json.loads(path.read_text(encoding="utf-8"))
            operation_id = (req.get("prepared") or {}).get("operation_id")
            if operation_id:
                event = next((e for e in read_events(s) if e["type"] in {"turn_committed", "variant_added"}
                              and e["payload"].get("operation_id") == operation_id), None)
                if event:
                    return {"id": request_id, "status": "committed", "turn": event["payload"]["turn"]}
                archive = self.child(s / ".runtime/archived", operation_id) / "archive.json"
                if archive.exists():
                    return {"id": request_id, "status": "archived"}
            for row in preparations(s):
                txn = row["transaction"] or {}
                if txn and (txn.get("operation_id") == operation_id or txn.get("request_id") == request_id):
                    return {"id": request_id, "status": txn["status"] if self.transaction_current(p, s, txn) else "stale"}
            return {"id": request_id, "status": "waiting" if req["expected_version"] == self.version(p, s) else "expired"}

    def configure(self, project, session, expected_version, pairs=None, reset=None, undo=False, mode=None, style=None):
        p, s = self.locate(project, session)
        if sum(bool(v) for v in (pairs, reset, undo, mode, style)) != 1:
            raise StudioError("每次只提交一种配置操作")
        with session_lock(p), session_lock(s or p):
            self.guard(p, s, expected_version, idle=not (s and recoverable(s)))
            locator = ["--project", str(p)] + (["--session", s.name] if s else [])
            if mode:
                result = self.cli(["mode", *locator, "--set", mode])
            elif style:
                result = self.cli(["style", "set", *locator, *(["--inherit"] if style == "inherit" else ["--preset", style])])
            else:
                args = ["config", *locator]
                if reset:
                    args += ["--reset", reset]
                elif undo:
                    args += ["--undo"]
                else:
                    if not isinstance(pairs, list) or not all(isinstance(v, str) for v in pairs):
                        raise StudioError("配置必须为 key=value 字符串列表")
                    for pair in pairs:
                        args += ["--set", pair]
                result = self.cli(args)
            return {"result": result, "version": self.version(p, s)}

    def preview(self, project, session, override):
        p, s = self.locate(project, session)
        with session_lock(s):
            return config_view(p, s, override=override)

    def new_session(self, project, session_id, expected_version, profile=None, opening=None, player="由用户指定"):
        p, _ = self.locate(project)
        self.child(p / "会话", session_id)
        if profile:
            self.child(p / "用户档案", profile)
        with session_lock(p):
            self.guard(p, None, expected_version)
            result = self.cli(["session", "new", "--project", str(p), "--id", session_id, "--player", player]
                              + (["--profile", profile] if profile else []) + (["--opening", opening] if opening else []))
            return {"session": Path(result).name}

    def select(self, project, session, turn, variant, expected_version):
        p, s = self.locate(project, session)
        with session_lock(s):
            self.guard(p, s, expected_version)
            if int(turn) != reduce_events(read_events(s))["current_turn"]:
                raise StudioError("历史回合请先从检查点创建分支，再选择版本")
            return self.cli(["variant", "select", "--project", str(p), "--session", session,
                             "--turn", str(turn), "--variant", variant])

    def checkpoint(self, project, session, checkpoint_id, expected_version, through_turn=None):
        p, s = self.locate(project, session)
        self.child(s / "检查点", checkpoint_id)
        with session_lock(s):
            self.guard(p, s, expected_version)
            path = s / "检查点" / f"{safe_name(checkpoint_id)}.json"
            if path.exists():
                raise StudioError("检查点名称已存在")
            return self.cli(["checkpoint", "create", "--project", str(p), "--session", session, "--id", checkpoint_id]
                            + (["--through-turn", str(through_turn)] if through_turn is not None else []))

    def branch(self, project, session, checkpoint_id, new_session, expected_version):
        p, s = self.locate(project, session)
        self.child(s / "检查点", checkpoint_id)
        self.child(p / "会话", new_session)
        with session_lock(p), session_lock(s):
            self.guard(p, s, expected_version)
            path = self.cli(["branch", "create", "--project", str(p), "--session", session,
                             "--checkpoint", checkpoint_id, "--new-session", new_session])
            return {"session": Path(path).name}

    def request(self, project, session, expected_version, user_text="", kind="continue", override=None,
                style_override=None, mode=None, query=""):
        p, s = self.locate(project, session)
        if kind not in {"continue", "regenerate"} or (kind == "continue" and not user_text.strip()):
            raise StudioError("请填写本轮输入，或选择重生成")
        with session_lock(s):
            self.guard(p, s, expected_version)
            config_view(p, s, override=override)
            state = reduce_events(read_events(s))
            if kind == "regenerate" and not state["current_turn"]:
                raise StudioError("还没有可重新生成的回合")
            rid = uuid.uuid4().hex
            directory = s / ".workbench/requests"
            directory.mkdir(parents=True, exist_ok=True)
            self.contained(directory, s)
            payload = {"id": rid, "project": project, "session": session, "kind": kind, "user_text": user_text,
                       "override": override, "style_override": style_override, "mode": mode, "query": query,
                       "expected_version": expected_version, "created_at": utc_now()}
            atomic_write_json(directory / f"{rid}.json", payload)
            cli = Path(__file__).with_name("story_studio.py")
            instruction = (f"继续使用 Story Bible Studio。先读取工作区 AGENTS.md 和技能 SKILL.md。\n"
                           f"目标作品：{project}；会话：{session}。只使用本会话上下文，不沿用其他任务的剧情记忆。\n"
                           f"执行工作台请求 {rid}。若已连接 MCP，调用 studio_prepare(project={project!r}, session={session!r}, request_id={rid!r})。\n"
                           f'未连接 MCP 时运行：python -B "{cli}" workbench --workspace "{self.workspace}" --operation prepare --payload-file "{directory / (rid + ".json")}"\n'
                           "只有 ready 才根据返回的上下文起草；需要压缩时按技能处理，再重新准备。\n"
                           "通过 studio_commit 或 workbench commit 提交正文与状态补丁；重生成只保存候选，不自动选择。\n"
                           "请求过期则停止并告知我刷新面板，不替换成另一个回合。")
            # CLI payload is separate from the durable request (no ambiguous field reuse).
            atomic_write_json(directory / f"{rid}-prepare.json", {"project": project, "session": session, "request_id": rid})
            instruction = instruction.replace(f'{rid}.json"', f'{rid}-prepare.json"')
            return ticket_result(self.workspace, directory / f"{rid}.json", instruction, id=rid, status="saved")

    def contained(self, path, root):
        if not path.resolve().is_relative_to(root.resolve()):
            raise StudioError("运行目录超出会话边界")

    def prepare(self, project, session, user_text=None, expected_version=None, request_id=None, regenerate=False, override=None,
                style_override=None, mode=None, query=""):
        p, s = self.locate(project, session)
        with session_lock(p), session_lock(s):
            req = None
            if request_id:
                path = self.child(s / ".workbench/requests", request_id + ".json")
                req = json.loads(path.read_text(encoding="utf-8"))
                user_text, override = req["user_text"], req.get("override")
                style_override, mode, query = req.get("style_override"), req.get("mode"), req.get("query", "")
                expected_version, regenerate = req["expected_version"], req["kind"] == "regenerate"
            payload = {"user_text": user_text, "override": override, "regenerate": regenerate}
            if style_override is not None or mode is not None or query:
                payload.update(style_override=style_override, mode=mode, query=query)
            folder = runtime_folder(s, regenerate)
            txn_path = folder / "transaction.json"
            txn = json.loads(txn_path.read_text(encoding="utf-8")) if txn_path.exists() else None
            history = read_events(s)
            retry_blocked = False
            metadata = {"request_payload": payload, "request_version": expected_version, "request_id": request_id}
            if txn:
                same_request = txn.get("request_payload") == payload and txn.get("request_id") == request_id
                if "request_payload" not in txn and not request_id:
                    original = recovery_input(s, {"transaction": txn, "regenerate": regenerate})
                    same_request = (original["user_text"].rstrip() == (user_text or "").rstrip()
                                    and original["override"] == override and original["style_override"] == style_override
                                    and original["mode"] == mode and original["query"] == query)
                same_request |= bool(req and (req.get("prepared") or {}).get("operation_id") == txn["operation_id"])
                valid_version = bool(expected_version) and expected_version in {txn.get("request_version"), self.version(p, s)}
                if not same_request or (not request_id and not valid_version):
                    raise Conflict("已有待完成事务；请查询原操作，或显式归档后更换请求")
                requirements_current = (txn.get("requirements") or {}).get("version", 0) == read_requirements(s)["version"]
                if self.transaction_current(p, s, txn, history) and (txn["status"] == "ready" or requirements_current):
                    if req and (req.get("prepared") or {}).get("operation_id") != txn["operation_id"]:
                        req["prepared"] = {"operation_id": txn["operation_id"]}
                        atomic_write_json(path, req)
                    return self.prepared_result(s, regenerate)
                if txn["status"] == "ready" or (folder / "response.md").exists():
                    raise Conflict("原事务已过期；请保留草稿并显式归档后重新准备")
                origin = next((i for i, e in enumerate(history) if e["event_id"] == txn["source_event_id"]), None)
                retry_blocked = origin is not None and all(e["type"] == "memory_compacted" for e in history[origin + 1:])
                if not retry_blocked:
                    raise Conflict("剧情已变化；请显式归档旧事务后重新准备")
                metadata["request_version"] = txn.get("request_version", expected_version)
                expected_version = self.version(p, s)
            elif req and req.get("prepared"):
                raise Conflict("请求已提交或归档；请查询原操作，不能再次追加回合")
            self.guard(p, s, expected_version, idle=not retry_blocked)
            if regenerate:
                state = reduce_events(history)
                turn = state["current_turn"]
                if not turn:
                    raise StudioError("没有可重新生成的回合")
                original = state["turns"][str(turn)]["user"]
                # Exclude the entire replaced turn, its memory and scene consequences.
                events = project_events_through_turn(history, turn - 1)
                packet, report = build_context(p, s, original, query=user_text or "", config_override=override,
                                              style_override=style_override, mode=mode, projected_events=events)
                if user_text:
                    packet += "\n\n## 用户对本次重生成的要求（不作为角色行动）\n\n" + user_text
                folder.mkdir(parents=True, exist_ok=True)
                operation = {"operation_id": uuid.uuid4().hex, "turn": turn, "source_event_id": state["last_event_id"],
                             "status": report["status"], "config_hash": hashlib.sha256((s / CONFIG_FILE).read_bytes()).hexdigest(),
                             "style_dependency": style_dependency(p, s, style_override), "style_override": style_override,
                             "requirements": report["requirements"], "output_config": report["output_config"], **metadata}
                atomic_write_text(folder / "context-packet.md", packet if report["status"] == "ready" else "上下文尚未就绪；不得起草。")
                atomic_write_json(folder / "report.json", report)
                atomic_write_json(txn_path, operation)
            else:
                if not user_text or not user_text.strip():
                    raise StudioError("用户输入不能为空")
                write_context(p, s, user_text, query=query, mode=mode, style_override=style_override,
                              config_override=override, request_metadata=metadata)
            result = self.prepared_result(s, regenerate)
            if request_id:
                req["prepared"] = {"operation_id": result["operation_id"]}
                atomic_write_json(path, req)
            return result

    @staticmethod
    def transaction_current(p, s, txn, events=None):
        return (txn["source_event_id"] == reduce_events(read_events(s) if events is None else events)["last_event_id"]
                and txn["config_hash"] == hashlib.sha256((s / CONFIG_FILE).read_bytes()).hexdigest()
                and txn.get("style_dependency") == style_dependency(p, s, txn.get("style_override")))

    def operation(self, project, session, operation_id=None, action="show", expected_version=None, reason=None):
        p, s = self.locate(project, session)
        with session_lock(p), session_lock(s):
            if action not in {"show", "archive", "finish"}:
                raise StudioError("操作只能为 show/archive/finish")
            if action != "show" and not operation_id:
                raise StudioError("管理操作必须指定操作 ID")
            rows = preparations(s)
            row = next((r for r in rows if r["transaction"] and
                        (operation_id is None or r["transaction"]["operation_id"] == operation_id)), None)
            if operation_id is None and row:
                operation_id = row["transaction"]["operation_id"]
            events = read_events(s)
            event = next((e for e in events if operation_id and e["type"] in {"turn_committed", "variant_added"}
                          and e["payload"].get("operation_id") == operation_id), None)
            if event:
                if action == "finish" and row:
                    self.guard(p, s, expected_version, idle=False)
                    self.cli(["session", "render", "--project", str(p), "--session", session])
                    self.finish_operation(s, operation_id, row["regenerate"])
                payload = event["payload"]
                return {"status": "committed", "operation_id": operation_id, "turn": payload["turn"],
                        "variant": payload["variant_id"], "needs_finish": bool(row) and action != "finish",
                        "version": self.version(p, s)}
            if operation_id:
                archive = self.child(s / ".runtime/archived", operation_id) / "archive.json"
                if archive.exists():
                    return {"status": "archived", **json.loads(archive.read_text(encoding="utf-8")), "version": self.version(p, s)}
            if row:
                txn, folder = row["transaction"], runtime_folder(s, row["regenerate"])
                recovery = recovery_input(s, row)
                if action == "archive":
                    self.guard(p, s, expected_version, idle=False)
                    target = archive_preparation(s, operation_id, reason, recovery)
                    return {"status": "archived", "operation_id": operation_id, "path": str(target),
                            "recovery": recovery, "version": self.version(p, s)}
                if action == "finish":
                    raise StudioError("正文尚未提交；请恢复原任务或保留草稿后重新开始")
                valid = self.transaction_current(p, s, txn, events)
                try:
                    result = self.prepared_result(s, row["regenerate"]) if valid else {
                        "status": "stale", "operation_id": operation_id, "context": None,
                        "next": "原上下文已过期；显式归档并使用原输入重新准备。"}
                except (OSError, ValueError, KeyError):
                    result = {"status": "stale", "operation_id": operation_id, "context": None,
                              "next": "上下文文件不完整；保留草稿并重新开始。"}
                drafts = {name: (folder / name).read_text(encoding="utf-8") for name in
                          ("response.md", "status.txt", "scene-patch.json", "state-updates.json") if (folder / name).is_file()}
                return {**result, "request_id": txn.get("request_id"), "requirements": txn.get("requirements") or {"version": 0, "items": []},
                        "recovery": recovery, "drafts": drafts, "has_draft": row["has_draft"], "version": self.version(p, s)}
            if rows and operation_id is None:
                return {"status": "damaged", "error": rows[0]["error"], "version": self.version(p, s)}
            if operation_id:
                raise StudioError("操作不存在于指定会话")
            return {"status": "idle", "version": self.version(p, s)}

    def recovery_ticket(self, project, session, operation_id=None):
        p, s = self.locate(project, session)
        if operation_id:
            safe_name(operation_id)
        directory = s / ".workbench/recovery"
        self.contained(directory, s)
        # A diagnostic ticket must remain available when a crashed process left a session lock.
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ((operation_id or "diagnostic") + ".json")
        atomic_write_json(path, {"project": project, "session": session, "operation_id": operation_id})
        cli = Path(__file__).with_name("story_studio.py")
        instruction = (f"使用 Story Bible Studio，先读取 AGENTS.md、技能 SKILL.md 和 workbench/agent-guide.md。\n"
                       f"恢复作品 {project!r} 的会话 {session!r}，操作 ID：{operation_id or '需要诊断'}。只读取该会话。\n"
                       f"MCP：studio_operation(project={project!r}, session={session!r}, operation_id={operation_id!r})。\n"
                       f'CLI：python -B "{cli}" workbench --workspace "{self.workspace}" --operation operation --payload-file "{path}"\n'
                       "ready：使用返回的原上下文和创作要求快照，复核已落盘草稿及状态补丁，再以原 operation_id 提交。没有草稿则按原请求起草。\n"
                       "needs_compaction/budget_blocked：按报告处理后，携带 recovery 中的原输入、覆盖及任务类型重新准备；不要另加用户回合。\n"
                       "committed：不重复正文；needs_finish 为真时调用 operation(action='finish', expected_version=返回版本, operation_id=原ID)。\n"
                       "stale/archived：停止旧任务并提示工作台重新开始，不转为提交当前其他操作。\n"
                       "损坏或锁占用：保留文件并诊断；仅确认写入者已停止后按既有修复流程处理，不根据等待时长删锁。\n"
                       "此小票只授权恢复与已提交操作的收尾；不得自动归档或选择回复变体。")
        return ticket_result(self.workspace, path, instruction, operation_id=operation_id)

    @staticmethod
    def prepared_result(s, variant=False):
        folder = s / (".runtime/variant-current" if variant else ".runtime/current")
        operation = json.loads((folder / "transaction.json").read_text(encoding="utf-8"))
        packet = folder / "context-packet.md" if variant else s / ".runtime/context-packet.md"
        report = json.loads((folder / "report.json" if variant else s / ".runtime/retrieval-report.json").read_text(encoding="utf-8"))
        return {"status": operation["status"], "operation_id": operation["operation_id"], "regenerate": variant,
                "requirements": operation.get("requirements") or {"version": 0, "items": []},
                "context": packet.read_text(encoding="utf-8") if operation["status"] == "ready" else None,
                "report": {k: report.get(k) for k in ("compaction", "actual_chars", "budget_chars", "mandatory_chars", "budget_overflow_chars", "section_chars", "excluded_for_budget", "missing_core_voice", "unresolved_characters")},
                "next": {"ready": "起草并复核后调用 studio_commit；不要另行追加用户回合。",
                         "needs_compaction": "先准备并审核压缩记忆，应用后使用同一请求重新 prepare。",
                         "budget_blocked": "按 section_chars 检查超限来源；可调整模式/配置后用同一请求重新 prepare，不能起草。"}[operation["status"]]}

    def commit(self, project, session, operation_id, prose, status="", scene_patch=None, state_updates=None, motifs=None, regenerate=False):
        p, s = self.locate(project, session)
        with session_lock(p), session_lock(s):
            events = read_events(s)
            previous = next((e for e in events if e["type"] in {"turn_committed", "variant_added"} and e["payload"].get("operation_id") == operation_id), None)
            if previous:
                previous_data = previous["payload"]
                candidate = {"prose": prose.rstrip(), "status": status.rstrip(), "scene_patch": scene_patch or {},
                             "state_updates": state_updates or {}, "motifs_used": normalize_motif_ids(motifs)}
                if (previous_data.get("output_config") or {}).get("status_bar", {}).get("enabled") is False:
                    candidate["status"] = ""
                if any(previous_data.get(k, {} if k in {"scene_patch", "state_updates"} else [] if k == "motifs_used" else "") != v for k, v in candidate.items()):
                    raise Conflict("相同提交 ID 的内容发生变化")
                self.cli(["session", "render", "--project", str(p), "--session", session])
                self.finish_operation(s, operation_id, regenerate)
                return {"status": "saved", "turn": previous_data["turn"], "variant": previous_data["variant_id"], "replayed": True,
                        "requirements_check": check_requirements(previous_data.get("requirements") or {}, prose)}
            folder = s / (".runtime/variant-current" if regenerate else ".runtime/current")
            self.contained(folder, s)
            if not (folder / "transaction.json").exists():
                raise Conflict("原操作已结束或归档；不能将旧回复提交为新任务")
            txn = json.loads((folder / "transaction.json").read_text(encoding="utf-8"))
            if txn["operation_id"] != operation_id or txn["status"] != "ready":
                raise Conflict("提交 ID 不匹配或上下文未就绪")
            if txn["source_event_id"] != reduce_events(events)["last_event_id"] or txn["config_hash"] != hashlib.sha256((s / CONFIG_FILE).read_bytes()).hexdigest():
                raise Conflict("上下文或设置已变化，保留草稿并重新准备")
            if txn.get("style_dependency") != style_dependency(p, s, txn.get("style_override")):
                raise Conflict("文风已变化，请重新准备")
            if not prose.strip():
                raise StudioError("正文不能为空")
            if txn.get("output_config", {}).get("status_bar", {}).get("enabled") is False:
                status = ""
            atomic_write_text(folder / "response.md", prose)
            atomic_write_text(folder / "status.txt", status)
            atomic_write_json(folder / "scene-patch.json", scene_patch or {})
            atomic_write_json(folder / "state-updates.json", state_updates or {})
            if regenerate:
                vid = add_variant(s, txn["turn"], prose, status, scene_patch, motifs, state_updates, operation_id,
                                  txn.get("output_config"), txn.get("requirements"))
                result = {"status": "saved", "turn": txn["turn"], "variant": vid, "selected": False}
            else:
                args = ["turn", "commit", "--project", str(p), "--session", session, "--operation-id", operation_id]
                for motif in motifs or []:
                    args += ["--motif-used", motif]
                result = self.cli(args)
            self.finish_operation(s, operation_id, regenerate)
            result["requirements_check"] = check_requirements(txn.get("requirements") or {}, prose)
            return result

    @staticmethod
    def finish_operation(s, operation_id, regenerate):
        folder = s / (".runtime/variant-current" if regenerate else ".runtime/current")
        txn_path = folder / "transaction.json"
        if txn_path.exists() and json.loads(txn_path.read_text(encoding="utf-8"))["operation_id"] == operation_id:
            # Archive only our prepared operation; never discard another writer's files.
            target = s / ".runtime/completed" / operation_id
            target.parent.mkdir(parents=True, exist_ok=True)
            folder.rename(target)

    def memory(self, project, session, action="prepare", text=None, through_turn=None, expected_event_id=None):
        p, s = self.locate(project, session)
        with session_lock(s):
            if action == "prepare":
                from studio_core import prepare_memory
                return prepare_memory(s, through_turn)
            if action != "apply" or not text or through_turn is None or not expected_event_id:
                raise StudioError("应用记忆需要审核后的完整文本、through_turn 和 expected_event_id")
            from studio_core import apply_memory
            apply_memory(s, text, through_turn, expected_event_id=expected_event_id)
            return {"status": "applied", "next": "旧上下文已过期；使用原输入重新准备，不能继续旧草稿。"}

    def dispatch(self, operation, params):
        allowed = {"projects", "project", "openings", "snapshot", "status", "configure", "new_session", "select",
                   "checkpoint", "branch", "request", "prepare", "commit", "memory", "preview", "operation", "request_status",
                   "requirements", "requirements_view", "operation_view", "operation_edit", "recovery_ticket", "session_diagnostic",
                   "bible_view", "bible_edit", "bible_prepare", "bible_commit", "bible_operation", "ticket"}
        if operation not in allowed or not isinstance(params, dict):
            raise StudioError("未知操作或参数格式错误")
        return getattr(self, operation)(**params)
