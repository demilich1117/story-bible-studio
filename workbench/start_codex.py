"""Interactive convenience launcher; credentials remain managed by Codex."""
import argparse
import os
import subprocess
import sys

from bootstrap import ROOT
from studio_core import StudioError
from codex_cli import codex_executable


def logged_in(binary):
    try:
        result = subprocess.run([binary, "login", "status"], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired):
        raise StudioError("无法检查 Codex 登录状态，请确认 CLI 可运行后重试。") from None
    if result.returncode == 0:
        return True
    if "not logged in" in (result.stdout + result.stderr).lower():
        return False
    raise StudioError("Codex 登录检查失败，请在终端运行 codex login status 查看诊断。")


def prepare_codex(check_only=False):
    binary = codex_executable()
    print(f"Codex CLI：{binary}", flush=True)
    authenticated = logged_in(binary)
    if not authenticated:
        if check_only:
            print("CLI 尚未登录。双击启动入口时会引导登录。", flush=True)
            return None
        print("CLI 尚未登录。请按接下来的提示在浏览器中完成授权；凭据由 Codex 保存。", flush=True)
        if subprocess.call([binary, "login"], cwd=ROOT) != 0 or not logged_in(binary):
            raise StudioError("登录未完成；完成登录后再次双击启动即可。")
    print("CLI 已登录，可直接使用。", flush=True)
    return binary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只检查路径和登录，不登录或启动工作台")
    args = parser.parse_args()
    try:
        binary = prepare_codex(args.check)
        if args.check:
            return 0 if binary else 1
        env = os.environ.copy()
        env["STORY_STUDIO_CODEX"] = binary
        print("正在打开工作台。首次使用时请选择「Codex · 本地生成」。", flush=True)
        return subprocess.call([sys.executable, "-B", str(ROOT / "workbench/server.py"), "--open"], cwd=ROOT, env=env)
    except (StudioError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
