"""Explicit runtime presets; custom settings remain authoritative."""
from copy import deepcopy

import yaml

from studio_core import CONFIG_FILE, StudioError, load_yaml, write_yaml


def preset(mode="quality"):
    if mode not in {"quality", "economy"}:
        raise StudioError(f"未知模式: {mode}")
    economy = mode == "economy"
    return {
        "mode": mode,
        "context_budget_chars": 40000 if economy else 80000,
        "recent_turns": 3 if economy else 5,
        "retrieval_top_k": 2 if economy else 5,
        "compression_cadence": 10 if economy else 16,
        "memory_policy": {
            "soft_context_ratio": .75, "hard_context_ratio": .9,
            "max_uncompressed_turns": 10 if economy else 16,
            "min_uncompressed_turns": 5 if economy else 6,
            "compact_on_scene_transition": False,
        },
    }


def resolve_policy(config, mode=None):
    if mode:
        return {**config, **preset(mode)}
    result = {**preset(config.get("mode", "quality")), **deepcopy(config)}
    result["memory_policy"] = {
        **preset(result["mode"])["memory_policy"], **config.get("memory_policy", {})}
    return result


def set_mode(project, mode, session=None):
    from studio_core import session_lock
    with session_lock(session or project):
        return _set_mode(project, mode, session)


def _set_mode(project, mode, session=None):
    path = session / CONFIG_FILE if session else project / "项目配置.yaml"
    config = load_yaml(path)
    if session:
        config.update(preset(mode))
    else:
        config["session_defaults"] = {**config.get("session_defaults", {}), **preset(mode)}
    write_yaml(path, config)
    return {"status": "configured", "mode": mode, "path": str(path)}


def config_view(project, session=None, writing=False, override=None):
    from studio_output import resolve_output, output_rules, CONFIG_KEYS
    from studio_core import output_config_snapshot
    if writing and session:
        raise StudioError("正式创作配置不能指定 RP 会话")
    path = session / CONFIG_FILE if session else project / "项目配置.yaml"
    document = load_yaml(path)
    target = document if session else document.get("writing_defaults" if writing else "session_defaults", {})
    target = deepcopy(target)
    fallback = writing and "prose" not in target
    if fallback:
        target["prose"] = deepcopy(document.get("session_defaults", {}).get("prose"))
    writing_status_default = writing and "status_bar" not in target
    if writing_status_default:
        target["status_bar"] = False
    effective = resolve_output(target, override)
    sources = effective.pop("output_sources")
    for key, source in list(sources.items()):
        if source == "saved":
            sources[key] = "session" if session else "writing_defaults" if writing else "session_defaults"
    if fallback and sources.get("prose") == "writing_defaults":
        sources["prose"] = "session_defaults fallback"
    if writing_status_default and sources.get("status_bar") == "writing_defaults":
        sources["status_bar"] = "writing builtin"
    return {"status": "configured", "scope": "session" if session else "writing" if writing else "project",
            "path": str(path), "effective": {k: effective.get(k) for k in sorted(CONFIG_KEYS)},
            "sources": sources, "output_config": output_config_snapshot(effective),
            "rules": output_rules(effective)}


def set_config(project, pairs=None, session=None, *, writing=False, reset=None, undo=False):
    """Atomic edits with a single undo record; short-RP changes stay in its layer."""
    from contextlib import nullcontext
    from studio_core import session_lock
    if writing and session:
        raise StudioError("正式创作配置不能指定 RP 会话")
    with session_lock(session or project):
        return _edit_config(project, pairs or [], session, writing, reset, undo)


def _edit_config(project, pairs, session, writing, reset, undo):
    from studio_output import (CONFIG_KEYS, PRESET_KEYS, SHORT_RP, CHILDREN,
                               apply_patch, resolve_output, validate_patch)
    path = session / CONFIG_FILE if session else project / "项目配置.yaml"
    config = load_yaml(path)
    target = config if session else config.setdefault("writing_defaults" if writing else "session_defaults", {})
    managed = CONFIG_KEYS | {"short_rp_overrides"}
    before = {k: deepcopy(v) for k, v in target.items() if k in managed}
    if undo:
        previous = target.get("_output_undo")
        if not isinstance(previous, dict):
            raise StudioError("没有可撤销的持续配置修改")
        for key in managed:
            target.pop(key, None)
        target.update(deepcopy(previous))
        target.pop("_output_undo", None)
        resolve_output(target)
        write_yaml(path, config)
        return config_view(project, session, writing)
    patch = {}
    changed = {}
    for pair in pairs:
        key, separator, raw = pair.partition("=")
        key = key.strip()
        if not separator or not key:
            raise StudioError(f"--set 必须是 key=value: {pair!r}")
        top = key.split(".")[0]
        if top not in CONFIG_KEYS:
            allowed = ", ".join(sorted(CONFIG_KEYS))
            raise StudioError(f"不支持即时配置的键: {top!r}（可选: {allowed}）")
        try:
            value = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise StudioError(f"非法 YAML 值: {key}") from exc
        cursor = patch
        parts = key.split(".")
        if len(parts) > 1:
            if parts[1] not in CHILDREN.get(top, set()):
                raise StudioError(f"未知配置路径: {key}")
            if len(parts) > 2 and not (top == "bilingual" and parts[1] == "languages" and len(parts) == 3 and parts[2]):
                raise StudioError(f"未知配置路径: {key}")
        for part in parts[:-1]:
            existing = cursor.get(part)
            if not isinstance(existing, dict):
                existing = {}
                cursor[part] = existing
            cursor = existing
        cursor[parts[-1]] = value
        changed[key] = value
    validate_patch(patch)
    active = patch.get("interaction_preset", target.get("interaction_preset", "regular"))
    if reset:
        if reset == "short-rp":
            target.pop("short_rp_overrides", None)
        elif reset not in CONFIG_KEYS:
            raise StudioError(f"未知重置模块: {reset}")
        elif active == "short-rp" and reset in PRESET_KEYS:
            custom = target.setdefault("short_rp_overrides", {})
            custom.pop(reset, None)
        else:
            defaults = load_yaml(project / "项目配置.yaml").get("session_defaults", {}) if session else {}
            if reset in defaults:
                target[reset] = deepcopy(defaults[reset])
            else:
                target.pop(reset, None)
    if active == "short-rp":
        local = {k: v for k, v in patch.items() if k in PRESET_KEYS}
        if local:
            # Start with the preset's stored range, not its disabled normalized range.
            storage = apply_patch(target, SHORT_RP)
            storage = apply_patch(storage, target.get("short_rp_overrides", {}))
            updated = apply_patch(storage, local)
            custom = target.setdefault("short_rp_overrides", {})
            custom.update({k: deepcopy(updated[k]) for k in local})
        patch = {k: v for k, v in patch.items() if k not in PRESET_KEYS}
    # In formal writing the old project prose default remains the fallback.
    if writing and "prose" in patch and "prose" not in target:
        target["prose"] = deepcopy(config.get("session_defaults", {}).get("prose"))
    updated = apply_patch(target, patch)
    resolve_output(updated)
    target.clear()
    target.update(updated)
    after = {k: deepcopy(v) for k, v in target.items() if k in managed}
    if after == before:
        return config_view(project, session, writing)
    target["_output_undo"] = before
    write_yaml(path, config)
    return {**config_view(project, session, writing), "changed": changed,
            "reset": reset}
