"""Official agy CLI adapter; never launch the Antigravity editor as an agent."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from studio_core import StudioError


def antigravity_executable():
    override = os.environ.get("STORY_STUDIO_ANTIGRAVITY")
    if os.name == "nt" and "STORY_STUDIO_ANTIGRAVITY" not in os.environ:
        # Existing desktop launchers can retain their old environment even after
        # an installer broadcasts PATH changes. Honor the user's saved location.
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                saved, kind = winreg.QueryValueEx(key, "STORY_STUDIO_ANTIGRAVITY")
                if kind in {winreg.REG_SZ, winreg.REG_EXPAND_SZ} and isinstance(saved, str):
                    override = os.path.expandvars(saved)
        except OSError:
            pass
    candidates = [override] if override else [shutil.which("agy")]
    if not override:
        if os.name == "nt":
            local_roots = [Path.home() / "AppData/Local"]
            if os.environ.get("LOCALAPPDATA"):
                local_roots.insert(0, Path(os.environ["LOCALAPPDATA"]))
            for local in dict.fromkeys(local_roots):
                candidates.append(local / "agy/bin/agy.exe")
            # MSIX-packaged terminals virtualize LOCALAPPDATA writes. A server
            # launched outside that package must use the physical cache path.
            for local in dict.fromkeys(local_roots):
                packages = local / "Packages"
                if packages.is_dir():
                    candidates.extend(sorted(packages.glob("OpenAI.Codex_*/LocalCache/Local/agy/bin/agy.exe")))
        else:
            candidates.append(Path.home() / ".local/bin/agy")
    for value in candidates:
        if not value:
            continue
        path = Path(value).resolve()
        if path.is_file() and (os.name != "nt" or path.suffix.lower() in {".exe", ".com"}):
            return str(path)
    raise StudioError("未找到独立 Antigravity CLI（agy）；请安装并登录，或用 STORY_STUDIO_ANTIGRAVITY 指定 agy 可执行文件。编辑器的 antigravity 命令不能用于生成。")


def command(binary, prompt, model=None, reasoning_effort=None):
    # stdin keeps multiline tickets out of shell parsing and Windows argv limits.
    args = [binary, "--input-format", "stream-json", "--output-format", "stream-json", "--print-timeout", "30m"]
    if model:
        args += ["--model", model]
    if reasoning_effort:
        args += ["--effort", reasoning_effort]
    return args, json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n"


def discover_models(binary, workspace):
    try:
        result = subprocess.run([binary, "models"], cwd=workspace, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace",
            timeout=20, shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired):
        raise StudioError("Antigravity 模型查询失败或超时；请检查 agy 安装和登录。") from None
    if result.returncode:
        raise StudioError("无法读取 Antigravity 模型；请先运行 agy 完成登录。")
    models = {}
    for line in result.stdout.splitlines():
        parts = re.split(r"\s+", line.strip(), maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*", parts[0]):
            models[parts[0]] = {"id": parts[0], "name": parts[1], "reasoning_efforts": ["low", "medium", "high"]}
    if not models:
        raise StudioError("Antigravity 未返回可识别的模型目录；请检查 CLI 版本或手动填写模型 ID。")
    return {"models": list(models.values()), "supports_reasoning_effort": True, "hint": "来自本机 Antigravity 模型目录；推理强度支持低、中、高，可用权限以账号为准。"}


def consume_event(job, event):
    kind = event.get("event")
    if kind not in {"init", "step_update", "result"}:
        return
    payload = event.get(kind)
    if not isinstance(payload, dict):
        payload = {}
    sid = event.get("conversation_id") or payload.get("conversation_id")
    if sid:
        if not isinstance(sid, str) or (job.get("platform_session") and job["platform_session"] != sid):
            raise StudioError("Antigravity 返回了其他 conversation 的事件，已停止本次任务。")
        job["platform_session"] = sid
    if kind == "step_update" and payload.get("step_type") == "agent_response":
        delta = payload.get("text_delta")
        if isinstance(delta, str):
            job["reply"] = (job["reply"] + delta)[-16000:]
    elif kind == "result":
        job["platform_status"] = payload.get("status")
        response = payload.get("response")
        if isinstance(response, str):
            job["reply"] = response[-16000:]
        if payload.get("status") != "SUCCESS":
            job["platform_error"] = "Antigravity 未正常完成；请检查 CLI 登录、模型权限与工具授权后重试。"


def read_diagnostics(stream, flags):
    """Drain stderr without storing raw paths, credentials, or model content."""
    try:
        for line in stream:
            lower = line.lower()
            if any(word in lower for word in ("authentication required", "not authenticated", "sign in", "log in", "login required")):
                flags["auth"] = True
            if any(word in lower for word in ("soft-denied", "permission denied", "requires approval", "permissions.allow", "permission required")):
                flags["permission"] = True
    except (OSError, ValueError):
        pass


def diagnostic_hint(flags):
    if flags.get("auth"):
        return "请先在终端运行 agy 完成登录，再重试原小票。"
    if flags.get("permission"):
        return "Antigravity 工具权限不足；请在 CLI 授权工作台所需的核心命令，再重试原小票。"
    return "检查 Antigravity 登录、模型及核心命令权限后重试，也可复制小票。"
