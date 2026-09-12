"""OpenCode v1 loopback connection. Credentials never enter public job records."""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from bootstrap import ROOT
from studio_core import StudioError, atomic_write_json


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_url(value):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                 and parsed.port and not parsed.username and not parsed.password
                 and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
                 and not any(c.isspace() for c in value))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise StudioError("OpenCode 服务地址须为带端口的本机 HTTP 地址，例如 http://127.0.0.1:4096；不要在地址中填写密码。")
    return value.rstrip("/")


def session_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"ses_[A-Za-z0-9]+", value):
        raise StudioError("OpenCode 返回了不兼容的 session ID。")
    return value


class OpenCodeServer:
    def __init__(self, workspace, url, username="opencode", password=""):
        self.workspace = str(Path(workspace).resolve())
        self.url = local_url(url)
        if not isinstance(username, str) or not isinstance(password, str) or ":" in username:
            raise StudioError("OpenCode 服务认证配置无效。")
        self.username, self.password = username, password
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    @classmethod
    def configured(cls, workspace):
        path = Path(workspace) / ".workbench/opencode-server.json"
        config = {}
        if path.is_file():
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(config, dict):
                    raise ValueError()
            except (ValueError, OSError):
                raise StudioError("无法读取 .workbench/opencode-server.json，请检查本机连接配置。") from None
        url = os.environ.get("STORY_STUDIO_OPENCODE_SERVER_URL") or config.get("url")
        if not url:
            raise StudioError("尚未配置共享后台。运行 workbench/opencode_server.py configure --url http://127.0.0.1:4096，或设置 STORY_STUDIO_OPENCODE_SERVER_URL。")
        return cls(workspace, url,
                   os.environ.get("OPENCODE_SERVER_USERNAME", config.get("username", "opencode")),
                   os.environ.get("OPENCODE_SERVER_PASSWORD", config.get("password", "")))

    def env(self, env):
        env["OPENCODE_SERVER_USERNAME"] = self.username
        env["OPENCODE_SERVER_PASSWORD"] = self.password
        # The CLI's fetch must also keep loopback traffic out of configured proxies.
        for key in ("NO_PROXY", "no_proxy"):
            env[key] = ",".join(filter(None, [env.get(key, ""), "127.0.0.1,localhost,::1"]))

    def redact(self, text):
        value = str(text)
        if self.password:
            value = value.replace(self.password, "[已隐藏]")
            token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            value = value.replace(token, "[已隐藏]")
        return value

    def request(self, method, path, body=None, scoped=True):
        url = self.url + path
        if scoped:
            url += "?" + urlencode({"directory": self.workspace})
        headers = {"Accept": "application/json"}
        if self.password:
            token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            headers["Authorization"] = "Basic " + token
        data = None if body is None else json.dumps(body).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            with self.opener.open(Request(url, data=data, headers=headers, method=method), timeout=5) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError()
                return json.loads(raw)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code in {401, 403}:
                raise StudioError("OpenCode 共享后台认证失败，请核对服务用户名和密码。") from None
            raise StudioError(f"OpenCode 共享后台返回 HTTP {code}，请检查服务版本和连接配置。") from None
        except (URLError, OSError, TimeoutError):
            raise StudioError("无法连接 OpenCode 共享后台或请求超时；请确认服务仍在运行。") from None
        except (ValueError, UnicodeError):
            raise StudioError("OpenCode 共享后台响应格式不兼容。") from None

    def health(self):
        data = self.request("GET", "/global/health", scoped=False)
        if not isinstance(data, dict) or data.get("healthy") is not True:
            raise StudioError("OpenCode 共享后台未就绪。")
        version = data.get("version", "")
        if not isinstance(version, str) or not re.fullmatch(r"1\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
            raise StudioError("共享后台接入目前支持 OpenCode 1.x，请核对服务版本。")
        return version

    def check_workspace(self):
        data = self.request("GET", "/path")
        directory = data.get("directory") if isinstance(data, dict) else None
        if not isinstance(directory, str) or os.path.normcase(os.path.normpath(directory)) != os.path.normcase(os.path.normpath(self.workspace)):
            raise StudioError("OpenCode 后台工作目录与小票工作区不一致；Windows 工作区请连接同机 Windows 服务。")

    def create(self, title):
        data = self.request("POST", "/session", {"title": title})
        return session_id(data.get("id") if isinstance(data, dict) else None)

    def abort(self, sid):
        sid = session_id(sid)
        if self.request("POST", f"/session/{sid}/abort", {}) is not True:
            raise StudioError("OpenCode 后台尚未确认取消 session。")
        statuses = self.request("GET", "/session/status")
        if not isinstance(statuses, dict):
            raise StudioError("OpenCode 后台未返回有效的 session 状态。")
        status = statuses.get(sid, {"type": "idle"})
        if not isinstance(status, dict) or status.get("type") != "idle":
            raise StudioError("OpenCode session 仍在运行，请稍后再次停止。")


def main():
    import argparse
    import getpass
    parser = argparse.ArgumentParser(description="配置或检查工作台使用的 OpenCode 共享后台")
    parser.add_argument("action", choices=["configure", "check", "serve"])
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--url")
    parser.add_argument("--username", default="opencode")
    parser.add_argument("--ask-password", action="store_true")
    args = parser.parse_args()
    if args.action == "configure":
        password = getpass.getpass("OpenCode 服务密码（不会显示）: ") if args.ask_password else os.environ.get("OPENCODE_SERVER_PASSWORD", "")
        conn = OpenCodeServer(args.workspace, args.url or "http://127.0.0.1:4096", args.username, password)
        atomic_write_json(args.workspace / ".workbench/opencode-server.json",
                          {"url": conn.url, "username": conn.username, "password": conn.password})
        print("已保存本机连接配置；密码不会发送到工作台网页。")
    elif args.action == "check":
        conn = OpenCodeServer.configured(args.workspace)
        version = conn.health()
        conn.check_workspace()
        print(f"OpenCode {version} 共享后台可连接，工作目录匹配：{conn.url}")
    else:
        import secrets
        import subprocess
        from agent_runner import executable
        path = args.workspace / ".workbench/opencode-server.json"
        if not path.exists() and not os.environ.get("STORY_STUDIO_OPENCODE_SERVER_URL"):
            # A persistent password lets both clients reconnect after restarting the service.
            atomic_write_json(path, {"url": local_url(args.url or "http://127.0.0.1:4096"),
                                     "username": args.username,
                                     "password": os.environ.get("OPENCODE_SERVER_PASSWORD") or secrets.token_urlsafe(32)})
        conn = OpenCodeServer.configured(args.workspace)
        parsed = urlsplit(conn.url)
        env = os.environ.copy()
        conn.env(env)
        python_dir = ROOT / ".venv-workbench" / ("Scripts" if os.name == "nt" else "bin")
        if python_dir.is_dir():
            env["PATH"] = str(python_dir) + os.pathsep + env.get("PATH", "")
        print(f"OpenCode 共享后台：{conn.url}\n桌面端请选择此服务器，并打开工作区：{conn.workspace}", flush=True)
        print(f"桌面连接使用的用户名和密码可在本机文件 {path} 中查看（如设置环境变量则以环境变量为准）。", flush=True)
        print("此窗口运行共享服务；保持窗口打开。端口已被占用时不会停止已有服务。", flush=True)
        child = subprocess.Popen([executable("opencode"), "serve", "--hostname", parsed.hostname,
                                  "--port", str(parsed.port)], cwd=conn.workspace, env=env,
                                 start_new_session=os.name != "nt")
        try:
            raise SystemExit(child.wait())
        except KeyboardInterrupt:
            from agent_runner import AgentRunner
            if child.poll() is None:
                AgentRunner._terminate(child)


if __name__ == "__main__":
    try:
        main()
    except StudioError as exc:
        raise SystemExit(str(exc)) from None
