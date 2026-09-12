from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from studio_core import StudioError, atomic_write_json, atomic_write_text, load_yaml, safe_name, write_yaml


PROFILE_SCHEMA = 1
PROFILE_CONFIG_SCHEMA = 2
PROFILE_DIR = "用户档案"
PROFILE_CONFIG = "档案配置.yaml"
PROFILE_FILES = {
    "角色": "角色.md",
    "关系接口": "关系接口.md",
    "NSFW": "NSFW.md",
    "语料": "语料.md",
    "开场候选": "开场候选.md",
}
OPENING_ID_RE = re.compile(r"opening-\d{2,}")
VOICE_INCOMPLETE_MARKERS = (
    ("候选声线", re.compile(r"候选声线|声线候选|核心声线[（(]候选[）)]|对象覆盖[^\n]*[（(]候选[）)]|场景覆盖[^\n]*[（(]候选[）)]")),
    ("待补／待采样", re.compile(r"待补|待采样|待校准|待正式(?:故事|会话)|例句未冻结|不作为冻结声线")),
    ("故事反推来源", re.compile(r"已选正史|(?:Turn|回合)\s*\d+|正式故事对白证据|跨场景真实对白")),
)


def voice_errors(text: str, label: str = "语料") -> list[str]:
    """Reject provisional or story-derived voice corpora.

    Voice is a construction-time design artifact.  Runtime story text may expose an
    OOC mismatch, but must never be promoted into the corpus as evidence.
    """
    errors: list[str] = []
    if not re.search(r"(?m)^##\s+核心声线\s*$", text):
        errors.append(f"{label}缺少“核心声线”章节")
    if not re.search(r"(?m)^##\s+对象覆盖(?:[：:].+)?\s*$", text):
        errors.append(f"{label}缺少至少一个“对象覆盖”章节")
    if not re.search(r"(?m)^##\s+场景覆盖(?:[：:].+)?\s*$", text):
        errors.append(f"{label}缺少至少一个“场景覆盖”章节")
    for marker, pattern in VOICE_INCOMPLETE_MARKERS:
        if pattern.search(text):
            errors.append(f"{label}含{marker}；须在构筑／导入时按人设补完，不得等待故事反推")
    return errors


def section_has_content(text: str) -> bool:
    visible = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return any(line.strip() and not line.lstrip().startswith("#") for line in visible.splitlines())


def parse_openings(text: str) -> dict[str, Any]:
    """Parse the normalized, human-readable opening catalog."""
    headings = list(re.finditer(r"^##\s+([^\n]+)$", text, re.M))
    shared = ""
    candidates: list[dict[str, Any]] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        title = heading.group(1).strip()
        body = text[heading.end():end].strip()
        if title in {"共同手法", "开场共同手法"}:
            shared = body
            continue
        match = re.fullmatch(r"(opening-\d{2,})\s*[｜|]\s*(.+)", title)
        if not match:
            continue
        status_match = re.search(r"^-\s*状态[：:]\s*(.+)$", body, re.M)
        candidates.append({
            "id": match.group(1),
            "title": match.group(2).strip(),
            "status": status_match.group(1).strip() if status_match else "可用",
            "body": f"## {title}\n\n{body}".rstrip(),
        })
    ids = [item["id"] for item in candidates]
    if len(ids) != len(set(ids)):
        raise StudioError("开场候选 ID 重复")
    return {"shared": shared, "candidates": candidates}


