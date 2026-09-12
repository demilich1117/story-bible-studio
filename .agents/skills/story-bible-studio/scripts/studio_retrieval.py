from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from studio_core import CONFIG_FILE, MEMORY_FILE, OPENING_FILE, STATE_FILES, StudioError, atomic_write_json, atomic_write_text, load_yaml, read_events, reduce_events, resolve_time_display, selected_turns
from studio_profiles import profile_context


INDEX_VERSION = 6
INDEX_FILE = "检索索引.json"
EXCLUDED_PARTS = {".git", ".runtime", "构筑", "导出", "会话", "检查点", "回复变体", "用户档案", "素材", "正史版本"}
HIDDEN_NSFW_KEYS = {"adults_only", "consent_required"}
PLACEHOLDER_NSFW_VALUES = {
    "contraception_or_fertility": {"follow-canon"},
    "aftercare": {"character-specific"},
}

PREFLIGHT_GATE = """## 生成前私有停顿（不得输出或落盘）

起草正文前必须按 G0→G5 串行完成；每关先形成短语级答案再进入下一关，禁止一次性扫清单、用“均符合／同上”跳过，或边写正文边补答案：

G0 用户契约：必须兑现、允许补写、不得偷加各是什么？先读包首回合规范及 output_rules，字数、权限、人称、双语、主动性、跨度和状态栏以本包有效配置为准，不凭前轮记忆。人称不扩大权限。
G1 抗过拟合：人物稳定基线、本轮唯一变化、阻止夸大的反向证据各是什么？
G2 因果骨架：`欲求 → 惯用策略 → 阻力／代价 → 具体动作 → 现场变化` 是否完整？短 RP 允许只有对白、观察或等待，链可止于等待回应，不要求新增动作或局势变化。
G3 强度解耦：情绪、权力、力度、速度、语言粗度、露骨度与关系后果中，哪些有依据上升？其余持平或下降。
G4 模板反选：类型惯性最可能自动补出的下一拍是什么？若无用户要求或人物化因果，拒绝它。
G5 物理落点：人物与物件位置、有效 span 允许的跨度和自然停点是什么？短 RP 默认单拍，在需 user 回应前停笔，不强制问句、不为凑字数加戏。

任一关没有具体答案就停在该关重拟，不能开始正文。达到会话压缩阈值、转场、用户纠正或明显重复时先用压缩记忆与固定 Profile 重建基线；最近原文是现场证据，不是人格趋势，只证明当前场景状态。“继续／更露骨／更投入”不自动等于更狠、更快、更粗、更支配或更多玩法。成人草稿命中三项 Wild Sex 模板特征时，保留用户点名行为与露骨度，只撤销无依据的联动升级后重新过 G2→G5。"""


def source_files(project):
    for root in (project / "StoryBible", project / "故事/章节", project / "故事/片段"):
        if root.exists():
            yield from sorted(root.rglob("*.md"))


