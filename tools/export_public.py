"""Export explicitly reviewed files without copying private data or Git history."""
import argparse
import json
import shutil
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = ".public-export.json"
ALLOWED_ROOT_FILES = {
    "README.md", "AGENTS.md", "requirements.txt", "启动工作台.cmd",
    "安装MCP.ps1", "public-files.json",
}
ALLOWED_TREES = (".agents/skills/story-bible-studio/", "workbench/", "tests/", "docs/", "tools/")
ALLOWED_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".txt", ".cmd", ".ps1", ".js", ".cjs", ".css", ".html", ".svg", ".gitignore"}
FORBIDDEN_PARTS = {".git", "作品", "会话", "发布", "验收", "output", ".workbench", ".runtime", "__pycache__", "node_modules"}


def validate_name(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("Invalid public file path")
    parts = name.split("/")
    if any(p in {"", ".", ".."} or p in FORBIDDEN_PARTS for p in parts):
        raise ValueError(f"Forbidden public path: {name}")
    if name in ALLOWED_ROOT_FILES:
        return
    if not name.startswith(ALLOWED_TREES) or Path(name).suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"Unapproved public path: {name}")
    if any(p.startswith(".") and p != ".agents" for p in parts):
        raise ValueError(f"Hidden file is not approved: {name}")


def reject_links(path):
    # Path.is_junction is unavailable on Python 3.11. lstat also catches
    # junctions and other Windows reparse points on the minimum runtime.
    for current in (*reversed(path.parents), path):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Linked paths cannot be exported")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise ValueError("Hard-linked files cannot be exported")


def checked_path(root, name):
    """Reject symlinks/junctions and any traversal outside the selected root."""
    current = root / name
    reject_links(current)
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Path escapes the export root")
    return current


def export(source, destination):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    reject_links(source)
    reject_links(destination)
    source = source.resolve()
    # Keep the source and any parent of it out of the write target.
    destination = destination.resolve()
    if source == destination or source.is_relative_to(destination):
        raise ValueError("Export destination must be separate from the source")
    names = json.loads((source / "public-files.json").read_text(encoding="utf-8"))
    if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
        raise ValueError("public-files.json must contain a list of paths")
    if len(names) != len(set(names)):
        raise ValueError("Duplicate public file path")
    for name in names:
        validate_name(name)
        if not checked_path(source, name).is_file():
            raise ValueError(f"Missing public source file: {name}")
    ignore_source = checked_path(source, "tools/public.gitignore")
    if not ignore_source.is_file():
        raise ValueError("Missing public ignore rules")

    old = set()
    marker = checked_path(destination, MARKER)
    if destination.exists() and any(destination.iterdir()):
        git_dir = checked_path(destination, ".git")
        if git_dir.exists() and not git_dir.is_dir():
            raise ValueError("Public export must use its own Git directory, not a worktree pointer")
        if not marker.is_file():
            raise ValueError("Destination must be empty or an existing public export")
        previous = json.loads(marker.read_text(encoding="utf-8"))
        if previous.get("format") != 1:
            raise ValueError("Unknown export format")
        old = set(previous["files"])
        for name in old:
            if name != ".gitignore":
                validate_name(name)
            checked_path(destination, name)
        for item in destination.rglob("*"):
            rel = item.relative_to(destination).as_posix()
            if rel == ".git" or rel.startswith(".git/"):
                continue
            checked_path(destination, rel)
            if item.is_file() and rel not in old | {MARKER}:
                raise ValueError(f"Unexpected file in public checkout: {rel}")

    output_names = set(names) | {".gitignore"}
    for name in output_names:
        checked_path(destination, name)
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        target = checked_path(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked_path(source, name), target)
    shutil.copyfile(ignore_source, checked_path(destination, ".gitignore"))
    # Only remove previously managed files after validating all paths above.
    for name in old - output_names:
        checked_path(destination, name).unlink(missing_ok=True)
    marker.write_text(json.dumps({"format": 1, "files": sorted(output_names)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(output_names)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    try:
        count = export(ROOT, args.destination)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f"Export refused: {error}\n")
    print(f"Exported {count} public files to {args.destination.resolve()}")
    print("No Git history, personal works or sessions were copied. Review before pushing.")


if __name__ == "__main__":
    main()
