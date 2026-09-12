"""Shared output settings, reversible presets and per-turn overlays.

Natural-language interpretation belongs to the writing agent. This module resolves
its explicit structured request; it never infers facts from names or dialogue.
"""
from copy import deepcopy
import re

from studio_core import (StudioError, normalize_agency, normalize_prose,
                         normalize_status_bar)

SHORT_RP = {
    "agency": "short-rp", "prose": {"enabled": True, "min_chars": 150, "max_chars": 350},
    "status_bar": {"enabled": False}, "initiative": "balanced", "span": "beat",
}
PRESET_KEYS = set(SHORT_RP)
OUTPUT_KEYS = PRESET_KEYS | {"person", "interaction_preset", "bilingual"}
EXTRA_KEYS = {"pov", "tense", "time_display", "nsfw_overlay"}
CONFIG_KEYS = OUTPUT_KEYS | EXTRA_KEYS
ENUMS = {
    "interaction_preset": {"regular", "short-rp"},
    "initiative": {"inherit", "follow", "balanced", "active"},
    "span": {"inherit", "beat", "scene", "timeskip"},
}
CHILDREN = {
    "prose": {"enabled", "min_chars", "max_chars"},
    "status_bar": {"enabled", "fields"},
    "bilingual": {"enabled", "strategy", "languages"},
    "time_display": {"mode", "format", "anchor"},
}


def merge(base, patch):
    result = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def normalize_bilingual(value=None):
    if value is None:
        value = {}
    if isinstance(value, bool):
        value = {"enabled": value}
    if not isinstance(value, dict) or set(value) - CHILDREN["bilingual"]:
        raise StudioError("bilingual 必须包含 enabled/strategy/languages 中的字段")
    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise StudioError("bilingual.enabled 必须是布尔值")
    if value.get("strategy", "world") != "world":
        raise StudioError("bilingual.strategy 只支持 world（世界内语言）")
    languages = value.get("languages", {})
    if not isinstance(languages, dict) or not all(
        isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
        for k, v in languages.items()
    ):
        raise StudioError("bilingual.languages 必须是角色到语言的非空字符串映射")
    return {"enabled": enabled, "strategy": "world", "languages": deepcopy(languages)}


def validate_patch(patch):
    if not isinstance(patch, dict):
        raise StudioError("配置覆盖必须是映射")
    for key, value in patch.items():
        if key not in CONFIG_KEYS:
            raise StudioError(f"不支持即时配置的键: {key}")
        if key in ENUMS and (not isinstance(value, str) or value not in ENUMS[key]):
            raise StudioError(f"非法 {key}: {value!r}；可选 {sorted(ENUMS[key])}")
        if isinstance(value, dict):
            if key not in CHILDREN or set(value) - CHILDREN[key]:
                raise StudioError(f"非法 {key} 子字段")
        if key == "person" and value not in ("first", "second", "third"):
            raise StudioError("person 请选择 first/second/third；已有自定义值可继续读取")
        if key == "agency":
            normalize_agency(value)
        if key == "prose":
            normalize_prose(None if value is True else value)
        if key == "status_bar":
            normalize_status_bar(value)
        if key == "bilingual":
            normalize_bilingual(value)
        if key in {"pov", "tense", "nsfw_overlay"} and not isinstance(value, str):
            raise StudioError(f"{key} 必须是字符串")
        if key == "time_display" and not isinstance(value, dict):
            raise StudioError("time_display 必须是映射")
    return patch


def editable_module(key, value):
    """Expand legacy shorthands before changing a leaf, retaining disabled state."""
    if key == "prose":
        if isinstance(value, dict):
            return deepcopy(value)
        if value is False or isinstance(value, str) and value.lower() in {"off", "none", "不限"}:
            return {"enabled": False, "min_chars": 1000, "max_chars": 1200}
        return normalize_prose(value)
    if key == "status_bar":
        return normalize_status_bar(value)
    if key == "bilingual":
        return normalize_bilingual(value)
    return deepcopy(value) if isinstance(value, dict) else {}


def apply_patch(base, patch):
    result = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict):
            result[key] = merge(editable_module(key, result.get(key)), value)
        elif key in {"prose", "status_bar", "bilingual"} and isinstance(value, bool):
            result[key] = merge(editable_module(key, result.get(key)), {"enabled": value})
        elif key == "prose" and isinstance(value, str) and value.lower() in {"off", "none", "不限"}:
            result[key] = merge(editable_module(key, result.get(key)), {"enabled": False})
        else:
            result[key] = deepcopy(value)
    return result


