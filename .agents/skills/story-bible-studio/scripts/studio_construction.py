"""Construction transactions shared by legacy CLI and the topic workbench."""
from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from pathlib import Path

from studio_core import StudioError, atomic_write_json, atomic_write_text, load_yaml, session_lock, write_yaml
from studio_versions import canon_payload, digest
from studio_construction_history import (append_event, confined, initialize, legacy_topic,
    make_event, module_path, read_event, read_state)

bible_path = module_path


def runtime_root(project):
    runtime = confined(project, ".runtime/bible")
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def read_ledger(project):
    state = read_state(project)
    pending = read_json(confined(project, ".runtime/bible/pending.json"))
    if pending and state["operations"].get(pending["operation_id"], {}).get("status") != "complete":
        state = deepcopy(state)
        state["operations"][pending["operation_id"]] = {**pending, "status": "pending"}
    return state


def string_list(value, label):
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise StudioError(f"{label} 必须是文字列表")
    return value


def options_map(value):
    if not isinstance(value, dict) or any(not isinstance(k, str) or not k.strip()
            or not isinstance(v, str) or not v.strip() for k, v in value.items()):
        raise StudioError("options 必须是编号到文字方案的映射")
    return value


def current_prompt(project, state, topic, prompt_id=None):
    current = topic.get("prompt_id")
    if prompt_id is not None and prompt_id != current:
        raise StudioError("题目版本已变化，不能把旧编号用于新问题")
    if not current:
        return None
    metadata = state["prompts"].get(current)
    if not metadata:
        raise StudioError("题目快照缺失")
    return read_event(project, metadata["event_id"])["data"]["prompt"]


def dependencies(project, topic, names, legacy=False):
    if legacy:
        return digest(canon_payload(project))
    files = {name: digest(module_path(project, name).read_text(encoding="utf-8"))
             if module_path(project, name).exists() else None for name in names}
    return digest({"files": files, "topic": topic,
                   "bible_status": load_yaml(project / "项目配置.yaml").get("bible_status")})


