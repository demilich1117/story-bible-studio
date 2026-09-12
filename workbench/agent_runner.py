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

ACTIVE = {"starting", "running", "stopping"}


def executable(provider):
    if provider not in {"codex", "opencode"}:
        raise StudioError("请选择 Codex 或 OpenCode")
    value = os.environ.get("STORY_STUDIO_" + provider.upper()) or shutil.which(provider)
    if not value:
        raise StudioError(f"未找到 {provider} CLI；安装并登录后重启工作台，或使用复制小票。")
    path = Path(value).resolve()
    # Never send prompts through cmd.exe / shell wrappers.
    if not path.is_file() or (os.name == "nt" and path.suffix.lower() not in {".exe", ".com"}):
        raise StudioError(f"请用 STORY_STUDIO_{provider.upper()} 指向本机 CLI 可执行文件（Windows 使用 .exe）。")
    return str(path)


def command(provider, binary, workspace, prompt):
    if provider == "codex":
        return [binary, "exec", "--json", "--color", "never", "--sandbox", "workspace-write",
                "-c", 'approval_policy="never"', "--skip-git-repo-check", "-C", str(workspace), "-"], prompt
    return [binary, "run", "--format", "json", "--dir", str(workspace), prompt], None


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

    def providers(self):
        rows = []
        for name in ("codex", "opencode"):
            try:
                executable(name)
                rows.append({"id": name, "available": True})
            except StudioError as exc:
                rows.append({"id": name, "available": False, "reason": str(exc)})
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
        path = self.job_path(job_id)
        if not path.is_file():
            raise StudioError("生成任务不存在")
        job = json.loads(path.read_text(encoding="utf-8"))
        # A crashed backend is never silently restarted. A surviving writer's lock is retained.
        if job["status"] in ACTIVE and job_id != self.active:
            job["status"] = "unknown" if (self.folder / ".session.lock").exists() else "interrupted"
            job["message"] = "后台连接已中断；先确认原任务已停止，再重试或复制恢复小票。"
        return job

    def start(self, ticket_path, provider, retry=False):
        if type(retry) is not bool:
            raise StudioError("retry 必须为布尔值")
        binary = executable(provider)
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
                args=(job_id, info, provider, binary, started, errors), daemon=True)
            self.worker.start()
        if not started.wait(10):
            raise StudioError("任务正在启动，请刷新查看状态；不要重复发送。")
        if errors:
            raise StudioError(errors[0])
        return self.status(job_id)

    def _save(self, job, **updates):
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

    def _run(self, job_id, info, provider, binary, started, errors):
        job = {"id": job_id, **info, "provider": provider, "created_at": utc_now(),
               "status": "starting", "message": "正在启动 Agent", "reply": ""}
        proc = None
        watchdog = None
        try:
            # One dispatch at a time across all workbench processes for this workspace.
            # This is a dispatch lock, separate from short-lived story transaction locks.
            with session_lock(self.folder):
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
                    prompt = (f"Read {ROOT / 'AGENTS.md'} and {ROOT / 'workbench/agent-guide.md'} first. "
                              "Execute only the saved Story Bible Studio ticket below, using its specified workspace. "
                              "Read only this ticket's construction topic or RP session context. "
                              "You are the writing agent: prepare, review, and commit through the existing core. "
                              "For construction, save the full public answer, decision and next_prompt with bible_commit. "
                              "Do not merely return text without committing. Do not edit events directly, "
                              "delete locks, archive operations, select variants, freeze canon, or run git. "
                              "Do not start unrelated work or change platform configuration. "
                              "If blocked, stop and explain; preserve the original operation ID. "
                              "Use the CLI fallback for this exact workspace if MCP points elsewhere.\n\n"
                              + short_ticket(self.service.workspace, Path(info["ticket_path"])))
                    args, stdin = command(provider, binary, self.service.workspace, prompt)
                    env = os.environ.copy()
                    env.pop("CODEX_THREAD_ID", None)
                    env["PYTHONIOENCODING"] = "utf-8"
                    # Make the project's Python available to ticket commands even from desktop launchers.
                    python_dir = ROOT / ".venv-workbench" / ("Scripts" if os.name == "nt" else "bin")
                    if python_dir.is_dir():
                        env["PATH"] = str(python_dir) + os.pathsep + env.get("PATH", "")
                    with self.mutex:
                        if self.cancel.is_set():
                            self._save(job, status="stopped", message="生成已停止，小票仍保留。")
                            return
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
                        if event.get("type") == "thread.started":
                            job["platform_session"] = event.get("thread_id")
                        if event.get("sessionID"):
                            job["platform_session"] = event["sessionID"]
                        item = event.get("item") or {}
                        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                            job["reply"] = str(item.get("text", ""))[-16000:]
                        if event.get("type") == "text":
                            job["reply"] = (job["reply"] + str((event.get("part") or {}).get("text", "")))[-16000:]
                        if event.get("type") == "error":
                            error = event.get("error") or event
                            job["reply"] = str(error.get("message", "平台返回错误，请检查 CLI 登录与权限。"))[-2000:]
                    code = proc.wait()
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
                    self._save(job, status="failed", message=f"生成未完成：{exc}")
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
                return self.status(job_id)
            self.cancel.set()
            proc = self.process
            if proc and proc.poll() is None:
                self._terminate(proc)
        return self.status(job_id)

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
