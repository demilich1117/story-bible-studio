"""Read the CLI's model picker over stdio, without creating a task or turn."""
from __future__ import annotations

import json
import contextlib
import os
import queue
import subprocess
import threading
import time

from studio_core import StudioError

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def reasoning_value(provider, value):
    if value is None or value == "":
        return None
    if provider != "codex" or not isinstance(value, str) or value not in EFFORTS:
        raise StudioError("推理强度无效；请从 Codex 推理强度选项中选择。")
    return value


def discover_models(binary, workspace, timeout=20):
    """Use one initialized connection, including pagination and a total deadline."""
    proc = None
    reader = None
    inbox = queue.Queue()
    deadline = time.monotonic() + timeout
    env = os.environ.copy()
    env.pop("CODEX_THREAD_ID", None)

    def read_lines():
        try:
            for line in proc.stdout:
                inbox.put(line)
        finally:
            inbox.put(None)

    def send(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def request(request_id, method, params):
        send({"id": request_id, "method": method, "params": params})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise queue.Empty
            line = inbox.get(timeout=remaining)
            if line is None:
                raise StudioError("Codex 模型查询已退出；请检查 CLI 登录、版本及配置后重试。")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if "error" in message:
                # Do not return raw CLI/config diagnostics or credentials to the page.
                raise StudioError("Codex 无法提供模型目录；请检查 CLI 登录、版本及配置后重试。")
            result = message.get("result")
            if not isinstance(result, dict):
                raise StudioError("Codex 模型目录格式无效，请更新 CLI 后重试。")
            return result

    try:
        proc = subprocess.Popen([binary, "app-server"], cwd=workspace, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        request(1, "initialize", {"clientInfo": {
            "name": "story_bible_studio", "title": "Story Bible Studio", "version": "1.0.0"}})
        send({"method": "initialized"})
        models, seen, cursors = [], set(), set()
        cursor = None
        for request_id in range(2, 102):
            params = {"limit": 100, "includeHidden": False}
            if cursor is not None:
                params["cursor"] = cursor
            result = request(request_id, "model/list", params)
            if not isinstance(result.get("data"), list):
                raise StudioError("Codex 模型目录格式无效，请更新 CLI 后重试。")
            for row in result["data"]:
                if not isinstance(row, dict) or row.get("hidden"):
                    continue
                model = row.get("model") or row.get("id")
                if not isinstance(model, str) or not model or model in seen:
                    continue
                seen.add(model)
                options = row.get("supportedReasoningEfforts")
                if not isinstance(options, list):
                    raise StudioError("Codex 模型缺少推理强度信息，请更新 CLI 后重试。")
                efforts = [item["reasoningEffort"] for item in options
                    if isinstance(item, dict) and item.get("reasoningEffort") in EFFORTS]
                models.append({"id": model, "name": row.get("displayName") or model,
                    "reasoning_efforts": list(dict.fromkeys(efforts)),
                    "default_reasoning_effort": row.get("defaultReasoningEffort"),
                    "is_default": bool(row.get("isDefault"))})
            cursor = result.get("nextCursor")
            if not cursor:
                return {"models": models, "supports_reasoning_effort": True,
                        "hint": "来自本机 Codex 模型目录；选择模型后可设置其支持的推理强度。"}
            if not isinstance(cursor, str) or cursor in cursors:
                raise StudioError("Codex 模型目录分页异常，请重试。")
            cursors.add(cursor)
        raise StudioError("Codex 模型目录分页过多，请重试。")
    except queue.Empty:
        raise StudioError("读取 Codex 模型目录超时；请检查登录与网络后重试。") from None
    except (OSError, ValueError):
        raise StudioError("无法读取 Codex 模型目录；请检查 CLI 登录、版本及配置后重试。") from None
    finally:
        if proc:
            if proc.stdin:
                with contextlib.suppress(OSError):
                    proc.stdin.close()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            if reader:
                reader.join(timeout=2)
            if proc.stdout:
                proc.stdout.close()