def prepare_bible(project, module=None, options=None, mode=None, related=None, *, topic_id=None,
                  user_text=None, prompt_id=None, history_ids=None, budget=None, request_id=None,
                  required_related=None):
    project = Path(project).resolve()
    runtime = runtime_root(project)
    with session_lock(project), session_lock(runtime):
        state = read_ledger(project)
        if any(op.get("status") == "pending" for op in state["operations"].values()):
            raise StudioError("存在未完成构筑提交，请先恢复原操作")
        modern = topic_id is not None
        if modern:
            topic = state["topics"].get(topic_id)
            if not topic:
                raise StudioError("构筑主题不存在")
            module = topic["module"]
            if not isinstance(user_text, str) or not user_text.strip():
                raise StudioError("请保留本轮用户原文")
        else:
            module_path(project, module)
            topic_id = legacy_topic(module)
            topic = state["topics"].get(topic_id, {"id": topic_id, "module": module,
                "title": Path(module).stem, "status": "open", "version": 0, "depends_on": []})
        config = load_yaml(project / "项目配置.yaml")
        if not modern and config.get("bible_status") == "frozen":
            raise StudioError("正史已冻结；显式 bible revise 后才能构筑")
        if not modern and state["modules"].get(module, {}).get("status") == "complete":
            raise StudioError("模块已完成；需要显式 bible revise --module 重新打开")
        prompt = current_prompt(project, state, topic, prompt_id) if modern else None
        mapping = options_map(prompt["options"] if prompt else (options or {}))
        related = string_list(related or [], "关联模块")
        required_related = string_list(required_related or [], "必需关联模块")
        history_ids = string_list(history_ids or [], "历史记录")
        if len(history_ids) > 3:
            raise StudioError("一次最多显式召回三条讨论")
        limit = budget if budget is not None else config.get("construction", {}).get("context_budget_chars", 20000)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1000:
            raise StudioError("构筑字符预算必须是至少 1000 的整数")
        seed = {"module": module, "topic_id": topic_id, "options": mapping, "mode": mode,
                "related": related, "user_text": user_text, "prompt_id": prompt_id,
                "history_ids": history_ids, "budget": limit, "request_id": request_id, "modern": modern}
        if required_related:
            seed["required_related"] = required_related
        active = read_json(runtime / "prepare.json")
        if active:
            if active.get("seed") == seed:
                if active["source_signature"] != dependencies(project, topic, active["dependency_names"], not modern):
                    raise StudioError("原构筑事务已过期，请保留草稿并归档后重新准备")
                return active
            raise StudioError("已有准备中的构筑任务；请恢复原任务或显式归档，不得覆盖")
        names = list(dict.fromkeys(["核心概念.md", module, *required_related]))
        missing = [name for name in required_related if not module_path(project, name).exists()]
        if missing:
            return {"status": "materials_blocked", "missing_required": missing,
                    "message": "必需关联材料缺失；补齐材料或明确调整本轮范围后再准备。"}
        materials = {name: module_path(project, name).read_text(encoding="utf-8")
                     for name in names if module_path(project, name).exists()}
        history = []
        for hid in history_ids:
            record = next((r for r in state["records"] if r["id"] == hid), None)
            allowed_topics = {topic_id, topic.get("revises"), topic.get("origin_topic_id")}
            if not record or record["topic_id"] not in allowed_topics:
                raise StudioError("只可显式读取当前主题的讨论记录")
            if record["kind"] == "legacy":
                history.append(record)
            else:
                historical = read_event(project, hid)["data"]
                history.append({"id": hid, "transcript": historical.get("transcript", {}),
                                "prompt": historical.get("prompt"), "summary": record["summary"]})
        packet = {"user_text": user_text, "topic": {k: deepcopy(v) for k, v in topic.items() if k not in {"draft", "source"}},
                  "prompt": prompt, "materials": materials, "history": history}
        if topic.get("depends_on"):
            packet["dependency_hints"] = [{"topic_id": tid, "module": state["topics"][tid]["module"],
                "title": state["topics"][tid]["title"], "needs_review": tid in topic.get("needs_review", [])}
                for tid in topic["depends_on"] if tid in state["topics"]]
        count = lambda: len(json.dumps(packet, ensure_ascii=False))
        omitted, used = [], list(names)
        for name in related:
            path = module_path(project, name)
            if name in materials:
                continue
            if not path.exists():
                omitted.append(name)
                continue
            materials[name] = path.read_text(encoding="utf-8")
            if count() > limit:
                materials.pop(name)
                omitted.append(name)
            else:
                used.append(name)
        if count() > limit:
            return {"status": "budget_blocked", "required_chars": count(), "budget_chars": limit,
                    "required_files": names, "omitted": omitted,
                    "message": "必要材料超限；显式提高本次预算或先整理该主题，不自动压缩。"}
        request = {"status": "ready", "operation_id": str(uuid.uuid4()), "seed": seed,
                   "module": module, "topic_id": topic_id, "modern": modern, "mode": mode or "quality",
                   "options": mapping, "prompt_id": prompt["id"] if prompt else None, "request_id": request_id,
                   "source_signature": dependencies(project, topic, used, not modern), "dependency_names": used,
                   "materials": materials, "decisions": {module: {k: topic.get(k) for k in
                        ("status", "decision", "open_questions", "intentional_blanks")}},
                   "context": packet, "context_chars": count(), "omitted": omitted}
        atomic_write_json(runtime / "prepare.json", request)
        return request


def selected_keys(choice, mapping):
    keys = re.split(r"[+、,，\s]+", str(choice).strip())
    if len(keys) == 1 and keys[0] not in mapping and keys[0].isdigit():
        keys = list(keys[0])
    if not keys or any(key not in mapping for key in keys):
        raise StudioError("选项映射缺失，不能猜测编号")
    return list(dict.fromkeys(keys))