def normalize_openings(text: str, display_name: str) -> str:
    """Normalize legacy heading/list catalogs into stable opening-NN blocks."""
    if parse_openings(text)["candidates"]:
        return text.strip()
    lines = text.splitlines()
    shared_blocks: list[str] = []
    raw_candidates: list[tuple[str, str, str]] = []
    headings = list(re.finditer(r"^##\s+([^\n]+)$", text, re.M))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        title = heading.group(1).strip()
        body = text[heading.end():end].strip()
        if "共同手法" in title:
            shared_blocks.append(body)
            continue
        numbered = list(re.finditer(r"^(?:\d+[.、]|[-*]\s*(?:[A-Za-z0-9一二三四五六七八九十]+)[｜|：:])\s*(.+)$", body, re.M))
        if numbered:
            for item_index, item in enumerate(numbered):
                item_end = numbered[item_index + 1].start() if item_index + 1 < len(numbered) else len(body)
                item_text = body[item.start():item_end].strip()
                item_title = re.split(r"[：:]", item.group(1), maxsplit=1)[0].strip(" *：:") or title
                raw_candidates.append((item_title, item_text, title))
        elif re.match(r"^(?:[A-Za-z]|候选[一二三四五六七八九十]+)(?:\s*[｜|：:]|$)", title):
            clean_title = re.sub(r"^(?:[A-Za-z]|候选[一二三四五六七八九十]+)\s*[｜|：:]?\s*", "", title).strip() or title
            raw_candidates.append((clean_title, body, ""))
    if not raw_candidates:
        bullet_candidates = []
        for line in lines:
            match = re.match(r"^[-*]\s*(?:[A-Za-z0-9一二三四五六七八九十]+)\s*[｜|]\s*([^：:]+)[：:]\s*(.+)$", line.strip())
            if match:
                bullet_candidates.append((match.group(1).strip(), match.group(2).strip(), ""))
        raw_candidates = bullet_candidates
    if not raw_candidates and text.strip():
        raw_candidates = [("默认开场", text.strip(), "")]
    output = [f"# {display_name}·开场候选"]
    if shared_blocks:
        output += ["", "## 共同手法", "", "\n\n".join(shared_blocks).strip()]
    for index, (title, body, category) in enumerate(raw_candidates, start=1):
        output += ["", f"## opening-{index:02d}｜{title}", "", "- 状态：推荐" if index == 1 else "- 状态：可用"]
        if category:
            output.append(f"- 分类：{category}")
        output += ["", body.strip()]
    return "\n".join(output).strip()


def list_openings(project: Path, profile_id: str, revision: str | None = None) -> dict[str, Any]:
    snapshot = profile_snapshot(project, profile_id, revision)
    catalog = parse_openings((snapshot / PROFILE_FILES["开场候选"]).read_text(encoding="utf-8"))
    if not catalog["candidates"]:
        raise StudioError(f"Profile 开场候选尚未规范化: {profile_id}")
    return {
        "profile_id": profile_id,
        "revision": load_yaml(snapshot / PROFILE_CONFIG).get("current_revision", revision),
        "shared": catalog["shared"],
        "candidates": [{key: item[key] for key in ("id", "title", "status")} for item in catalog["candidates"]],
    }


def resolve_opening(project: Path, profile_id: str, revision: str, opening_id: str) -> dict[str, str]:
    if not OPENING_ID_RE.fullmatch(opening_id):
        raise StudioError(f"非法开场候选 ID: {opening_id}")
    snapshot = profile_snapshot(project, profile_id, revision)
    catalog = parse_openings((snapshot / PROFILE_FILES["开场候选"]).read_text(encoding="utf-8"))
    match = next((item for item in catalog["candidates"] if item["id"] == opening_id), None)
    if not match:
        raise StudioError(f"开场候选不存在: {profile_id}@{revision}/{opening_id}")
    combined = "\n\n".join(part for part in (catalog["shared"], match["body"]) if part.strip()).strip()
    return {"id": opening_id, "title": match["title"], "text": combined, "sha256": hashlib.sha256(combined.encode("utf-8")).hexdigest()}


def profile_root(project: Path, profile_id: str) -> Path:
    return project / PROFILE_DIR / safe_name(profile_id)


def canonical_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if int(payload.get("schema_version", 0)) != PROFILE_SCHEMA:
        raise StudioError(f"Profile payload schema_version 必须为 {PROFILE_SCHEMA}")
    profile_id = safe_name(str(payload.get("profile_id", "")))
    display_name = str(payload.get("display_name", "")).strip()
    if not display_name:
        raise StudioError("Profile display_name 不能为空")
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        raise StudioError("Profile payload sections 必须是对象")
    normalized_sections = {}
    for key, filename in PROFILE_FILES.items():
        value = sections.get(key, f"# {display_name}·{key}")
        if not isinstance(value, str):
            raise StudioError(f"Profile section {key} 必须是字符串")
        normalized = value.strip() or f"# {display_name}·{key}"
        if key == "开场候选":
            normalized = normalize_openings(normalized, display_name)
        normalized_sections[key] = normalized
    if not section_has_content(normalized_sections["NSFW"]):
        raise StudioError("Profile NSFW 不能为空；没有已确认内容时也应明确写为有意留白")
    problems = voice_errors(normalized_sections["语料"], "Profile 语料")
    if problems:
        raise StudioError("Profile 声线不完整：\n- " + "\n- ".join(problems))
    anchors = payload.get("anchors", [])
    aliases = payload.get("aliases", [])
    conflicts = payload.get("conflicts", [])
    if not all(isinstance(value, str) and value.strip() for value in anchors):
        raise StudioError("Profile anchors 必须是非空字符串数组")
    if not all(isinstance(value, str) and value.strip() for value in aliases):
        raise StudioError("Profile aliases 必须是非空字符串数组")
    if not isinstance(conflicts, list):
        raise StudioError("Profile conflicts 必须是数组")
    return {
        "schema_version": PROFILE_SCHEMA,
        "profile_id": profile_id,
        "display_name": display_name,
        "aliases": list(dict.fromkeys(value.strip() for value in aliases)),
        "anchors": list(dict.fromkeys(value.strip() for value in anchors)),
        "sections": normalized_sections,
        "conflicts": conflicts,
    }


