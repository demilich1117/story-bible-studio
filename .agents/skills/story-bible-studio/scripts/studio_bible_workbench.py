"""Project-only construction surface. No model calls or session retrieval."""
from __future__ import annotations

import os
import re
import shlex
import uuid
from copy import deepcopy
from pathlib import Path

from studio_core import StudioError, atomic_write_json, load_yaml, session_lock
from studio_versions import canon_payload, digest
from studio_construction import (commit_bible, current_prompt, operation_bible,
    prepare_bible, read_json, runtime_root)
from studio_construction_history import (append_event, confined, initialize, make_event,
    module_path, read_event, read_state)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9-]+", value):
        raise StudioError("非法构筑记录 ID")
    return value


def short_ticket(workspace, path):
    quote = (lambda s: "'" + str(s).replace("'", "''") + "'") if os.name == "nt" else lambda s: shlex.quote(str(s))
    cli = Path(__file__).with_name("story_studio.py")
    return (("需要诊断。" if path.name == "diagnostic.json" else "") + "用 Story Bible Studio 接手这张本地小票，按返回状态完成任务。\n"
            f"MCP：studio_ticket(ticket_path={str(path)!r})；未连接时执行：\n"
            f"python -B {quote(cli)} ticket --workspace {quote(workspace)} --file {quote(path)}")


def ticket_result(workspace, path, full, **extra):
    return {**extra, "ticket_path": str(path), "instruction": short_ticket(workspace, path), "full_instruction": full}


def compact_prepared(result):
    if "context" not in result:
        return result
    return {key: result.get(key) for key in ("status", "operation_id", "topic_id", "module",
            "prompt_id", "context", "context_chars", "omitted")}