def resolve_output(config, override=None):
    override = {} if override is None else validate_patch(override)
    result = deepcopy(config)
    sources = {k: "saved" if k in config else "builtin" for k in CONFIG_KEYS}
    preset = override.get("interaction_preset", config.get("interaction_preset", "regular"))
    if not isinstance(preset, str) or preset not in ENUMS["interaction_preset"]:
        raise StudioError(f"非法 interaction_preset: {preset}")
    if preset == "short-rp":
        result = apply_patch(result, SHORT_RP)
        for key in PRESET_KEYS:
            sources[key] = "short-rp preset"
        custom = config.get("short_rp_overrides", {})
        if not isinstance(custom, dict) or set(custom) - PRESET_KEYS:
            raise StudioError("short_rp_overrides 只允许短 RP 预设模块")
        validate_patch(custom)
        result = apply_patch(result, custom)
        sources.update({k: "short-rp saved override" for k in custom})
    result = apply_patch(result, override)
    sources.update({k: "temporary" for k in override})
    result["interaction_preset"] = preset
    result["agency"] = normalize_agency(result.get("agency"))
    result["prose"] = normalize_prose(result.get("prose"))
    result["status_bar"] = normalize_status_bar(result.get("status_bar"))
    result["bilingual"] = normalize_bilingual(result.get("bilingual"))
    for key in ("initiative", "span"):
        value = result.get(key, "inherit")
        if not isinstance(value, str) or value not in ENUMS[key]:
            raise StudioError(f"非法 {key}: {value!r}")
        result[key] = value
    result["output_sources"] = sources
    return result


def output_rules(config):
    rules = []
    prose = normalize_prose(config.get("prose"))
    if any(value is not None for value in prose.values()):
        rules.append("字数是写作软目标：按目标份量一次起草，自然停笔；不为差几十字凑写、裁句或重新生成。"
                     "提交成功后不因字数提示重写本轮；只有用户明确要求严格字数或改写时才调整。")
    if config.get("interaction_preset") == "short-rp":
        rules.append("短 RP：允许只有对白、观察或等待，不要求局势每轮变化，不为凑字数补动作。"
                     "在需要 user 回应前自然停笔，不强制问句。当前明确模块覆盖优先于预设默认。")
    person = config.get("person")
    label = {"first": "我", "second": "你", "third": "他／她／姓名"}.get(person)
    if label:
        rules.append(f"旁白以{label}指代 user；不改变 NPC 对白称呼、POV 知情范围或代写权限。"
                     "正式创作未指定 user 角色时不应用，不据此更换整篇叙述者。")
    initiative = {"follow": "回应眼前事件，不主动引入新支线。", "balanced": "按人物动机自然推进。",
                  "active": "可引入有因果依据的事件、阻碍或机会，不替 user 作决定。"}
    span = {"beat": "完成眼前一次行动或回应。", "scene": "允许衔接多个节拍。",
            "timeskip": "允许概述重复过程、推进后续时点，但不得跨过需 user 决定的节点。"}
    if config.get("initiative") in initiative:
        rules.append("剧情主动性：" + initiative[config["initiative"]])
    if config.get("span") in span:
        rules.append("推进跨度：" + span[config["span"]])
    bilingual = config.get("bilingual", {})
    if bilingual.get("enabled"):
        rules.append("双语仅限直接对白，格式：“源语言对白”（中文翻译）。旁白、动作、内心、状态栏均保持中文。"
                     "逐说话角色判定世界内实际用语：用户明确指定（含 languages 覆盖）>已确立场景交谈语言>角色档案。"
                     "仅使用本包已有依据，不按姓名、作品产地或资料书写语言猜测。未知或无法可靠表达的虚构语言回退中文。"
                     "中文对白不重复翻译；译文保留原意和声线，不加解释；不赋予听者理解能力。"
                     "字数目标按中文阅读内容计，外语原句不重复计入；翻译括号紧跟闭引号。")
    return rules


def prose_metrics(text, snapshot):
    actual = len(re.sub(r"\s+", "", text))
    reading = text
    if snapshot.get("bilingual", {}).get("enabled"):
        # Only canonical paired direct speech is excluded. Ordinary parentheses,
        # inner thoughts, and unpaired speech remain in the count.
        reading = re.sub(r'“[^“”]*”\s*（([^（）]*(?:（[^（）]*）[^（）]*)*)）',
                         lambda m: m.group(1), reading)
    count = len(re.sub(r"\s+", "", reading))
    result = {"prose_chars": count, "prose_actual_chars": actual}
    bounds = snapshot.get("prose") or {}
    low, high = bounds.get("min_chars"), bounds.get("max_chars")
    # Keep the original writing target; allow small misses without a repair loop.
    soft_low = max(0, low - max(50, (low + 9) // 10)) if low is not None else None
    soft_high = high + max(50, (high + 9) // 10) if high is not None else None
    if (soft_low is not None and count < soft_low) or (soft_high is not None and count > soft_high):
        from studio_core import prose_range_text
        result["prose_warning"] = (f"正文 {count} 字，偏离目标 {prose_range_text(bounds)} 较多；"
                                   "仅供后续调整，不影响提交，不要据此重写本轮。")
    return result
