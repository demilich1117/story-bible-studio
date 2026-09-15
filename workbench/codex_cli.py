"""Locate a native Codex CLI without persisting versioned machine paths."""
import os
import shutil
from pathlib import Path

from studio_core import StudioError


def codex_executable():
    override = os.environ.get("STORY_STUDIO_CODEX")
    if override:
        path = Path(override).resolve()
        if path.is_file() and (os.name != "nt" or path.suffix.lower() in {".exe", ".com"}):
            return str(path)
        raise StudioError("STORY_STUDIO_CODEX 路径无效；请指向 Codex CLI 的原生可执行文件。")
    found = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if found:
        return str(Path(found).resolve())
    if os.name == "nt":
        candidates = [Path.home() / ".local/bin/codex.exe", Path.home() / ".codex/bin/codex.exe"]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            folder = Path(local) / "OpenAI/Codex/bin"
            candidates.extend(sorted(folder.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True))
        # npm exposes a .cmd wrapper; execute only the underlying native binary.
        wrapper = shutil.which("codex.cmd")
        if wrapper:
            packages = Path(wrapper).parent / "node_modules/@openai"
            candidates.extend(packages.glob("codex*/vendor/*/codex/codex.exe"))
            candidates.extend(packages.glob("codex/node_modules/@openai/codex*/vendor/*/codex/codex.exe"))
        for path in candidates:
            if path.is_file():
                return str(path.resolve())
    raise StudioError("未找到 Codex CLI。请先安装 CLI，或用 STORY_STUDIO_CODEX 指向本机 codex.exe。")
