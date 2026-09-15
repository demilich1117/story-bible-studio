"""Local, opt-in CLI dispatch. Only the agent may prepare/commit story content."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
from pathlib import Path

from bootstrap import ROOT
from studio_bible_workbench import short_ticket
from studio_core import StudioError, LockError, atomic_write_json, session_lock, utc_now
from studio_construction_history import read_event
from opencode_server import OpenCodeServer
from codex_models import discover_models, reasoning_value
from codex_cli import codex_executable

ACTIVE = {"starting", "running", "stopping"}


def opencode_installation():
    """Inspect only known installation paths; the desktop executable is not a CLI."""
    desktops, binaries = [], []
    local, roaming = os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")
    if local:
        for name in ("@opencode-aidesktop", "OpenCode"):
            folder = Path(local) / "Programs" / name
            desktops.append(folder / "OpenCode.exe")
            binaries.append(folder / "resources/opencode-cli.exe")
    if roaming:
        cache = Path(roaming) / "OpenCode/cli"
        if cache.is_dir():
            binaries.extend(sorted(cache.glob("*/opencode-cli.exe"), key=lambda p: p.stat().st_mtime, reverse=True))
    return next((p for p in desktops if p.is_file()), None), next((p for p in binaries if p.is_file()), None)


def executable(provider):
    if provider not in {"codex", "opencode"}:
        raise StudioError("请选择 Codex 或 OpenCode")
    if provider == "codex":
        return codex_executable()
    value = os.environ.get("STORY_STUDIO_" + provider.upper()) or shutil.which(provider)
    desktop = None
    if not value and provider == "opencode":
        desktop, value = opencode_installation()
    if not value:
        if desktop:
            raise StudioError("已检测到 OpenCode 桌面版，但未找到独立 CLI。直接生成需要 opencode run；请安装 CLI 或用 STORY_STUDIO_OPENCODE 指定它的路径，也可复制小票到桌面版。")
        raise StudioError(f"未找到 {provider} CLI；安装并登录后重启工作台，或使用复制小票。")
    path = Path(value).resolve()
    # Never send prompts through cmd.exe / shell wrappers.
    if not path.is_file() or (os.name == "nt" and path.suffix.lower() not in {".exe", ".com"}):
        raise StudioError(f"请用 STORY_STUDIO_{provider.upper()} 指向本机 CLI 可执行文件（Windows 使用 .exe）。")
    return str(path)


def model_id(provider, value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 240 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value):
        raise StudioError("模型 ID 无效；请填写平台模型 ID，不要填写命令或参数。")
    if provider in {"opencode", "opencode_server"} and ("/" not in value or not all(value.split("/", 1))):
        raise StudioError("OpenCode 模型须使用 provider/model 格式。")
    return value


def platform_policy(provider):
    if provider == "codex":
        return "Platform: Codex."
    return ("Platform: OpenCode; ignore Luna/subagents. The main agent handles compaction and recall; "
            "all memory thresholds still apply. Do not change story settings.")


def dispatch_prompt(workspace, ticket_path, provider):
    return (f"Follow {ROOT / 'workbench/agent-guide.md'} (read once per task). "
            "Execute only this ticket; its entry point already prepares or resumes the operation. "
            "Review and commit through the core before replying.\n"
            + platform_policy(provider) + "\n\n" + short_ticket(workspace, Path(ticket_path)))


def command(provider, binary, workspace, prompt, server=None, platform_session=None, model=None, reasoning_effort=None):
    model = model_id(provider, model)
    reasoning_effort = reasoning_value(provider, reasoning_effort)
    if provider == "codex":
        return [binary, "exec", "--json", "--color", "never", "--sandbox", "workspace-write",
                "-c", 'approval_policy="never"', "--skip-git-repo-check", "-C", str(workspace),
                *(["--model", model] if model else []),
                *(["-c", f'model_reasoning_effort="{reasoning_effort}"'] if reasoning_effort else []), "-"], prompt
    args = [binary, "run", "--format", "json", "--dir", str(workspace)]
    if model:
        args += ["--model", model]
    if provider == "opencode_server":
        args += ["--attach", server.url, "--session", platform_session]
    return [*args, prompt], None


class AgentRunner:
    def __init__(self, service):
        self.service = service
        self.folder = service.workspace / ".workbench/agent-jobs"
        service.contained(self.folder, service.workspace)
        self.mutex = threading.RLock()
        self.active = None
        self.process = None
        self.worker = None
        self.cancel = threading.Event()
        self.closed = False
        self.codex_catalog = None

    def providers(self):
        rows = []
        for name in ("codex", "opencode"):
            try:
                executable(name)
                rows.append({"id": name, "available": True})
            except StudioError as exc:
                desktop = name == "opencode" and opencode_installation()[0] is not None
                rows.append({"id": name, "available": False, "desktop_available": desktop, "reason": str(exc)})
        try:
            conn = OpenCodeServer.configured(self.service.workspace)
            executable("opencode")
            version = conn.health()
            rows.append({"id": "opencode_server", "available": True, "server_url": conn.url,
                         "version": version, "hint": f"连接 {conn.url}（OpenCode {version}）。桌面端连接同一服务并打开此工作区即可查找任务。"})
        except StudioError as exc:
            rows.append({"id": "opencode_server", "available": False, "reason": str(exc)})
        return {"providers": rows}

    def ticket_info(self, ticket_path):
        """Read only a saved ticket, without consuming it or building context."""
        path = Path(ticket_path).resolve()
        if not path.is_relative_to(self.service.projects_root):
            raise StudioError("小票不属于当前工作区")
        parts = path.relative_to(self.service.projects_root).parts
        bible = len(parts) == 5 and parts[1:4] == (".workbench", "bible", "requests")
        session = len(parts) == 6 and parts[1] == "会话" and parts[3:5] in {
            (".workbench", "requests"), (".workbench", "recovery")}
        if not (bible or session):
            raise StudioError("只接受工作台保存的小票")
        project = parts[0]
        self.service.locate(project, parts[2] if session else None)
        req = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(req, dict) or req.get("project") != project:
            raise StudioError("小票目标不匹配")
        if session and req.get("session") != parts[2]:
            raise StudioError("小票会话不匹配")
        recovery = req.get("ticket_type") == "bible-recovery" if bible else parts[4] == "recovery"
        rid = req.get("id")
        expected = (req.get("operation_id") or "diagnostic") if session and recovery else rid
        if not isinstance(expected, str) or path.name != expected + ".json":
            raise StudioError("小票 ID 不匹配")
        if recovery and not req.get("operation_id"):
            raise StudioError("诊断小票请复制给 Agent 检查，不能直接生成。")
        return {"ticket_path": str(path), "project": project, "session": parts[2] if session else None,
                "kind": "bible" if bible else "session", "recovery": recovery,
                "request_id": rid, "operation_id": req.get("operation_id"), "topic_id": req.get("topic_id")}

    def result(self, info):
        if info["kind"] == "bible":
            if info["recovery"]:
                return self.service.bible_operation(info["project"], operation_id=info["operation_id"])
            return self.service.bible_view(info["project"], request_id=info["request_id"])
        if info["recovery"]:
            return self.service.operation(info["project"], info["session"], operation_id=info["operation_id"])
        return self.service.request_status(info["project"], info["session"], info["request_id"])

    def job_path(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise StudioError("任务 ID 无效")
        path = self.folder / (job_id + ".json")
        self.service.contained(path, self.service.workspace)
        return path

    def status(self, job_id):
        # Windows cannot replace a JSON file while another thread has it open.
        # Coordinate polling with local writes as well as serializing generators.
        with self.mutex:
            return self._status(job_id)

    def _status(self, job_id):
        path = self.job_path(job_id)
        if not path.is_file():
            raise StudioError("生成任务不存在")
        job = json.loads(path.read_text(encoding="utf-8"))
        # A crashed backend is never silently restarted. A surviving writer's lock is retained.
        if job["status"] in ACTIVE and job_id != self.active:
            job["status"] = "unknown" if job.get("server_pending") or (self.folder / ".session.lock").exists() else "interrupted"
            job["message"] = "后台连接已中断；先确认原任务已停止，再重试或复制恢复小票。"
        return job

    def models(self, provider):
        if provider == "codex":
            catalog = discover_models(executable("codex"), self.service.workspace)
            self.codex_catalog = catalog
            return catalog
        if provider == "opencode_server":
            return OpenCodeServer.configured(self.service.workspace).models()
        if provider != "opencode":
            raise StudioError("请选择 Codex 或 OpenCode")
        try:
            result = subprocess.run([executable("opencode"), "models"], cwd=self.service.workspace,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                encoding="utf-8", errors="replace", timeout=15, shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, subprocess.TimeoutExpired):
            raise StudioError("模型列表读取失败或超时；可重试，或手动填写 provider/model。") from None
        if result.returncode:
            raise StudioError("无法读取 OpenCode 模型列表；请检查 CLI 配置，或手动填写 provider/model。")
        ids = set()
        for line in result.stdout.splitlines():
            try:
                value = model_id(provider, line.strip())
                if value:
                    ids.add(value)
            except StudioError:
                continue
        return {"models": [{"id": value, "name": value} for value in sorted(ids)],
                "hint": "来自本机 OpenCode 模型列表；可用权限以平台账号为准。"}

    def start(self, ticket_path, provider, retry=False, model=None, reasoning_effort=None):
        if type(retry) is not bool:
            raise StudioError("retry 必须为布尔值")
        model = model_id(provider, model)
        reasoning_effort = reasoning_value(provider, reasoning_effort)
        if reasoning_effort and self.codex_catalog:
            selected = next((row for row in self.codex_catalog["models"] if row["id"] == model), None)
            if selected and reasoning_effort not in selected["reasoning_efforts"]:
                raise StudioError("所选模型不支持此推理强度，请重新选择。")
        binary = executable("opencode" if provider == "opencode_server" else provider)
        conn = OpenCodeServer.configured(self.service.workspace) if provider == "opencode_server" else None
        info = self.ticket_info(ticket_path)
        job_id = hashlib.sha256(info["ticket_path"].encode()).hexdigest()[:32]
        with self.mutex:
            if self.closed:
                raise StudioError("工作台正在关闭")
            if self.active:
                if self.active == job_id:
                    return self.status(job_id)
                raise StudioError("当前工作台已有生成任务，请等待完成或停止后再发送。")
            path = self.job_path(job_id)
            if path.exists() and not retry:
                return self.status(job_id)
            self.folder.mkdir(parents=True, exist_ok=True)
            self.active = job_id
            self.cancel.clear()
            started = threading.Event()
            errors = []
            self.worker = threading.Thread(target=self._run,
                args=(job_id, info, provider, binary, started, errors, conn, model, reasoning_effort), daemon=True)
            self.worker.start()
        if not started.wait(10):
            raise StudioError("任务正在启动，请刷新查看状态；不要重复发送。")
        if errors:
            raise StudioError(errors[0])
        return self.status(job_id)

    def _save(self, job, **updates):
        with self.mutex:
            job.update(updates, updated_at=utc_now())
            atomic_write_json(self.job_path(job["id"]), job)

    def _completed(self, job, info, current, **updates):
        # Construction is a conversation: show the actual committed answer, which may
        # contain the next question, rather than the CLI's final "saved" acknowledgement.
        operation_id = current.get("operation_id") or info.get("operation_id")
        if info["kind"] == "bible" and operation_id:
            project, _ = self.service.locate(info["project"])
            event = read_event(project, operation_id)
            job["reply"] = event.get("data", {}).get("transcript", {}).get("assistant", job["reply"])
        self._save(job, status="completed", message="已提交，工作台会自动更新。", **updates)

    def _run(self, job_id, info, provider, binary, started, errors, conn=None, model=None, reasoning_effort=None):
        job = {"id": job_id, **info, "provider": provider, "created_at": utc_now(),
               "model": model, "reasoning_effort": reasoning_effort,
               "helper_policy": "session" if provider == "codex" else "main_agent_only",
               "status": "starting", "message": "正在启动 Agent", "reply": ""}
        proc = None
        watchdog = None
        try:
            # One dispatch at a time across all workbench processes for this workspace.
            # This is a dispatch lock, separate from short-lived story transaction locks.
            with session_lock(self.folder):
                # Durable protection: a remote writer can outlive both CLI and workbench.
                for path in self.folder.glob("*.json"):
                    previous = json.loads(path.read_text(encoding="utf-8"))
                    if previous.get("server_pending"):
                        raise StudioError("共享后台还有未确认停止的任务，请先在原任务点击停止并确认 session 已结束，再重试。")
                try:
                    current = self.result(info)
                    if current["status"] == "committed" and not current.get("needs_finish"):
                        self._completed(job, info, current)
                        return
                    if current["status"] in {"stale", "archived", "expired"}:
                        self._save(job, status="incomplete", message="原小票已过期或归档，请在工作台重新准备。")
                        return
                    self._save(job)
                    started.set()
                    prompt = dispatch_prompt(self.service.workspace, info["ticket_path"], provider)
                    if conn:
                        version = conn.health()
                        conn.check_workspace()
                        if self.cancel.is_set():
                            self._save(job, status="stopped", message="生成已停止，小票仍保留。")
                            return
                        sid = conn.create(f"Story Bible · {info['project']} · {job_id[:8]}")
                        self._save(job, platform_session=sid, server_url=conn.url, server_version=version)
                        args, stdin = command(provider, binary, self.service.workspace, prompt, conn, sid, model)
                    else:
                        args, stdin = command(provider, binary, self.service.workspace, prompt, None, None, model, reasoning_effort)
                    env = os.environ.copy()
                    env.pop("CODEX_THREAD_ID", None)
                    env["PYTHONIOENCODING"] = "utf-8"
                    if conn:
                        conn.env(env)
                    # Make the project's Python available to ticket commands even from desktop launchers.
                    python_dir = ROOT / ".venv-workbench" / ("Scripts" if os.name == "nt" else "bin")
                    if python_dir.is_dir():
                        env["PATH"] = str(python_dir) + os.pathsep + env.get("PATH", "")
                    with self.mutex:
                        if self.cancel.is_set():
                            self._save(job, status="stopped", message="生成已停止，小票仍保留。")
                            return
                        if conn:
                            # Persist before the child can send its prompt.
                            self._save(job, server_pending=True)
                        self.process = subprocess.Popen(args, cwd=self.service.workspace, env=env,
                            stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace", shell=False,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                            start_new_session=os.name != "nt")
                        proc = self.process
                    watchdog = threading.Timer(1800, lambda: self.stop(job_id))
                    watchdog.daemon = True
                    watchdog.start()
                    self._save(job, status="running", message="Agent 正在处理这张小票。")
                    if stdin:
                        proc.stdin.write(stdin)
                        proc.stdin.close()
                    for line in proc.stdout:
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(event, dict):
                            continue
                        if event.get("type") == "thread.started" and not conn:
                            job["platform_session"] = event.get("thread_id")
                        if event.get("sessionID") and not conn:
                            job["platform_session"] = event["sessionID"]
                        if conn and event.get("sessionID") not in {None, job["platform_session"]}:
                            raise StudioError("OpenCode CLI 返回了其他 session 的事件，已停止本次连接。")
                        item = event.get("item") or {}
                        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                            job["reply"] = str(item.get("text", ""))[-16000:]
                        if event.get("type") == "text":
                            job["reply"] = (job["reply"] + str((event.get("part") or {}).get("text", "")))[-16000:]
                        if event.get("type") == "error":
                            error = event.get("error") or event
                            job["reply"] = str(error.get("message", "平台返回错误，请检查 CLI 登录与权限。"))[-2000:]
                        if conn:
                            job["reply"] = conn.redact(job["reply"])
                    code = proc.wait()
                    if conn:
                        # Exiting the attached CLI alone is not proof the remote writer stopped.
                        self._settle_server(job, conn)
                    current = self.result(info)
                    if current["status"] == "committed" and not current.get("needs_finish"):
                        self._completed(job, info, current, exit_code=code)
                    elif self.cancel.is_set():
                        self._save(job, status="stopped", exit_code=code, message="生成已停止；已保存的草稿和小票仍保留。")
                    else:
                        self._save(job, status="failed" if code else "incomplete", exit_code=code,
                            message=f"Agent 已退出（{code}），尚未确认提交。检查下方回复或平台登录与权限后重试，也可复制恢复小票。")
                except Exception as exc:
                    if proc and proc.poll() is None:
                        self._terminate(proc)
                    if conn and job.get("server_pending"):
                        try:
                            self._settle_server(job, conn)
                        except StudioError:
                            self._save(job, status="unknown", message="CLI 连接已结束，但尚未确认服务器任务停止；请恢复服务连接后点击停止，确认前不会重复生成。")
                    if not job.get("server_pending"):
                        current = self.result(info)
                        if current["status"] == "committed" and not current.get("needs_finish"):
                            self._completed(job, info, current)
                        else:
                            self._save(job, status="failed", message=f"生成未完成：{conn.redact(exc) if conn else exc}")
        except LockError:
            errors.append("另一个工作台进程正在生成，或上次中断留下了调度锁。请先确认原进程状态，不能重复启动。")
        except Exception as exc:
            errors.append(str(exc))
        finally:
            if watchdog:
                watchdog.cancel()
            if proc:
                if proc.stdout:
                    proc.stdout.close()
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            with self.mutex:
                self.process = None
                self.active = None
            started.set()

    def stop(self, job_id):
        with self.mutex:
            if self.active != job_id:
                job = self.status(job_id)
                if not job.get("server_pending"):
                    return job
                if self.active:
                    raise StudioError("请先等待当前任务退出。")
                # A crash leaves this lock behind: never race an unowned surviving CLI.
                with session_lock(self.folder):
                    conn = OpenCodeServer.configured(self.service.workspace)
                    if conn.url != job.get("server_url"):
                        raise StudioError("连接地址已改变，请恢复原任务的 OpenCode 服务地址后再停止。")
                    self._settle_server(job, conn)
                    current = self.result(job)
                    if current["status"] == "committed" and not current.get("needs_finish"):
                        self._completed(job, job, current)
                    else:
                        self._save(job, status="stopped", message="已确认共享后台 session 停止；原小票和草稿保留，可重试。")
                return job
            self.cancel.set()
            job = self.status(job_id)
            self._save(job, status="stopping", message="正在停止生成并确认后台状态。")
            proc = self.process
            if proc and proc.poll() is None:
                self._terminate(proc)
        return self.status(job_id)

    def _settle_server(self, job, conn):
        conn.abort(job["platform_session"])
        self._save(job, server_pending=False)

    @staticmethod
    def _terminate(proc):
        if os.name == "nt":
            subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"),
                            "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
        else:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
            proc.wait(timeout=5)

    def close(self):
        with self.mutex:
            self.closed = True
            job_id = self.active
        if job_id:
            self.stop(job_id)
        if self.worker:
            self.worker.join(timeout=15)