def materialize_section_files(payload: dict[str, Any], base: Path) -> dict[str, Any]:
    files = payload.get("section_files")
    if files is None:
        return payload
    if not isinstance(files, dict):
        raise StudioError("Profile payload section_files 必须是对象")
    result = dict(payload)
    sections = dict(result.get("sections", {}))
    for key, value in files.items():
        if key not in PROFILE_FILES or not isinstance(value, str):
            raise StudioError(f"非法 Profile section_files 项: {key}")
        path = Path(value)
        if not path.is_absolute():
            path = (base / path).resolve()
        sections[key] = path.read_text(encoding="utf-8")
    result["sections"] = sections
    result.pop("section_files", None)
    return result


def revision_for(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def source_digest(path: Path | None) -> str:
    if path is None:
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_config(payload: dict[str, Any], revision: str, source: Path | None) -> dict[str, Any]:
    return {
        "schema_version": PROFILE_CONFIG_SCHEMA,
        "profile_id": payload["profile_id"],
        "display_name": payload["display_name"],
        "aliases": payload["aliases"],
        "status": "needs-review" if payload["conflicts"] else "ready",
        "anchors": payload["anchors"],
        "current_revision": revision,
        "source": {
            "name": source.name if source else "",
            "sha256": source_digest(source),
        },
    }


def write_profile(target: Path, payload: dict[str, Any], revision: str, source: Path | None) -> None:
    target.mkdir(parents=True, exist_ok=True)
    write_yaml(target / PROFILE_CONFIG, render_config(payload, revision, source))
    for key, filename in PROFILE_FILES.items():
        atomic_write_text(target / filename, payload["sections"][key])


def store_import_artifacts(project: Path, payload: dict[str, Any], revision: str, source: Path | None, pending: bool) -> Path:
    state = "待确认" if pending else "导入记录"
    target = project / PROFILE_DIR / payload["profile_id"] / state / revision
    target.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target / "profile-payload.json", payload)
    if source:
        shutil.copy2(source, target / source.name)
    return target


def profile_snapshot(project: Path, profile_id: str, revision: str | None = None) -> Path:
    root = profile_root(project, profile_id)
    config = load_yaml(root / PROFILE_CONFIG)
    if not config:
        raise StudioError(f"Profile 不存在: {profile_id}")
    current = str(config.get("current_revision", ""))
    revision = revision or current
    if revision == current:
        return root
    history = root / "历史" / safe_name(revision)
    if not history.is_dir():
        raise StudioError(f"Profile revision 不存在: {profile_id}@{revision}")
    return history


def archive_current(project: Path, profile_id: str) -> None:
    root = profile_root(project, profile_id)
    config = load_yaml(root / PROFILE_CONFIG)
    revision = str(config.get("current_revision", ""))
    if not revision:
        raise StudioError(f"Profile 缺少 current_revision: {profile_id}")
    target = root / "历史" / revision
    if target.exists():
        return
    target.mkdir(parents=True)
    for filename in (PROFILE_CONFIG, *PROFILE_FILES.values()):
        source = root / filename
        if source.exists():
            shutil.copy2(source, target / filename)


def profile_diff(project: Path, profile_id: str, payload: dict[str, Any]) -> str:
    current = profile_snapshot(project, profile_id)
    blocks = []
    for key, filename in PROFILE_FILES.items():
        before = (current / filename).read_text(encoding="utf-8").splitlines()
        after = payload["sections"][key].splitlines()
        delta = list(difflib.unified_diff(before, after, fromfile=f"current/{filename}", tofile=f"candidate/{filename}", lineterm=""))
        if delta:
            blocks.append("\n".join(delta))
    return "\n\n".join(blocks) or "无内容变化。"