def source_manifest(project: Path) -> list[dict[str, Any]]:
    manifest = []
    for path in source_files(project):
        relative = path.relative_to(project)
        if EXCLUDED_PARTS.intersection(relative.parts) or path.name in {"上下文包.md", "压缩候选.md"}:
            continue
        stat = path.stat()
        manifest.append({"path": relative.as_posix(), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    return manifest


def layer_for(project: Path, path: Path) -> str:
    parts = path.relative_to(project).parts
    if parts and parts[0] == "StoryBible":
        if len(parts) > 1 and parts[1] == "语料":
            return "voice"
        return "bible"
    if parts and parts[0] == "故事" and ("章节" in parts or "片段" in parts):
        return "prose"
    if "研究" in parts:
        return "research"
    return "other"


def tokens(text: str) -> set[str]:
    result = {item.lower() for item in re.findall(r"[A-Za-z0-9_'-]{2,}", text)}
    for sequence in re.findall(r"[\u3400-\u9fff]+", text):
        if len(sequence) == 1:
            result.add(sequence)
        else:
            result.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return result


def split_markdown(text: str, chunk_chars: int = 1800) -> list[tuple[str, str]]:
    headings = list(re.finditer(r"^(#{1,4})\s+(.+)$", text, re.M))
    sections: list[tuple[str, str]] = []
    if not headings:
        headings = []
        raw_sections = [("", text)]
    else:
        raw_sections = []
        if headings[0].start() > 0 and text[: headings[0].start()].strip():
            raw_sections.append(("", text[: headings[0].start()]))
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            raw_sections.append((heading.group(2).strip(), text[heading.start():end]))
    for heading, body in raw_sections:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
        current: list[str] = []
        length = 0
        for paragraph in paragraphs:
            if current and length + len(paragraph) + 2 > chunk_chars:
                sections.append((heading, "\n\n".join(current)))
                current = []
                length = 0
            if len(paragraph) > chunk_chars:
                if current:
                    sections.append((heading, "\n\n".join(current)))
                    current = []
                    length = 0
                for start in range(0, len(paragraph), chunk_chars):
                    sections.append((heading, paragraph[start:start + chunk_chars]))
            else:
                current.append(paragraph)
                length += len(paragraph) + 2
        if current:
            sections.append((heading, "\n\n".join(current)))
    return sections


def build_index(project: Path) -> dict[str, Any]:
    chunks = []
    for path in source_files(project):
        relative = path.relative_to(project)
        if EXCLUDED_PARTS.intersection(relative.parts) or path.name in {"上下文包.md", "压缩候选.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, re.M)
        title = title_match.group(1).strip() if title_match else path.stem
        for index, (heading, content) in enumerate(split_markdown(text), start=1):
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
            chunks.append({
                "chunk_id": f"{relative.as_posix()}#{index}-{digest}",
                "path": relative.as_posix(),
                "layer": layer_for(project, path),
                "title": title,
                "heading": heading,
                "tokens": sorted(tokens(f"{relative.as_posix()} {title} {heading} {content}")),
                "content": content,
                "content_hash": digest,
            })
    return {"schema_version": INDEX_VERSION, "source_manifest": source_manifest(project), "chunk_count": len(chunks), "chunks": chunks}


def save_index(project: Path) -> Path:
    target = project / INDEX_FILE
    atomic_write_json(target, build_index(project))
    return target


def load_or_build_index(project: Path) -> dict[str, Any]:
    path = project / INDEX_FILE
    if not path.exists():
        return build_index(project)
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
        if index.get("schema_version") != INDEX_VERSION or index.get("source_manifest") != source_manifest(project):
            index = build_index(project)
            atomic_write_json(path, index)
            return index
        return index
    except (json.JSONDecodeError, AttributeError):
        index = build_index(project)
        atomic_write_json(path, index)
        return index


def search_chunks(
    project: Path,
    query: str,
    top_k: int = 8,
    layers: set[str] | None = None,
    scope: str = "canon",
    index: dict | None = None,
) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []
    query_tokens = tokens(query)
    query_lower = query.lower()
    ranked = []
    for chunk in (index if index is not None else load_or_build_index(project))["chunks"]:
        if layers and chunk["layer"] not in layers:
            continue
        if scope == "canon" and chunk["layer"] not in {"bible", "voice"}:
            continue
        if scope == "story" and chunk["layer"] not in {"bible", "voice", "prose"}:
            continue
        chunk_tokens = set(chunk["tokens"])
        overlap = len(query_tokens & chunk_tokens)
        if not overlap:
            continue
        score = float(overlap)
        path_title = f"{chunk['path']} {chunk['title']} {chunk['heading']}".lower()
        score += sum(4 for word in re.findall(r"[A-Za-z0-9_'-]{2,}|[\u3400-\u9fff]{2,}", query_lower) if word in path_title)
        if query_lower in chunk["content"].lower():
            score += 8
        ranked.append({**chunk, "score": score})
    ranked.sort(key=lambda item: (-item["score"], item["path"], item["chunk_id"]))
    seen_hashes = set()
    results = []
    for item in ranked:
        if item["content_hash"] in seen_hashes:
            continue
        seen_hashes.add(item["content_hash"])
        results.append(item)
        if len(results) >= top_k:
            break
    return results


def writing_config_text(project: Path, session: Path) -> str:
    project_config = load_yaml(project / "项目配置.yaml")
    session_config = load_yaml(session / CONFIG_FILE)
    def compact_nsfw(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        compacted = {}
        for key, item in value.items():
            if key in HIDDEN_NSFW_KEYS or item in (None, "", [], {}):
                continue
            if isinstance(item, str) and item in PLACEHOLDER_NSFW_VALUES.get(key, set()):
                continue
            compacted[key] = item
        return compacted

    selected_session = {key: session_config.get(key) for key in ("agency", "pov", "person", "tense", "prose", "status_bar", "style", "profile_id", "profile_revision", "opening_id")}
    selected_session["nsfw_overlay"] = compact_nsfw(session_config.get("nsfw_overlay", {}))
    selected_session["subagents"] = session_config.get("subagents", {})
    return json.dumps({
        "project": {"default_style": project_config.get("default_style"), "nsfw": compact_nsfw(project_config.get("nsfw", {}))},
        "session": selected_session,
        "resolved_time_display": resolve_time_display(project_config, session_config),
    }, ensure_ascii=False, indent=2)


def format_turn(turn: dict[str, Any]) -> str:
    status = f"\n\n[状态]\n{turn['status']}" if turn.get("status") else ""
    return f"## Turn {turn['turn']} ({turn['variant_id']})\n\n### User\n{turn['user']}\n\n### Assistant\n{turn['prose']}{status}"


def motif_ledger_text(state: dict[str, Any]) -> str:
    ledger = {key: dict(value) for key, value in state.get("motif_ledger", {}).items()}
    for motif_id in state.get("scene", {}).get("motif_cooldowns", []):
        ledger.setdefault(str(motif_id), {
            "last_turn": state.get("current_turn", 0),
            "last_scene": state.get("current_scene", "opening"),
            "uses_in_scene": 1,
            "status": "cooling",
        })
    if not ledger:
        return ""
    lines = ["同一梗族不得连续回合使用，同一场景至多一次；cooling 时默认不用。"]
    for motif_id, entry in sorted(ledger.items()):
        lines.append(
            f"- {motif_id}: {entry.get('status', 'cooling')}；"
            f"最近 Turn {entry.get('last_turn', '?')}／{entry.get('last_scene', '?')}；"
            f"本场 {entry.get('uses_in_scene', 0)} 次"
        )
    return "\n".join(lines)


def select_voice_chunks(
    project: Path,
    state: dict[str, Any],
    user_text: str,
    query: str,
    index: dict | None = None,
) -> list[dict[str, Any]]:
    chunks = [item for item in (index if index is not None else load_or_build_index(project))["chunks"] if item["layer"] == "voice"]
    if not chunks:
        return []

    active = {str(item) for item in state.get("scene", {}).get("characters", [])}
    active.update(Path(item["path"]).stem for item in chunks if Path(item["path"]).stem in user_text)
    if not active:
        return []

    scene_data = {key: value for key, value in state.get("scene", {}).items() if key != "characters"}
    scene_terms = tokens(f"{json.dumps(scene_data, ensure_ascii=False)} {user_text} {query}")
    explicit_terms = tokens(f"{user_text} {query}")
    for character in active:
        scene_terms -= tokens(character)
        explicit_terms -= tokens(character)

    selected = []
    for character in sorted(active):
        character_chunks = [item for item in chunks if Path(item["path"]).stem == character]
        core = [item for item in character_chunks if item["heading"].startswith("核心声线")]
        if core:
            selected.append(sorted(core, key=lambda item: item["chunk_id"])[0])

        coverage = [
            item for item in character_chunks
            if item["heading"].startswith(("对象覆盖", "场景覆盖"))
        ]
        ranked_coverage = []
        for item in coverage:
            overlap = len(scene_terms & set(item["tokens"]))
            if overlap:
                ranked_coverage.append((overlap, item))
        if ranked_coverage:
            ranked_coverage.sort(key=lambda pair: (-pair[0], pair[1]["chunk_id"]))
            selected.append(ranked_coverage[0][1])

        motif_candidates = [item for item in character_chunks if item["heading"].startswith("回环梗")]
        ranked_motifs = []
        for item in motif_candidates:
            alias_match = re.search(r"^- 别名[：:]\s*(.+)$", item["content"], re.M)
            motif_terms = tokens(f"{item['heading']} {alias_match.group(1) if alias_match else ''}")
            overlap = len(explicit_terms & motif_terms)
            if overlap:
                ranked_motifs.append((overlap, item))
        if ranked_motifs:
            ranked_motifs.sort(key=lambda pair: (-pair[0], pair[1]["chunk_id"]))
            selected.append(ranked_motifs[0][1])

    seen = set()
    deduplicated = []
    for item in selected:
        if item["content_hash"] in seen:
            continue
        seen.add(item["content_hash"])
        deduplicated.append(item)
    return deduplicated




def build_context(project, session, user_text, query="", top_k=None, budget_chars=None, mode=None, style_override=None, config_override=None):
    from studio_context import build_context as assemble
    return assemble(project, session, user_text, query, top_k, budget_chars, mode, style_override, config_override)


def write_context(project, session, user_text, query="", mode=None, style_override=None, config_override=None):
    from studio_context import write_context as prepare
    return prepare(project, session, user_text, query, mode, style_override, config_override)
