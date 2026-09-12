"""Loopback-only, same-origin local workbench. Standard library + existing PyYAML."""
from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from bootstrap import ROOT
from studio_workbench import StudioService, Conflict
from studio_core import StudioError, LockError
from agent_runner import AgentRunner

STATIC = Path(__file__).resolve().parent / "static"
READS = {"projects", "project", "openings", "snapshot", "status", "request_status", "requirements_view", "operation_view", "session_diagnostic", "bible_view"}
WRITES = {"configure", "new_session", "select", "checkpoint", "branch", "request", "preview",
          "requirements", "operation_edit", "recovery_ticket", "bible_edit"}


def connections(root):
    python = ROOT / ".venv-workbench" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    command = str(python if python.exists() else Path(sys.executable))
    args = ["-B", str(ROOT / "workbench/mcp_server.py"), "--workspace", str(root)]
    json_entry = {"command": command, "args": args}
    return {"codex": '[mcp_servers.story_bible]\ncommand = ' + json.dumps(command) + '\nargs = ' + json.dumps(args) + '\n',
            "antigravity": json.dumps({"mcpServers": {"story_bible": json_entry}}, ensure_ascii=False, indent=2),
            "opencode": json.dumps({"mcp": {"story_bible": {"type": "local", "command": [command, *args], "enabled": True}}}, ensure_ascii=False, indent=2)}


class DinerServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, workspace):
        super().__init__(address, Handler)
        self.service = StudioService(workspace)
        self.agents = AgentRunner(self.service)
        self.token = secrets.token_urlsafe(32)
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def server_close(self):
        self.agents.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Avoid story names, request contents and query parameters in access logs.
        pass

    def reply(self, code, data, content_type="application/json; charset=utf-8"):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if not isinstance(data, bytes) else data
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            raise PermissionError("仅支持本机工作台地址")
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            raise PermissionError("请求来源不匹配")
        if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
            raise PermissionError("不接受其他站点的请求")

    def do_GET(self):
        try:
            self.trusted()
            parsed = urlsplit(self.path)
            if parsed.path == "/api/bootstrap":
                import hashlib
                workspace_id = hashlib.sha256(str(self.server.service.workspace).encode()).hexdigest()[:20]
                return self.reply(200, {"token": self.server.token, "workspace_id": workspace_id,
                                       "legacy_workspace": self.server.service.workspace == ROOT,
                                       "connections": connections(self.server.service.workspace)})
            if parsed.path.startswith("/api/"):
                op = parsed.path[5:]
                if op == "agent_providers":
                    return self.reply(200, self.server.agents.providers())
                if op == "agent_status":
                    params = parse_qs(parsed.query)
                    return self.reply(200, self.server.agents.status(params.get("job_id", [""])[-1]))
                if op not in READS:
                    return self.reply(404, {"error": "接口不存在"})
                params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
                return self.reply(200, self.server.service.dispatch(op, params))
            assets = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/diner.svg": "diner.svg",
                      "/agent-panel.js": "agent-panel.js",
                      "/bible": "bible.html", "/bible.js": "bible.js", "/bible.css": "bible.css"}
            if parsed.path not in assets:
                return self.reply(404, {"error": "页面不存在"})
            asset = STATIC / assets[parsed.path]
            mime = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml"}[asset.suffix]
            self.reply(200, asset.read_bytes(), mime + "; charset=utf-8")
        except Exception as exc:
            self.error(exc)

    def do_POST(self):
        try:
            self.trusted()
            if not secrets.compare_digest(self.headers.get("X-Studio-Token", ""), self.server.token):
                raise PermissionError("请刷新面板后重试")
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("请提交 JSON")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000:
                raise ValueError("请求体为空或超过 1 MB")
            op = urlsplit(self.path).path.removeprefix("/api/")
            if op == "shutdown":
                self.reply(200, {"status": "stopped"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if op not in WRITES | {"agent_start", "agent_stop"}:
                return self.reply(404, {"error": "接口不存在"})
            payload = json.loads(self.rfile.read(length))
            if op == "agent_start":
                return self.reply(200, self.server.agents.start(**payload))
            if op == "agent_stop":
                return self.reply(200, self.server.agents.stop(**payload))
            self.reply(200, self.server.service.dispatch(op, payload))
        except Exception as exc:
            self.error(exc)

    def error(self, exc):
        code = 403 if isinstance(exc, PermissionError) else 409 if isinstance(exc, (Conflict, LockError)) else 400 if isinstance(exc, (StudioError, ValueError, TypeError, FileNotFoundError, FileExistsError)) else 500
        self.reply(code, {"error": str(exc) if code != 500 else "后台读取失败，请查看终端诊断；原文件未自动修复。"})
        if code == 500:
            import traceback
            traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    try:
        server = DinerServer(("127.0.0.1", args.port), args.workspace)
    except OSError:
        server = DinerServer(("127.0.0.1", 0), args.workspace)
    print(f"Story Bible Diner: {server.origin}", flush=True)
    if args.open:
        webbrowser.open(server.origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