def _pending_commit(project, request, payload, state):
    topic = deepcopy(state["topics"].get(request["topic_id"], request["context"]["topic"]))
    prior_status, prior_decision = topic["status"], topic.get("decision", "")
    if request["source_signature"] != dependencies(project, topic, request["dependency_names"], not request["modern"]):
        raise StudioError("构筑上下文已过期")
    decision = payload.get("decision", "").strip()
    keys = selected_keys(payload["choice"], request["options"]) if payload.get("choice") is not None else []
    if keys:
        if payload.get("prompt_id", request["prompt_id"]) != request["prompt_id"]:
            raise StudioError("提交引用了不同的题目版本")
        decision = "；".join(request["options"][k] for k in keys) + ("。修订：" + decision if decision else "")
    if not decision or re.fullmatch(r"[\d+、,，\s]+", decision):
        raise StudioError("必须保存展开后的文字结论")
    status = payload.get("status", "complete")
    if status not in {"complete", "proposal", "open", "parked"}:
        raise StudioError("主题状态必须是 complete/proposal/open/parked")
    edits = payload.get("edits", {})
    if not isinstance(edits, dict):
        raise StudioError("edits 必须是模块与完整 Markdown 的映射")
    if status in {"proposal", "parked"} and edits:
        raise StudioError("候选方案不得写入正史")
    if status == "complete" and request["module"] not in edits:
        raise StudioError("完成主题必须提交对应模块正文")
    if edits and (topic["status"] in {"complete", "superseded"} or
            (load_yaml(project / "项目配置.yaml").get("bible_status") == "frozen" and not topic.get("revises"))):
        raise StudioError("已采用或冻结设定需要先提出修订请求")
    for rel, value in edits.items():
        if rel not in request["dependency_names"]:
            raise StudioError(f"修改未准备的模块 {rel}，请先显式加入关联材料")
        module_path(project, rel)
        if not isinstance(value, str) or not value.strip():
            raise StudioError("构筑编辑必须是完整非空 Markdown")
    assistant_text = payload.get("assistant_text", "")
    if request["modern"] and (not isinstance(assistant_text, str) or not assistant_text.strip()):
        raise StudioError("请在同次提交保存实际展示的完整回答 assistant_text")
    topics = {}
    topic.update(decision=decision, status=status, version=topic.get("version", 0) + 1,
                 open_questions=string_list(payload.get("open_questions", topic.get("open_questions", [])), "待决问题"),
                 intentional_blanks=string_list(payload.get("intentional_blanks", topic.get("intentional_blanks", [])), "有意留白"))
    if 'accepted_facts' in payload:
        if status not in {'open', 'complete'} or prior_status in {'complete', 'superseded'}:
            raise StudioError('累计事实只可在开放主题确认；已采用主题请先提出修订')
        if load_yaml(project / '项目配置.yaml').get('bible_status') == 'frozen' and not topic.get('revises'):
            raise StudioError('冻结设定的累计事实需要先提出修订')
        from studio_construction_facts import update_facts
        topic['accepted_facts'] = update_facts(topic.get('accepted_facts', []), payload['accepted_facts'], request['operation_id'])
    from studio_construction_chat import apply_fact_review
    apply_fact_review(topic, payload, edits, request['operation_id'])
    if 'rejected_directions' in payload:
        topic['rejected_directions'] = string_list(payload['rejected_directions'], '明确否决方向')
    if prior_status in {"complete", "superseded"} and not edits:
        topic.update(status=prior_status, decision=prior_decision)
    if "depends_on" in payload:
        depends = string_list(payload["depends_on"], "依赖主题")
        if any(t not in state["topics"] or t == topic["id"] for t in depends):
            raise StudioError("依赖必须指向本作品中另一个现有主题")
        topic["depends_on"] = list(dict.fromkeys(depends))
    topic.pop("prompt_id", None)
    data = {"transcript": {"user": request["context"].get("user_text"), "assistant": assistant_text or None},
            "record": {"topic_id": topic["id"], "summary": decision[:180], "status": status,
                       "source": "recorded" if request["modern"] else "legacy-cli"},
            "selection": {"prompt_id": request["prompt_id"], "keys": keys},
            "edits": {rel: {"before": module_path(project, rel).read_text(encoding="utf-8")
                             if module_path(project, rel).exists() else None, "after": value} for rel, value in edits.items()}}
    next_prompt = payload.get("next_prompt")
    from studio_construction_chat import next_topic
    prompt_topic = next_topic(project, state, topic, payload.get("next_topic"), next_prompt)
    if next_prompt:
        if not isinstance(next_prompt, dict) or not isinstance(next_prompt.get("question"), str) or not next_prompt["question"].strip():
            raise StudioError("next_prompt 需要完整的 question 与 options")
        mapping = options_map(next_prompt.get("options", {}))
        pid = "prompt-" + request["operation_id"]
        data["prompt"] = {"id": pid, "question": next_prompt["question"], "options": mapping}
        data["prompts"] = {pid: {"id": pid, "topic_id": prompt_topic["id"], "event_id": request["operation_id"],
            "question": next_prompt["question"][:140], "options": {k: v[:160] for k, v in mapping.items()}, "selected": []}}
        prompt_topic["prompt_id"] = pid
    if prompt_topic is not topic:
        topics[prompt_topic["id"]] = prompt_topic
    if request["prompt_id"] and keys:
        previous = deepcopy(state["prompts"][request["prompt_id"]])
        previous["selected"] = keys
        data.setdefault("prompts", {})[request["prompt_id"]] = previous
    if status == "complete" and topic.get("revises"):
        source = deepcopy(state["topics"][topic["revises"]])
        if source.get("version", 0) != topic["revises_version"]:
            raise StudioError("待修订的原主题已经变化，请重新核对修订范围")
        source.update(status="superseded", version=source.get("version", 0) + 1, replaced_by=topic["id"])
        topics[source["id"]] = source
        for other in state["topics"].values():
            if source["id"] in other.get("depends_on", []) and other["id"] != topic["id"]:
                changed = deepcopy(topics.get(other['id'], other))
                changed["needs_review"] = list(dict.fromkeys(other.get("needs_review", []) + [source["id"]]))
                changed["version"] = changed.get("version", 0) + 1
                topics[changed["id"]] = changed
    reviewed = string_list(payload.get("reviewed_dependencies", []), "已复核依赖")
    if any(value not in topic.get("needs_review", []) for value in reviewed):
        raise StudioError("只能确认该主题实际待复核的依赖")
    dependencies_after = []
    for source in topic.get("depends_on", []):
        target, seen = source, set()
        if source in reviewed:
            while state["topics"].get(target, {}).get("replaced_by"):
                if target in seen:
                    raise StudioError("修订关系出现循环")
                seen.add(target)
                target = state["topics"][target]["replaced_by"]
        dependencies_after.append(target)
    topic["depends_on"] = list(dict.fromkeys(dependencies_after))
    topic["needs_review"] = [x for x in topic.get("needs_review", []) if x not in reviewed]
    topics[topic["id"]] = topic
    data["topics"] = topics
    data["conversation"] = {"topic_id": prompt_topic["id"], "prompt_id": prompt_topic.get("prompt_id"),
                            "operation_id": request["operation_id"]}
    module_status = status if not request["modern"] else "open"
    data["modules"] = {request["module"]: {"status": module_status, "decision": decision,
        "open_questions": topic["open_questions"], "intentional_blanks": topic["intentional_blanks"]}}
    receipt = {"status": "complete", "request_hash": digest(payload), "module": request["module"],
        "topic_id": topic["id"], "decision": decision, "module_status": module_status,
        "request_id": request.get("request_id"), "prompt_id": prompt_topic.get("prompt_id"),
        "next_topic_id": prompt_topic["id"]}
    data["operations"] = {request["operation_id"]: receipt}
    return {"operation_id": request["operation_id"], "request_hash": digest(payload), "payload": payload,
        "edits": edits, "before": {r: v["before"] for r, v in data["edits"].items()},
        "needs_revision": bool(edits and topic.get("revises")),
        "event": make_event("discussion", data, request["operation_id"])}