def import_profile(project: Path, payload_path: Path, source: Path | None = None) -> dict[str, Any]:
    raw = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise StudioError("Profile payload 必须是 JSON 对象")
    payload = canonical_payload(materialize_section_files(raw, payload_path.parent))
    revision = revision_for(payload)
    root = profile_root(project, payload["profile_id"])
    if root.exists():
        current = load_yaml(root / PROFILE_CONFIG)
        if current.get("current_revision") == revision:
            return {"status": "unchanged", "profile_id": payload["profile_id"], "revision": revision}
        candidate = store_import_artifacts(project, payload, revision, source, pending=True)
        atomic_write_text(candidate / "差异报告.md", "# Profile 重导差异\n\n" + profile_diff(project, payload["profile_id"], payload))
        return {"status": "staged", "profile_id": payload["profile_id"], "revision": revision, "candidate": str(candidate)}
    write_profile(root, payload, revision, source)
    record = store_import_artifacts(project, payload, revision, source, pending=False)
    return {"status": "imported", "profile_id": payload["profile_id"], "revision": revision, "record": str(record)}


def apply_profile(project: Path, profile_id: str, revision: str) -> dict[str, Any]:
    profile_id = safe_name(profile_id)
    revision = safe_name(revision)
    candidate = project / PROFILE_DIR / profile_id / "待确认" / revision
    payload_path = candidate / "profile-payload.json"
    if not payload_path.exists():
        raise StudioError(f"待确认 Profile revision 不存在: {profile_id}@{revision}")
    payload = canonical_payload(json.loads(payload_path.read_text(encoding="utf-8")))
    if payload["profile_id"] != profile_id or revision_for(payload) != revision:
        raise StudioError("待确认 Profile 的 ID 或 revision 不匹配")
    archive_current(project, profile_id)
    source_files = [path for path in candidate.iterdir() if path.name not in {"profile-payload.json", "差异报告.md"}]
    write_profile(profile_root(project, profile_id), payload, revision, source_files[0] if source_files else None)
    record = project / PROFILE_DIR / profile_id / "导入记录" / revision
    if record.exists():
        raise StudioError(f"导入记录已存在: {record}")
    record.parent.mkdir(parents=True, exist_ok=True)
    candidate.rename(record)
    return {"status": "applied", "profile_id": profile_id, "revision": revision, "record": str(record)}


def set_profile_voice(project: Path, profile_id: str, voice_path: Path) -> dict[str, Any]:
    """Replace a constructed voice corpus while preserving Profile revision history."""
    profile_id = safe_name(profile_id)
    root = profile_root(project, profile_id)
    config = load_yaml(root / PROFILE_CONFIG)
    if not config:
        raise StudioError(f"Profile 不存在: {profile_id}")
    voice = voice_path.read_text(encoding="utf-8").strip()
    problems = voice_errors(voice, f"Profile {profile_id}/语料.md")
    if problems:
        raise StudioError("Profile 声线不完整：\n- " + "\n- ".join(problems))
    sections = {
        key: (root / filename).read_text(encoding="utf-8")
        for key, filename in PROFILE_FILES.items()
    }
    sections["语料"] = voice
    payload = canonical_payload({
        "schema_version": PROFILE_SCHEMA,
        "profile_id": profile_id,
        "display_name": str(config.get("display_name", profile_id)),
        "aliases": list(config.get("aliases", [])),
        "anchors": list(config.get("anchors", [])),
        "sections": sections,
        "conflicts": [],
    })
    revision = revision_for(payload)
    old_revision = str(config.get("current_revision", ""))
    if revision == old_revision:
        return {"status": "unchanged", "profile_id": profile_id, "revision": revision}
    archive_current(project, profile_id)
    atomic_write_text(root / PROFILE_FILES["语料"], voice)
    config["current_revision"] = revision
    config["status"] = "ready"
    write_yaml(root / PROFILE_CONFIG, config)
    record = project / PROFILE_DIR / profile_id / "导入记录" / revision
    record.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record / "profile-payload.json", payload)
    atomic_write_text(record / "修订说明.md", "# Profile 声线修订\n\n声线在构筑期按人设补完；未使用故事或会话作为反推来源。")
    return {"status": "applied", "profile_id": profile_id, "revision": revision, "previous_revision": old_revision, "record": str(record)}


