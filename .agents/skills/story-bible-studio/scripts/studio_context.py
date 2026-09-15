"""Event-derived, budgeted context with pinned canon and explicit diagnostics."""
import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from studio_core import (AGENCY_RULES, CONFIG_FILE, StudioError, atomic_write_json, atomic_write_text,
                         diff_output_config, load_yaml, normalize_agency, normalize_prose, normalize_status_bar,
                         output_config_snapshot, read_events, reduce_events, render_output_spec, render_scene,
                         selected_turns, resolve_time_display, session_lock)
from studio_policy import resolve_policy
from studio_output import resolve_output, output_rules
from studio_profiles import profile_snapshot, relation_sections_for_characters
from studio_versions import bind_baseline, canon_payload, character_registry, snapshot_root
from studio_styles import resolve_style, render_style, style_dependency
from studio_requirements import requirements_snapshot, render_requirements


def language_section(heading, content):
    """Retrieve declared language facts, without choosing a language ourselves."""
    return bool(re.search(r"语言|母语|语种|language|tongue", heading, re.I)
                or re.search(r"(?im)^\s*(?:[-*]\s*)?(?:母语|语言|常用语言|交谈语言|language|native language)\s*[:：]", content))


def resolve_characters(registry, names, user_text):
    aliases = {}
    for name, item in registry.items():
        for alias in item["aliases"]:
            aliases.setdefault(alias.casefold(), set()).add(name)
    active, unresolved = set(), []
    for raw in names:
        matches = aliases.get(str(raw).casefold(), set())
        if len(matches) == 1:
            active.update(matches)
        else:
            unresolved.append(str(raw))
    for alias, matches in aliases.items():
        pattern = r"(?<!\w)" + re.escape(alias) + r"(?!\w)" if alias.isascii() else re.escape(alias)
        if len(matches) == 1 and re.search(pattern, user_text.casefold()):
            active.update(matches)
    return active, unresolved


def profile_blocks(project, config, active, query, mode, canon_config):
    from studio_retrieval import split_markdown, tokens
    if not config.get("profile_id"):
        return [], [], {}
    if not config.get("profile_revision"):
        raise StudioError("Profile 必须固定 revision")
    root = profile_snapshot(project, config["profile_id"], config["profile_revision"])
    required, optional = [], []
    terms = tokens(query)
    for filename in ("角色.md", "关系接口.md", "语料.md", "NSFW.md"):
        text = (root / filename).read_text(encoding="utf-8")
        if filename == "NSFW.md" and not (canon_config.get("nsfw") or {}).get("enabled", True):
            continue
        if filename == "关系接口.md" and (canon_config.get("story_scope") or {}).get("mode") == "world-hub":
            text = relation_sections_for_characters(text, active)
        for i, (heading, content) in enumerate(split_markdown(text)):
            label = f"Profile {filename[:-3]} · {heading}"
            core = (filename == "角色.md" and (i < 2 or re.search("核心|人格|选择|原则|身份|稳定|欲望", heading))) or (filename == "语料.md" and heading.startswith("核心声线")) or filename == "关系接口.md"
            if config.get("bilingual", {}).get("enabled") and filename in {"角色.md", "语料.md"} and language_section(heading, content):
                core = True
            score = len(terms & tokens(heading + " " + content))
            if core:
                required.append((label, content))
            elif score:
                optional.append((score, label, content))
    optional.sort(key=lambda item: -item[0])
    return required, [(h, c) for _, h, c in optional[:(6 if mode == "quality" else 3)]], {
        "profile_id": config["profile_id"], "profile_revision": config["profile_revision"]}


