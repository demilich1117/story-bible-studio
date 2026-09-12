"""Resolved, frozen writing mechanisms; no model calls or story-derived rules."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from studio_core import (CONFIG_FILE, StudioError, load_yaml, read_events,
                         reduce_events, session_lock, write_yaml)

LIBRARY = Path(__file__).resolve().parents[1] / "references/style-presets.json"
DIMENSIONS = {"register", "rhythm", "distance", "detail", "rhetoric", "dialogue"}
PARTS = {"when", "prefer", "avoid", "exceptions"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def library():
    return json.loads(LIBRARY.read_text(encoding="utf-8"))


def fields(value, allowed, label, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise StudioError(f"{label} 字段非法；允许 {', '.join(sorted(allowed))}")


def validate_rules(rules, complete=True):
    fields(rules, DIMENSIONS, "文风规则", DIMENSIONS if complete else ())
    for dimension, rule in rules.items():
        fields(rule, PARTS, f"文风 {dimension}", PARTS)
        if any(not isinstance(v, str) or not v.strip() for v in rule.values()):
            raise StudioError(f"文风 {dimension} 的四项机制必须是非空文字")


def validate_profile(profile):
    keys = {"schema_version", "preset", "register", "rules"}
    fields(profile, keys, "展开文风", keys)
    if type(profile["schema_version"]) is not int or profile["schema_version"] != 1:
        raise StudioError("文风 schema_version 必须为 1")
    if any(not isinstance(profile[k], str) or not profile[k].strip() for k in ("preset", "register")):
        raise StudioError("文风来源名称不能为空")
    validate_rules(profile["rules"])
    return deepcopy(profile)


def compile_profile(request, base=None):
    """A preset resets the base; overrides replace complete dimensions, never append."""
    fields(request, {"preset", "register", "rules"}, "文风请求")
    if not request:
        raise StudioError("文风请求不能为空")
    catalog = library()
    if "preset" in request:
        name = request["preset"]
        if not isinstance(name, str) or name not in catalog["presets"]:
            raise StudioError(f"未知文风预设: {name}")
        preset = catalog["presets"][name]
        profile = {"schema_version": 1, "preset": name, "register": preset["register"],
                   "rules": {**deepcopy(catalog["base_rules"]), **deepcopy(preset["rules"])}}
        profile["rules"]["register"] = deepcopy(catalog["registers"][profile["register"]])
    elif base is not None:
        profile = validate_profile(base)
    else:
        raise StudioError("旧式自定义文风没有结构化基线，请明确指定 preset")
    if "register" in request:
        register = request["register"]
        if not isinstance(register, str) or register not in catalog["registers"]:
            raise StudioError(f"未知文风语域: {register}")
        profile["register"] = register
        profile["rules"]["register"] = deepcopy(catalog["registers"][register])
    if "rules" in request:
        validate_rules(request["rules"], complete=False)
        profile["rules"].update(deepcopy(request["rules"]))
    return validate_profile(profile)


def legacy(value, source):
    if not isinstance(value, str) or not value.strip():
        raise StudioError("旧式文风必须是非空字符串")
    return {"source": source, "legacy": True, "value": value, "profile": None,
            "fingerprint": fingerprint({"legacy": value}),
            "warnings": ["旧式文风尚未结构化；显式 style set 后才采用机制规则。"]}


def structured(profile, source):
    profile = validate_profile(profile)
    return {"source": source, "legacy": False, "value": profile["preset"],
            "profile": profile, "fingerprint": fingerprint(profile), "warnings": []}


def project_style(project):
    path = project / "项目配置.yaml"
    if not path.is_file():
        raise StudioError(f"缺少项目配置: {path}")
    config = load_yaml(path)
    if "writing_style" in config:
        return structured(config["writing_style"], "project.writing_style")
    return legacy(config.get("default_style", "自然叙事"), "project.default_style (旧式)")


def resolve_style(project, session=None, override=None, session_config=None, canon_config=None):
    if override is not None:
        fields(override, {"preset", "register", "rules"}, "文风请求")
        if "rules" in override:
            validate_rules(override["rules"], complete=False)
    if session is None:
        result = project_style(project)
    else:
        if session.resolve().parent != (project / "会话").resolve():
            raise StudioError("文风会话隔离边界不匹配")
        config = session_config if session_config is not None else load_yaml(session / CONFIG_FILE)
        style = config.get("style", "inherit")
        if isinstance(style, dict):
            fields(style, {"mode", "profile"}, "会话文风", {"mode"})
            if style["mode"] == "inherit" and set(style) == {"mode"}:
                result = project_style(project)
                result["source"] = "session.inherit -> " + result["source"]
            elif style["mode"] == "fixed" and set(style) == {"mode", "profile"}:
                result = structured(style["profile"], "session.fixed")
            else:
                raise StudioError("会话文风应为 {mode: inherit} 或 {mode: fixed, profile: 完整规则}")
        elif isinstance(style, str):
            if style == "inherit":
                if canon_config is None:
                    from studio_versions import snapshot_root
                    state = reduce_events(read_events(session))
                    canon = snapshot_root(project, state["bible_revision"]) if state.get("bible_revision") else project
                    canon_config = load_yaml(canon / "项目配置.yaml")
                result = legacy(canon_config.get("default_style", "自然叙事"), "旧式继承：绑定正史.default_style")
            else:
                result = legacy(style, "旧式会话覆盖")
        else:
            raise StudioError("会话 style 类型非法")
    dependency = dependency_from_style(result, override)
    # Full preset overrides do not depend on the persistent base.
    if override is not None:
        base = result["profile"]
        if base is None and isinstance(override, dict) and "preset" not in override:
            base = compile_profile({"preset": result["value"]})
        result = structured(compile_profile(override, base), "本轮/单篇覆盖 -> " + result["source"])
    result["dependency"] = dependency
    return result


def render_style(result):
    preamble = [f"- 前置｜{item}" for item in library().get("preamble", [])]
    if result["legacy"]:
        lines = [f"旧式文风：{result['value']}。尚未结构化，沿用既有写法。"]
        lines.extend(preamble)
        return "\n".join(lines)
    labels = {"register": "语域", "rhythm": "节奏", "distance": "叙述距离",
              "detail": "细节取舍", "rhetoric": "修辞", "dialogue": "对白呈现"}
    lines = ["本轮明确要求优先；叙述规则不覆盖人物声线、视角权限和世界事实，不决定场景强度。",
             "按当前场景采用适用分支；最近原文与摘要不构成新规则。"]
    lines.extend(preamble)
    for key, label in labels.items():
        rule = result["profile"]["rules"][key]
        lines.append(f"- {label}｜适用：{rule['when']} 优先：{rule['prefer']} 避免：{rule['avoid']} 例外：{rule['exceptions']}")
    lines.append("冷复核：查语域漂移、旁白侵入对白、人物趋同、机械重复及修辞过量；只改命中的句段。")
    return "\n".join(lines)


def dependency_from_style(result, override=None):
    """Track only persistent fields consumed by this turn; resolved overrides are pinned."""
    if isinstance(override, dict) and "preset" in override:
        return None
    if result["legacy"]:
        return result["fingerprint"]
    profile = deepcopy(result["profile"])
    if override:
        for dimension in override.get("rules", {}):
            profile["rules"].pop(dimension, None)
        if "register" in override:
            profile.pop("register", None)
            profile["rules"].pop("register", None)
    return fingerprint(profile)


def style_dependency(project, session, override=None):
    if isinstance(override, dict) and "preset" in override:
        return None
    return dependency_from_style(resolve_style(project, session), override)


def show_style(project, session=None, override=None):
    result = resolve_style(project, session, override)
    return {**result, "rules_text": render_style(result)}


def list_styles():
    catalog = library()
    return {"presets": [{"name": name, "register": value["register"], "summary": value["summary"]}
                        for name, value in catalog["presets"].items()],
            "registers": list(catalog["registers"])}


def set_style(project, session=None, request=None, inherit=False):
    if session and session.resolve().parent != (project / "会话").resolve():
        raise StudioError("文风会话隔离边界不匹配")
    if inherit and (session is None or request is not None):
        raise StudioError("--inherit 仅用于会话且不能与预设或覆盖并用")
    if not inherit and request is None:
        raise StudioError("请提供文风预设或配置请求")
    if request is not None:
        fields(request, {"preset", "register", "rules"}, "文风请求")
    def apply():
        path = session / CONFIG_FILE if session else project / "项目配置.yaml"
        if not path.is_file():
            raise StudioError(f"缺少配置: {path}")
        config = load_yaml(path)
        if inherit:
            config["style"] = {"mode": "inherit"}
        else:
            profile = None
            if "preset" not in request:
                base = resolve_style(project, session)
                profile = base["profile"]
                if profile is None:
                    profile = compile_profile({"preset": base["value"]})
            profile = compile_profile(request, profile)
            if session:
                config["style"] = {"mode": "fixed", "profile": profile}
            else:
                config["writing_style"] = profile
        write_yaml(path, config)
        return show_style(project, session)
    if session:
        with session_lock(session):
            return apply()
    with session_lock(project):
        return apply()