def _finish(project, runtime, pending):
    try:
        completed_state = read_state(project)
    except StudioError:
        completed_state = None  # The exact pending event can repair its own torn tail.
    completed = (completed_state or {}).get("operations", {}).get(pending["operation_id"])
    if completed and completed.get("status") == "complete":
        if completed["request_hash"] != pending["request_hash"]:
            raise StudioError("已提交记录与恢复内容不匹配")
        from studio_construction_history import render
        render(project, completed_state)
        return _cleanup(runtime, pending, completed)
    for rel, target in pending["edits"].items():
        path = module_path(project, rel)
        current = path.read_text(encoding="utf-8").rstrip() if path.exists() else None
        before = pending["before"][rel]
        if current not in (before.rstrip() if before is not None else None, target.rstrip()):
            raise StudioError(f"恢复时发现外部修改，拒绝覆盖：{rel}")
    if pending.get("needs_revision"):
        config = load_yaml(project / "项目配置.yaml")
        config["bible_status"] = "revising"
        write_yaml(project / "项目配置.yaml", config)
        from studio_bible import _sync_status_view
        _sync_status_view(project / "00-项目索引.md", "修订中", "- Story Bible：")
    for rel, value in pending["edits"].items():
        atomic_write_text(module_path(project, rel), value)
    state = append_event(project, pending["event"])
    receipt = state["operations"][pending["operation_id"]]
    return _cleanup(runtime, pending, receipt)