def list_profiles(project: Path) -> list[dict[str, Any]]:
    root = project / PROFILE_DIR
    if not root.exists():
        return []
    result = []
    for path in sorted(item for item in root.iterdir() if item.is_dir()):
        config = load_yaml(path / PROFILE_CONFIG)
        if config:
            result.append(config)
    return result


def show_profile(project: Path, profile_id: str, revision: str | None = None) -> dict[str, Any]:
    snapshot = profile_snapshot(project, profile_id, revision)
    config = load_yaml(snapshot / PROFILE_CONFIG)
    return {
        "config": config,
        "path": str(snapshot),
        "files": {key: str(snapshot / filename) for key, filename in PROFILE_FILES.items()},
    }


def validate_profile(project: Path, profile_id: str) -> list[str]:
    errors = []
    root = profile_root(project, profile_id)
    config = load_yaml(root / PROFILE_CONFIG)
    if int(config.get("schema_version", 0)) not in {PROFILE_SCHEMA, PROFILE_CONFIG_SCHEMA}:
        errors.append(f"Profile {profile_id} schema_version 非法")
    if config.get("profile_id") != profile_id:
        errors.append(f"Profile {profile_id} 配置 ID 不一致")
    revision = str(config.get("current_revision", ""))
    if not revision:
        errors.append(f"Profile {profile_id} 缺少 current_revision")
    for filename in PROFILE_FILES.values():
        if not (root / filename).exists():
            errors.append(f"Profile {profile_id} 缺少 {filename}")
    nsfw_path = root / PROFILE_FILES["NSFW"]
    if nsfw_path.exists() and not section_has_content(nsfw_path.read_text(encoding="utf-8")):
        errors.append(f"Profile {profile_id}/NSFW.md 没有有效正文；无已确认内容时应明确写为有意留白")
    voice_path = root / PROFILE_FILES["语料"]
    if voice_path.exists():
        errors.extend(voice_errors(voice_path.read_text(encoding="utf-8"), f"Profile {profile_id}/语料.md"))
    return errors


def relation_sections_for_characters(text: str, characters: set[str]) -> str:
    headings = list(re.finditer(r"^(#{1,4})\s+(.+)$", text, re.M))
    if not headings:
        return text if any(name in text for name in characters) else ""
    selected = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[heading.start():end].strip()
        title = heading.group(2)
        if index == 0 or any(name in title for name in characters):
            selected.append(block)
    return "\n\n".join(selected)


def profile_context(project: Path, session_config: dict[str, Any], active_characters: set[str]) -> tuple[list[tuple[str, str]], dict[str, str]]:
    raw_profile_id = session_config.get("profile_id")
    raw_revision = session_config.get("profile_revision")
    if not raw_profile_id:
        return [], {}
    profile_id = str(raw_profile_id)
    revision = str(raw_revision) if raw_revision else ""
    snapshot = profile_snapshot(project, profile_id, revision or None)
    config = load_yaml(snapshot / PROFILE_CONFIG)
    sections = [("当前 User Profile", (snapshot / "角色.md").read_text(encoding="utf-8"))]
    project_config = load_yaml(project / "项目配置.yaml")
    scope = project_config.get("story_scope", {}) if isinstance(project_config.get("story_scope"), dict) else {}
    mode = scope.get("mode", "character-hub")
    relation = (snapshot / "关系接口.md").read_text(encoding="utf-8")
    anchors = {str(value) for value in config.get("anchors", [])}
    if mode == "character-hub":
        sections.append(("Profile 与正史关系接口", relation))
    elif active_characters & anchors:
        filtered_relation = relation_sections_for_characters(relation, active_characters & anchors)
        if filtered_relation:
            sections.append(("Profile 与正史关系接口", filtered_relation))
    nsfw = (snapshot / "NSFW.md").read_text(encoding="utf-8")
    project_nsfw = project_config.get("nsfw", {}) if isinstance(project_config.get("nsfw"), dict) else {}
    if project_nsfw.get("enabled", True) and nsfw.strip():
        sections.append(("Profile 成人设定", nsfw))
    voice = (snapshot / "语料.md").read_text(encoding="utf-8")
    if voice.strip():
        sections.append(("Profile 声线", voice))
    return sections, {"profile_id": profile_id, "profile_revision": revision or str(config.get("current_revision", ""))}