class BibleWorkbench:
    def bible_continue(self, project, topic_id=None):
        """Read a bounded chat handoff, never the panel selection or RP state."""
        p, _ = self.locate(project)
        with session_lock(p):
            active = read_json(confined(p, '.runtime/bible/pending.json')) or read_json(confined(p, '.runtime/bible/prepare.json'))
            if active:
                recovered = operation_bible(p, operation_id=active['operation_id'])
                return {'status': 'recovery_required', 'operation_id': active['operation_id'],
                        'operation_status': recovered['status'], 'next': '通过 bible_operation 恢复原操作。'}
            state = read_state(p)
            cursor = state.get('conversation', {})
            selected = topic_id or cursor.get('topic_id')
            topic = state['topics'].get(selected)
            if topic_id and not topic:
                raise StudioError('主题不存在')
            if not topic_id and topic and topic['status'] in {'superseded', 'parked'}:
                topic = None
            if not topic:
                candidates = [t for t in state['topics'].values() if t['status'] in {'open', 'proposal'}]
                if len(candidates) == 1:
                    topic = candidates[0]
                else:
                    return {'status': 'needs_topic' if candidates else 'idle', 'project': project,
                            'candidates': [{k: t[k] for k in ('id', 'title', 'module', 'status')} for t in candidates[:12]],
                            'candidate_count': len(candidates)}
            return {'status': 'ready' if topic['status'] in {'open', 'proposal'} else topic['status'],
                    'project': project, 'topic_id': topic['id'], 'topic_version': topic['version'],
                    'title': topic['title'], 'module': topic['module'], 'decision': topic.get('decision', '')[:600],
                    'prompt': current_prompt(p, state, topic),
                    'open_questions': topic.get('open_questions', []),
                    'pending_fact_ids': topic.get('pending_fact_ids', []),
                    'dependency_hints': [{k: state['topics'][tid][k] for k in ('id', 'title', 'module')}
                                         for tid in topic.get('depends_on', []) if tid in state['topics']],
                    'needs_review': topic.get('needs_review', []), 'replaced_by': topic.get('replaced_by')}

    def bible_revise_prepare(self, project, topic_id, user_text, expected_version,
                             related=None, history_ids=None, budget=None, required_related=None):
        """Prepare an explicit chat revision without generating a handoff ticket."""
        p, _ = self.locate(project)
        with session_lock(p):
            if not isinstance(user_text, str) or not user_text.strip():
                raise StudioError('请保留本轮用户修订原文')
            key = digest({'topic_id': topic_id, 'version': expected_version, 'text': user_text})
            state = read_state(p)
            revision = next((t for t in state['topics'].values() if t.get('chat_revision_key') == key), None)
            if not revision:
                if read_json(confined(p, '.runtime/bible/pending.json')) or read_json(confined(p, '.runtime/bible/prepare.json')):
                    raise StudioError('已有构筑任务，请先恢复原操作')
                revision = self.bible_edit(project, 'revise', topic_id=topic_id,
                    expected_version=expected_version, text=user_text)['topic']
            else:
                active = read_json(confined(p, '.runtime/bible/prepare.json'))
                if (active and active.get('topic_id') == revision['id']
                        and active.get('seed', {}).get('user_text') == user_text
                        and read_json(confined(p, '.runtime/bible/pending.json'))):
                    return compact_prepared(operation_bible(p, operation_id=active['operation_id']))
                done = next(((oid, op) for oid, op in state['operations'].items()
                             if op.get('topic_id') == revision['id'] and op.get('status') == 'complete'), None)
                if done:
                    return {**done[1], 'status': 'committed', 'operation_id': done[0]}
            return self.bible_prepare(project, revision['id'], user_text, related=related,
                history_ids=history_ids, budget=budget, required_related=required_related)

    def bible_version(self, p):
        paths = [confined(p, "构筑/events.jsonl"), confined(p, "构筑/decisions.json"), p / "项目配置.yaml",
                 confined(p, ".runtime/bible/prepare.json"), confined(p, ".runtime/bible/pending.json")]
        paths.extend(confined(p, "StoryBible").rglob("*.md"))
        return digest([(str(path.relative_to(p)), path.stat().st_size, path.stat().st_mtime_ns)
                       for path in paths if path.exists()])

    def bible_view(self, project, topic_id=None, event_id=None, cursor=0, limit=20, version=None, request_id=None):
        p, _ = self.locate(project)
        with session_lock(p):
            current = self.bible_version(p)
            if version == current and not request_id:
                return {"version": current, "unchanged": True}
            state = read_state(p)
            active = read_json(confined(p, ".runtime/bible/pending.json")) or read_json(confined(p, ".runtime/bible/prepare.json"))
            pending = {"operation_id": active["operation_id"], "status": "pending" if "event" in active else "ready"} if active else None
            if request_id:
                path = confined(p, f".workbench/bible/requests/{identifier(request_id)}.json")
                req = read_json(path)
                if not req:
                    raise StudioError("构筑小票不存在")
                op = req.get("operation_id")
                status = operation_bible(p, operation_id=op)["status"] if op else req.get("last_status", "waiting")
                return {"version": current, "request_id": request_id, "operation_id": op, "status": status}
            if topic_id:
                topic = state["topics"].get(topic_id)
                if not topic:
                    raise StudioError("主题不存在")
                rows = [r for r in reversed(state["records"]) if r["topic_id"] == topic_id]
                start, size = max(0, int(cursor)), max(1, min(50, int(limit)))
                result = {"version": current, "topic": topic, "history": rows[start:start + size],
                          "next_cursor": start + size if start + size < len(rows) else None,
                          "prompt": current_prompt(p, state, topic), "pending": pending}
                path = module_path(p, topic["module"])
                result["canon"] = path.read_text(encoding="utf-8") if path.exists() else ""
                if event_id:
                    record = next((r for r in rows if r["id"] == event_id), None)
                    if not record:
                        raise StudioError("记录不属于该主题")
                    result["event"] = record if record["kind"] == "legacy" else read_event(p, event_id)
                return result
            topics = [{**{k: v for k, v in t.items() if k not in {"decision", "draft", "open_questions", "intentional_blanks"}},
                       "summary": t.get("decision", "")[:180], "questions_count": len(t.get("open_questions", []))}
                      for t in state["topics"].values()]
            modules = sorted(set(t["module"] for t in topics))
            return {"version": current, "project": project, "topics": topics, "modules": modules,
                    "prompts": list(state["prompts"].values()), "pending": pending,
                    "bible_status": load_yaml(p / "项目配置.yaml").get("bible_status", "building")}

    def bible_edit(self, project, action, topic_id=None, expected_version=None, title=None,
                   module=None, text="", kind="continue", prompt_id=None, related=None,
                   history_ids=None, budget=None, operation_id=None, required_related=None):
        p, _ = self.locate(project)
        runtime = runtime_root(p)
        with session_lock(p), session_lock(runtime):
            state = read_state(p)
            topic = deepcopy(state["topics"].get(topic_id)) if topic_id else None
            if topic_id and not topic:
                raise StudioError("主题不存在")
            if topic and (expected_version is None or topic.get("version", 0) != int(expected_version)):
                raise StudioError("主题已变化，请刷新后重试；你的草稿仍保留")
            if not isinstance(text, str):
                raise StudioError("内容必须是文字")
            pending_commit = read_json(runtime / "pending.json")
            if pending_commit and topic_id in pending_commit["event"]["data"].get("topics", {}) and action in {"draft", "park", "request", "revise"}:
                raise StudioError("此主题有提交待恢复，请先完成原提交；新输入仍可保留在草稿框")
            if action == "recovery":
                active = read_json(runtime / "pending.json") or read_json(runtime / "prepare.json")
                oid = operation_id or (active or {}).get("operation_id")
                if not oid:
                    raise StudioError("没有待恢复的构筑任务")
                return self._bible_ticket(p, project, {"ticket_type": "bible-recovery", "operation_id": identifier(oid)})
            if action == "archive":
                return operation_bible(p, "archive", operation_id, text)
            if action not in {"create", "draft", "park", "request", "revise"}:
                raise StudioError("未知构筑编辑操作")
            if action == "create":
                if not isinstance(title, str) or not title.strip() or len(title) > 120:
                    raise StudioError("请填写不超过 120 字的主题名称")
                module_path(p, module)
                topic = {"id": uuid.uuid4().hex, "title": title.strip(), "module": module,
                         "status": "open", "decision": text, "draft": text, "version": 1,
                         "depends_on": [], "source": "workbench", "open_questions": [], "intentional_blanks": []}
            elif action in {"draft", "park"}:
                if not topic or topic["status"] in {"complete", "superseded"}:
                    raise StudioError("已采用内容请使用提出修订")
                topic.update(version=topic.get("version", 0) + 1)
                if action == "draft":
                    topic.update(draft=text, decision=text)
                else:
                    topic["status"] = "parked"
            elif action in {"request", "revise"}:
                if action == 'revise':
                    kind = 'revise'
                if not topic or not text.strip() or kind not in {"continue", "revise"}:
                    raise StudioError("请选择主题并填写继续讨论或修订要求")
                if kind == "revise":
                    origin = topic["id"]
                    seen = set()
                    while topic.get("replaced_by"):
                        if topic["id"] in seen:
                            raise StudioError("修订关系出现循环")
                        seen.add(topic["id"])
                        topic = deepcopy(state["topics"][topic["replaced_by"]])
                    topic = {"id": uuid.uuid4().hex, "title": "修订 · " + topic["title"][:100],
                        "module": topic["module"], "status": "open", "decision": text, "draft": text,
                        "revises": topic["id"], "revises_version": topic.get("version", 0), "origin_topic_id": origin,
                        "version": 1, "depends_on": topic.get("depends_on", []), "needs_review": topic.get("needs_review", []),
                        "accepted_facts": deepcopy(topic.get('accepted_facts', [])),
                        "fact_application_tracking": topic.get('fact_application_tracking', False),
                        "pending_fact_ids": deepcopy(topic.get('pending_fact_ids', [])),
                        "rejected_directions": deepcopy(topic.get('rejected_directions', [])),
                        "source": "workbench", "open_questions": [], "intentional_blanks": topic.get("intentional_blanks", [])}
                    prompt_id = None
                    if action == 'revise':
                        topic['chat_revision_key'] = digest({'topic_id': topic_id, 'version': expected_version, 'text': text})
                else:
                    current_prompt(p, state, topic, prompt_id)
                if kind == "continue":
                    return self._bible_ticket(p, project, {"ticket_type": "bible", "topic_id": topic["id"],
                        "topic_version": topic.get("version", 0), "user_text": text, "prompt_id": topic.get("prompt_id"),
                        "related": related, "required_related": required_related, "history_ids": history_ids, "budget": budget,
                        "canon_signature": digest(canon_payload(p))})
            initialize(p)
            event = make_event("revision_request" if action in {"request", "revise"} else action,
                {"topics": {topic["id"]: topic}, "record": {"topic_id": topic["id"], "summary": text[:180],
                 "status": topic["status"]}, "transcript": {"user": text, "assistant": None}}, uuid.uuid4().hex)
            append_event(p, event)
            if action == "request":
                return self._bible_ticket(p, project, {"ticket_type": "bible", "topic_id": topic["id"],
                    "topic_version": topic["version"], "user_text": text, "prompt_id": None,
                    "related": related, "required_related": required_related, "history_ids": history_ids, "budget": budget,
                    "canon_signature": digest(canon_payload(p))})
            return {"status": "saved", "topic": topic, "version": self.bible_version(p)}

    def _bible_ticket(self, p, project, payload):
        rid = uuid.uuid4().hex
        path = confined(p, f".workbench/bible/requests/{rid}.json")
        atomic_write_json(path, {**payload, "id": rid, "project": project})
        full = ("使用 Story Bible Studio，首次接手读取 AGENTS.md、技能 SKILL.md 和构筑工作台参考。\n"
                + short_ticket(self.workspace, path) + "\n只处理该作品构筑主题，不读取 RP 会话。ready 才讨论；"
                "通过 bible_commit 保存实际展示的回答、决策和可选 next_prompt。候选不写正史；"
                "过期任务保留原输入并提示刷新，不替换成其他任务。pending 用原提交恢复。")
        return ticket_result(self.workspace, path, full, id=rid, topic_id=payload.get("topic_id"), status="waiting")

    def bible_prepare(self, project, topic_id=None, user_text=None, request_id=None, prompt_id=None,
                      related=None, history_ids=None, budget=None, required_related=None):
        p, _ = self.locate(project)
        with session_lock(p):
            req = None
            if request_id:
                path = confined(p, f".workbench/bible/requests/{identifier(request_id)}.json")
                req = read_json(path)
                if not req or req.get("ticket_type") != "bible":
                    raise StudioError("构筑小票不存在或类型不匹配")
                if req.get("operation_id"):
                    return compact_prepared(operation_bible(p, operation_id=req["operation_id"]))
                topic = read_state(p)["topics"].get(req["topic_id"], {})
                if topic.get("version", 0) != req["topic_version"] or digest(canon_payload(p)) != req["canon_signature"]:
                    raise StudioError("构筑小票已过期，请刷新并重新发起；不替换旧任务")
                topic_id, user_text, prompt_id = req["topic_id"], req["user_text"], req.get("prompt_id")
                related, history_ids = req.get("related"), req.get("history_ids")
                required_related = req.get('required_related')
                budget = budget if budget is not None else req.get("budget")
            result = prepare_bible(p, topic_id=topic_id, user_text=user_text, prompt_id=prompt_id,
                                   related=related, history_ids=history_ids, budget=budget, request_id=request_id,
                                   required_related=required_related)
            if req and result["status"] == "ready":
                req["operation_id"] = result["operation_id"]
                atomic_write_json(path, req)
            elif req:
                req["last_status"] = result["status"]
                atomic_write_json(path, req)
            return compact_prepared(result)

    def bible_commit(self, project, payload):
        p, _ = self.locate(project)
        if not isinstance(payload, dict):
            raise StudioError("构筑提交需要 JSON 对象")
        return commit_bible(p, payload)

    def bible_operation(self, project, action="show", operation_id=None, reason=None):
        p, _ = self.locate(project)
        return compact_prepared(operation_bible(p, action, operation_id, reason))

    def ticket(self, ticket_path, context_delivery='reference'):
        from studio_delivery import deliver
        if context_delivery not in {'reference', 'inline'}:
            raise StudioError('context_delivery 必须为 reference 或 inline')
        result = self._ticket_inline(ticket_path)
        path = Path(ticket_path).resolve()
        parts = path.relative_to(self.projects_root).parts
        p, s = self.locate(parts[0], parts[2] if len(parts) == 6 and parts[1] == '会话' else None)
        return deliver(s or p, result, context_delivery)

    def _ticket_inline(self, ticket_path):
        path = Path(ticket_path).resolve()
        if not path.is_relative_to(self.projects_root):
            raise StudioError("小票不属于当前工作区")
        parts = path.relative_to(self.projects_root).parts
        if len(parts) < 2:
            raise StudioError("小票路径无效")
        project = parts[0]
        self.locate(project)
        req = None
        if len(parts) == 5 and parts[1:4] == (".workbench", "bible", "requests"):
            req = read_json(path)
            if not req or req.get("project") != project or path.name != req.get("id", "") + ".json":
                raise StudioError("小票目标不匹配")
            if req.get("ticket_type") == "bible-recovery":
                return {"kind": "bible-recovery", **self.bible_operation(project, operation_id=req["operation_id"])}
            return {"kind": "bible", **self.bible_prepare(project, request_id=req["id"])}
        if len(parts) == 6 and parts[1] == "会话" and parts[3] == ".workbench" and parts[4] in {"requests", "recovery"}:
            session = parts[2]
            self.locate(project, session)
            req = read_json(path)
            if not req or req.get("project") != project or req.get("session") != session:
                raise StudioError("小票目标不匹配")
            if parts[4] == "recovery":
                return {"kind": "recovery", **self.operation(project, session, operation_id=req.get("operation_id"))}
            rid = req.get("request_id") or req.get("id")
            if path.name not in {str(rid) + ".json", str(rid) + "-prepare.json"}:
                raise StudioError("小票 ID 不匹配")
            return {"kind": "session", **self.prepare(project, session, request_id=rid)}
        raise StudioError("只接受工作台生成的构筑、会话或恢复小票")