def _cleanup(runtime, pending, receipt):
    atomic_write_json(runtime / "last-commit.json", {**receipt, "operation_id": pending["operation_id"]})
    for filename in ("pending.json", "prepare.json"):
        path = runtime / filename
        if path.exists() and (read_json(path) or {}).get("operation_id") == pending["operation_id"]:
            path.unlink()
    return {**receipt, "status": "committed", "operation_id": pending["operation_id"]}


def commit_bible(project, payload):
    project = Path(project).resolve()
    runtime = runtime_root(project)
    with session_lock(project), session_lock(runtime):
        oid = payload.get("operation_id")
        pending = read_json(runtime / "pending.json")
        if pending:
            if pending["operation_id"] != oid or pending["request_hash"] != digest(payload):
                raise StudioError("存在不同的未完成提交；请原样恢复，不能覆盖")
            return _finish(project, runtime, pending)
        state = read_state(project)
        existing = state["operations"].get(oid)
        if existing:
            if existing["request_hash"] != digest(payload):
                raise StudioError("构筑事务 ID 已被不同内容使用")
            if existing["status"] == "complete":
                return {**existing, "status": "committed", "operation_id": oid}
            # v1 journals already contain the exact payload hash and before images.
            # Resume them without inferring a missing conversation or timestamp.
            if existing.get("status") != "pending" or "edits" not in existing or "before" not in existing:
                raise StudioError("旧版提交缺少恢复材料，保留原记录并诊断")
            topic_id = "legacy-" + digest(existing["module"])
            topic = deepcopy(state["topics"][topic_id])
            topic.update(status=existing["module_status"], decision=existing["decision"],
                open_questions=existing.get("open_questions", []), intentional_blanks=existing.get("intentional_blanks", []),
                version=topic.get("version", 0) + 1)
            receipt = {key: value for key, value in existing.items() if key not in {"edits", "before"}}
            receipt.update(status="complete", topic_id=topic_id)
            event = make_event("legacy_recovered", {"topics": {topic_id: topic},
                "modules": {existing["module"]: {k: topic[k] for k in ("status", "decision", "open_questions", "intentional_blanks")}},
                "operations": {oid: receipt}, "record": {"topic_id": topic_id, "summary": existing["decision"][:180],
                "status": existing["module_status"], "source": "legacy-cli"}, "transcript": {"user": None, "assistant": None},
                "edits": {rel: {"before": existing["before"][rel], "after": value} for rel, value in existing["edits"].items()}}, oid)
            pending = {"operation_id": oid, "request_hash": digest(payload), "payload": payload,
                       "edits": existing["edits"], "before": existing["before"], "event": event}
            initialize(project)
            atomic_write_json(runtime / "pending.json", pending)
            return _finish(project, runtime, pending)
        request = read_json(runtime / "prepare.json")
        if not request or request.get("operation_id") != oid:
            raise StudioError("请先 bible prepare，并使用对应操作 ID")
        if "modern" not in request:
            tid = legacy_topic(request["module"])
            request.update(topic_id=tid, modern=False, prompt_id=None,
                dependency_names=list(dict.fromkeys(["核心概念.md", request["module"], *request.get("materials", {}), *payload.get("edits", {})])),
                context={"topic": state["topics"][tid], "user_text": None})
        pending = _pending_commit(project, request, payload, state)
        initialize(project)
        atomic_write_json(runtime / "pending.json", pending)
        return _finish(project, runtime, pending)


