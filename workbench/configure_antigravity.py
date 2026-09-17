"""Opt-in setup of narrow core-command permissions for this workspace's agy CLI."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil


def permission_rule(workspace):
    root = Path(workspace).resolve()
    script = root / ".agents/skills/story-bible-studio/scripts/story_studio.py"
    if not script.is_file():
        raise ValueError("工作区缺少 Story Bible Studio 核心脚本")
    # Accept native Windows / POSIX paths, Python from PATH or this venv only.
    def paths(path):
        return "(?:" + "|".join(re.escape(p) for p in dict.fromkeys([str(path), path.as_posix()])) + ")"
    python = root / ".venv-workbench" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    interpreter = "(?:python(?:3|\\.exe)?|['\"]?" + paths(python) + "['\"]?)"
    script_arg = "['\"]?" + paths(script) + "['\"]?"
    # Exclude shell operators, substitutions, redirection and multiline commands.
    pattern = "^(?:&[ \\t]+)?" + interpreter + "(?:[ \\t]+-B)?[ \\t]+" + script_arg + "[ \\t]+(?:ticket|workbench)[ \\t]+[^;&|`$<>\\r\\n]+$"
    return "command(regex:" + pattern + ")"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rule = permission_rule(args.workspace)
    if not args.apply:
        print(json.dumps({"permissions": {"allow": [rule]}}, ensure_ascii=False, indent=2))
        return
    settings = Path.home() / ".gemini/antigravity-cli/settings.json"
    data = json.loads(settings.read_text(encoding="utf-8-sig")) if settings.exists() else {}
    permissions = data.setdefault("permissions", {})
    allowed = permissions.setdefault("allow", [])
    if not isinstance(allowed, list):
        raise ValueError("现有 permissions.allow 格式不兼容，未修改配置")
    if rule in allowed:
        print("本工作区的核心命令规则已配置。")
        return
    if settings.exists():
        shutil.copy2(settings, settings.with_name("settings.story-studio-backup-" + datetime.now().strftime("%Y%m%d%H%M%S%f") + ".json"))
    allowed.append(rule)
    settings.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.with_name("settings.story-studio.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(settings)
    print("已添加本工作区 ticket/workbench 核心命令权限；原有 allow/ask/deny 和其他设置保留。")


if __name__ == "__main__":
    main()