def build_context(project, session, user_text, query="", top_k=None, budget_chars=None, mode=None, style_override=None, config_override=None, *, projected_events=None, regeneration_request=''):
    from studio_retrieval import (INDEX_VERSION, PREFLIGHT_GATE, load_or_build_index,
                                  search_chunks, select_voice_chunks, format_turn,
                                  motif_ledger_text, tokens)
    started = time.perf_counter()
    if session.resolve().parent != (project / "会话").resolve():
        raise StudioError("会话隔离边界不匹配")
    config = resolve_policy(load_yaml(session / CONFIG_FILE), mode)
    config = resolve_output(config, config_override)
    if config.get("project") != project.name or config.get("session_id") != session.name or config.get("schema_version") != 3:
        raise StudioError("会话配置与请求不一致")
    budget = int(budget_chars if budget_chars is not None else config["context_budget_chars"])
    if budget <= 0:
        raise StudioError("上下文预算必须为正数")
    history = read_events(session) if projected_events is None else projected_events
    state = reduce_events(history)
    config["profile_id"] = state.get("profile_id")
    config["profile_revision"] = state.get("profile_revision")
    revision = state.get("bible_revision")
    # Read-only callers can inspect legacy sessions without binding them.
    canon = snapshot_root(project, revision) if revision else project
    canon_config = load_yaml(canon / "项目配置.yaml")
    writing_style = resolve_style(project, session, style_override, config, canon_config)
    requirements = requirements_snapshot(session)
    index = load_or_build_index(canon)
    registry = json.loads((canon / "manifest.json").read_text(encoding="utf-8"))["registry"] if revision else character_registry(canon_payload(canon)["files"])
    active, unresolved = resolve_characters(registry, state["scene"].get("characters", []), user_text)
    voice_state = {**state, "scene": {**state["scene"], "characters": sorted(active)}}
    query_parts = [user_text, query, " ".join(sorted(active)), str(state["scene"].get("location", "")),
                   str(state["scene"].get("open_event", ""))]
    recall_query = " ".join(p for p in query_parts if p.strip())
    agency = normalize_agency(config.get("agency"))
    output_snapshot = output_config_snapshot(config)
    spec_line = f"回合规范：{render_output_spec(output_snapshot)}"
    last_turns = state.get("turns", {})
    if last_turns:
        last_turn = max(last_turns, key=int)
        selected = state.get("selected_variants", {}).get(last_turn, "v1")
        last_payload = last_turns[last_turn].get("variants", {}).get(selected) or {}
        previous_snapshot = last_payload.get("output_config")
        if isinstance(previous_snapshot, dict):
            changes = diff_output_config(previous_snapshot, output_snapshot)
            if changes:
                spec_line += "；变更：" + "；".join(changes)
    required = [("运行规则", PREFLIGHT_GATE), ("用户最新输入", user_text),
                ("写作与会话配置", json.dumps({
                    "mode": config["mode"],
                    "agency": {"mode": agency, "rule": AGENCY_RULES[agency]},
                    "pov": config.get("pov"),
                    "person": config.get("person"), "tense": config.get("tense"),
                    "prose": output_snapshot["prose"],
                    "status_bar": output_snapshot["status_bar"],
                    "interaction_preset": output_snapshot["interaction_preset"],
                    "initiative": output_snapshot["initiative"], "span": output_snapshot["span"],
                    "bilingual": output_snapshot["bilingual"],
                    "output_rules": output_rules(config),
                    "style": writing_style["value"],
                    "resolved_time_display": resolve_time_display(canon_config, config),
                    "nsfw_overlay": config.get("nsfw_overlay"),
                }, ensure_ascii=False)),
                ("当前场景", render_scene(state))]
    required.insert(2, ("有效文风规则", render_style(writing_style)))
    if regeneration_request:
        required.append(('用户对本次重生成的要求（不作为角色行动）', regeneration_request))
    if requirements["items"]:
        required.insert(3, ("本会话创作要求", render_requirements(requirements)))
    if state["memory"].strip() != "# 压缩记忆":
        required.append(("压缩记忆", state["memory"]))
    required.extend((f"本会话状态：{Path(k).stem}", v) for k, v in state["continuity_state"].items())
    ledger = motif_ledger_text(state)
    if ledger:
        required.append(("回环梗台账", ledger))
    if not state["last_compressed_turn"] and (state.get("opening") or {}).get("text"):
        required.append(("会话开场", state["opening"]["text"]))
    profile_required, optional, binding = profile_blocks(project, config, active, recall_query, config["mode"], canon_config)
    required.extend(profile_required)
    selected_chunks, selected_voice = [], []
    core_files = {"StoryBible/核心概念.md", "StoryBible/连续性与留白.md"}
    for chunk in index["chunks"]:
        path, heading = chunk["path"], chunk["heading"]
        is_character = path in {registry[n]["path"] for n in active}
        core = path in core_files or (path == "StoryBible/世界设定.md" and re.search("核心|规则|约束|世界设定", heading)) or (is_character and (re.search("核心|人格|身份|稳定|欲望", heading) or heading == chunk["title"]))
        if config["bilingual"]["enabled"] and is_character and language_section(heading, chunk["content"]):
            core = True
        if core:
            required.append((f"人物与世界核心：{path} · {heading}", chunk["content"]))
            selected_chunks.append(chunk)
    # Unstructured imported dossiers still need a useful identity baseline.
    for name in sorted(active):
        dossier = [c for c in index["chunks"] if c["path"] == registry[name]["path"]]
        meaningful = [c for c in dossier if any(line.strip() and not line.startswith("#") for line in c["content"].splitlines())]
        for chunk in meaningful[:2]:
            if chunk not in selected_chunks:
                required.append((f"人物核心：{chunk['path']} · {chunk['heading']}", chunk["content"]))
                selected_chunks.append(chunk)
    world_blocks = [c for c in index["chunks"] if c["path"] == "StoryBible/世界设定.md"
                    and any(line.strip() and not line.startswith("#") for line in c["content"].splitlines())]
    for chunk in world_blocks[:2]:
        if chunk not in selected_chunks:
            required.append((f"世界核心：{chunk['heading']}", chunk["content"]))
            selected_chunks.append(chunk)
    voices = select_voice_chunks(canon, voice_state, user_text, recall_query, index=index)
    for chunk in voices:
        section = (f"人物声线：{chunk['path']} · {chunk['heading']}", chunk["content"])
        if chunk["heading"].startswith("核心声线"):
            required.append(section)
            selected_voice.append(chunk)
        else:
            optional.insert(0, section)
    missing_voice = [name for name in active if not any(Path(c["path"]).stem == name for c in selected_voice)]
    turns = selected_turns(state)
    first = min(max(1, state["current_turn"] - int(config["recent_turns"]) + 1), state["last_compressed_turn"] + 1)
    recent = [t for t in turns if t["turn"] >= first]
    raw = "\n\n".join(format_turn(t) for t in recent)
    # Original prose is kept until a reviewed memory covers it.
    if raw:
        required.append(("最近已选原文", raw))
    detail_input = "" if user_text.strip().casefold() in {"继续", "接着", "continue"} else user_text
    # Identity is guaranteed above; using it as a detail keyword ranks unrelated biography.
    detail_query = " ".join([detail_input, query, str(state["scene"].get("location", "")), str(state["scene"].get("open_event", ""))])
    for name in active:
        for alias in registry[name]["aliases"]:
            detail_query = re.sub(re.escape(alias), " ", detail_query, flags=re.I)
    retrieved = search_chunks(canon, detail_query, top_k=top_k or config["retrieval_top_k"], layers={"bible"}, index=index)
    optional.extend((f"检索：{c['path']} · {c['heading']}", c["content"]) for c in retrieved)
    from studio_session_recall import explicit_recall, recall
    historical = {'status': 'not_requested', 'results': [], 'chars': 0}
    if explicit_recall(user_text, query):
        historical = recall(session, query or user_text, config['mode'],
                            events=history if projected_events is not None else None, before_turn=first,
                            aliases={name: item['aliases'] for name, item in registry.items()})
        optional[0:0] = [(f"本会话旧事：Turn {m['turn']}-{m['through_turn']} / {m['event_id']}", m['text']) for m in historical['results']]
    body = [f"# 会话上下文：{session.name}\n\n{spec_line}\n\n动态材料仅来自本会话；正史版本 {revision or '未绑定基线'}。最新输入优先；不得读取兄弟会话或 Profile 工作区。"]
    seen, sizes, omitted = set(), {}, []
    skipped_chars = 0
    def append(heading, content, mandatory):
        nonlocal skipped_chars
        if not content.strip():
            return
        if not any(line.strip() and not line.startswith("#") for line in content.splitlines()):
            return
        hashed = hashlib.sha256(content.strip().encode()).hexdigest()
        if hashed in seen:
            skipped_chars += len(content)
            return
        addition = f"\n\n## {heading}\n\n{content.strip()}"
        if not mandatory and sum(map(len, body)) + len(addition) + 1 > budget:
            omitted.append(heading)
            return
        seen.add(hashed)
        body.append(addition)
        sizes[heading] = len(addition)
    for heading, content in required:
        append(heading, content, True)
    mandatory_chars = sum(map(len, body)) + 1
    for heading, content in optional:
        append(heading, content, False)
    packet = "".join(body).rstrip() + "\n"
    actual = len(packet)
    uncompressed = state["current_turn"] - state["last_compressed_turn"]
    policy = config["memory_policy"]
    reasons = []
    if state["memory_invalidated"]:
        reasons.append("selected_history_changed")
    if mandatory_chars >= budget * policy["hard_context_ratio"]:
        reasons.append("hard_context_budget")
    elif mandatory_chars >= budget * policy["soft_context_ratio"] and uncompressed >= policy["min_uncompressed_turns"]:
        reasons.append("soft_context_budget")
    if uncompressed >= policy["max_uncompressed_turns"]:
        reasons.append("max_uncompressed_turns")
    compressible = max(0, state["current_turn"] - int(config["recent_turns"]) - state["last_compressed_turn"])
    status = "needs_compaction" if reasons and compressible else "ready"
    if state["memory_invalidated"]:
        status = "needs_compaction"
    raw_chars = sizes.get('最近已选原文', 0)
    active_chars = sum(n for h, n in sizes.items() if h == '压缩记忆' or h.startswith('本会话状态：') or h in {'当前场景', '回环梗台账'})
    fixed_chars = mandatory_chars - raw_chars - active_chars
    if mandatory_chars > budget and (not compressible or fixed_chars + active_chars > budget):
        status = "budget_blocked"
    report = {"schema_version": INDEX_VERSION, "status": status, "mode": config["mode"],
              "writing_style": writing_style, "output_config": output_snapshot,
              "output_sources": config["output_sources"],
              "query": recall_query, "detail_query": detail_query, "source_event_id": state["last_event_id"], "bible_revision": revision,
              "budget_chars": budget, "actual_chars": actual, "mandatory_chars": mandatory_chars,
              "budget_overflow_chars": max(0, actual - budget), "section_chars": sizes,
              "duplicate_chars_skipped": skipped_chars, "excluded_for_budget": omitted,
              "budget_layers": {'fixed': fixed_chars, 'active_memory_state': active_chars, 'raw': raw_chars, 'optional': actual - mandatory_chars},
              "budget_blocker": 'fixed_or_active' if fixed_chars + active_chars > budget else 'raw' if mandatory_chars > budget else None,
              "history_recall": {k: v for k, v in historical.items() if k != 'results'},
              "history_evidence": [{k: v for k, v in m.items() if k != 'text'} for m in historical['results']],
              "active_characters": sorted(active), "unresolved_characters": unresolved,
              "missing_core_voice": missing_voice, "profile": binding,
              "motif_ids": sorted({m for m in state.get('motif_ledger', {}) if re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', m)}
                                  | {m for c in voices for m in re.findall(r'(?im)^\s*(?:[-*]\s*)?(?:ID|梗ID|梗 ID)\s*[:：]\s*([a-z0-9]+(?:-[a-z0-9]+)*)\s*$', c['content'])}),
              "history_changes_to_review": state["history_changes"],
              "recent_turns": [t["turn"] for t in recent],
              "uncompressed_turns": [t["turn"] for t in recent if t["turn"] > state["last_compressed_turn"]],
              "retrieved": [{k: c.get(k) for k in ("path", "heading", "chunk_id")} for c in selected_chunks + retrieved if hashlib.sha256(c["content"].strip().encode()).hexdigest() in seen],
              "voice_retrieved": [{k: c.get(k) for k in ("path", "heading", "chunk_id")} for c in voices if hashlib.sha256(c["content"].strip().encode()).hexdigest() in seen],
              "compaction": {"recommended": bool(reasons), "reasons": reasons,
                             "through_turn": state["current_turn"] if state["memory_invalidated"] and not compressible else state["current_turn"] - int(config["recent_turns"]),
                             "compressible_turns": compressible, "uncompressed_turn_count": uncompressed,
                             "last_compressed_turn": state["last_compressed_turn"]},
              "isolation": {"mode": "strict-session", "session": session.name},
              "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
    report["requirements"] = requirements
    return packet, report


def write_context(project, session, user_text, query="", mode=None, style_override=None, config_override=None, *, request_metadata=None):
    if not user_text.strip():
        raise StudioError("用户输入不能为空")
    bind_baseline(project, session)
    with session_lock(session):
        if (session / ".runtime/variant-current/transaction.json").exists():
            raise StudioError("存在待完成的重生成事务；先提交候选或显式归档，再准备新回合")
        current = session / ".runtime/current"
        current.mkdir(parents=True, exist_ok=True)
        transaction = current / "transaction.json"
        if (current / "response.md").exists():
            raise StudioError("存在待提交正文；先提交或显式移走草稿再 prepare")
        if config_override is None and transaction.exists():
            previous = json.loads(transaction.read_text(encoding="utf-8"))
            user_path = current / "user.md"
            if user_path.exists() and user_path.read_text(encoding="utf-8").rstrip() == user_text.rstrip():
                config_override = previous.get("config_override")
        options = {"query": query, "mode": mode, "style_override": style_override, "config_override": config_override}
        if transaction.exists():
            previous = json.loads(transaction.read_text(encoding="utf-8"))
            same_input = (current / "user.md").read_text(encoding="utf-8").rstrip() == user_text.rstrip()
            previous_options = previous.get("prepare_options", {"query": "", "mode": None,
                               "style_override": previous.get("style_override"), "config_override": previous.get("config_override")})
            if not same_input or (previous["status"] == "ready" and previous_options != options):
                raise StudioError("已有待完成事务；更换输入或本轮设置前请显式归档原操作")
            if previous["status"] == "ready":
                valid = (previous["source_event_id"] == reduce_events(read_events(session))["last_event_id"]
                         and previous["config_hash"] == hashlib.sha256((session / CONFIG_FILE).read_bytes()).hexdigest()
                         and previous.get("style_dependency") == style_dependency(project, session, style_override))
                packet_path, report_path = session / ".runtime/context-packet.md", session / ".runtime/retrieval-report.json"
                if valid and packet_path.exists() and report_path.exists():
                    return packet_path, report_path
                raise StudioError("原 ready 事务已过期或不完整；请显式归档后重新准备")
        packet, report = build_context(project, session, user_text, query, mode=mode, style_override=style_override, config_override=config_override)
        operation = {"operation_id": str(uuid.uuid4()), "source_event_id": report["source_event_id"],
                     "status": report["status"], "mode": report["mode"],
                     "config_hash": hashlib.sha256((session / CONFIG_FILE).read_bytes()).hexdigest(),
                     "writing_style": report["writing_style"], "style_override": style_override,
                     "requirements": report["requirements"],
                     "style_dependency": report["writing_style"]["dependency"],
                     "output_config": report["output_config"], "config_override": config_override,
                     "prepare_options": options}
        if transaction.exists():
            previous = json.loads(transaction.read_text(encoding="utf-8"))
            if previous["source_event_id"] == operation["source_event_id"] and (current / "user.md").read_text(encoding="utf-8").rstrip() == user_text.rstrip():
                operation["operation_id"] = previous["operation_id"]
        if request_metadata:
            operation.update(request_metadata)
        atomic_write_text(current / "user.md", user_text)
        context_path = session / ".runtime/context-packet.md"
        report_path = session / ".runtime/retrieval-report.json"
        atomic_write_text(context_path, packet if report["status"] == "ready" else f"# 暂停起草\n\n状态：{report['status']}。先处理检索报告；不得用不完整材料续写。")
        atomic_write_json(report_path, report)
        atomic_write_json(transaction, operation)
        return context_path, report_path