def operation_bible(project, action="show", operation_id=None, reason=None):
    runtime = runtime_root(project)
    if operation_id and not re.fullmatch(r"[a-zA-Z0-9-]+", operation_id):
        raise StudioError("非法操作 ID")
    with session_lock(project), session_lock(runtime):
        pending = read_json(runtime / "pending.json")
        request = read_json(runtime / "prepare.json")
        if action == "resume":
            if not pending or pending["operation_id"] != operation_id:
                raise StudioError("没有匹配的待恢复提交")
            return _finish(project, runtime, pending)
        if action == "archive":
            if pending:
                raise StudioError("已有提交中的正文；请先恢复原提交")
            if not request or request["operation_id"] != operation_id or not isinstance(reason, str) or not reason.strip():
                raise StudioError("归档需要匹配操作 ID 与明确原因")
            archive = confined(project, f".runtime/bible/archived/{operation_id}.json")
            atomic_write_json(archive, {"request": request, "reason": reason})
            (runtime / "prepare.json").unlink()
            return {"status": "archived", "operation_id": operation_id}
        if action != "show":
            raise StudioError("操作只支持 show/resume/archive")
        if pending and (operation_id is None or pending["operation_id"] == operation_id):
            return {"status": "pending", "operation_id": pending["operation_id"], "payload": pending["payload"]}
        if request and (operation_id is None or request["operation_id"] == operation_id):
            state = read_state(project)
            topic = state["topics"].get(request.get("topic_id"))
            valid = bool(topic and request.get("dependency_names") and request["source_signature"] ==
                         dependencies(project, topic, request["dependency_names"], not request.get("modern")))
            return {**request, "status": "ready" if valid else "stale"}
        state = read_state(project)
        if operation_id and operation_id in state["operations"]:
            return {**state["operations"][operation_id], "status": "committed", "operation_id": operation_id}
        if operation_id and confined(project, f".runtime/bible/archived/{operation_id}.json").exists():
            return {"status": "archived", "operation_id": operation_id}
        return {"status": "idle"}


def revise_bible(project, module=None):
    from studio_bible import _sync_status_view
    runtime = runtime_root(project)
    with session_lock(project), session_lock(runtime):
        if module:
            module_path(project, module)
        if (runtime / "pending.json").exists() or (runtime / "prepare.json").exists():
            raise StudioError("先完成或显式归档当前构筑事务")
        state = initialize(project)
        config = load_yaml(project / "项目配置.yaml")
        config["bible_status"] = "revising"
        write_yaml(project / "项目配置.yaml", config)
        _sync_status_view(project / "00-项目索引.md", "修订中", "- Story Bible：")
        data = {"topics": {}, "modules": {}}
        if module:
            if module in state["modules"]:
                data["modules"][module] = {**state["modules"][module], "status": "open"}
            tid = legacy_topic(module)
            if tid in state["topics"]:
                topic = {**state["topics"][tid], "status": "open", "version": state["topics"][tid].get("version", 0) + 1}
                data["topics"][tid] = topic
        append_event(project, make_event("reopened", data, str(uuid.uuid4())))
        return {"status": "revising", "module": module}
