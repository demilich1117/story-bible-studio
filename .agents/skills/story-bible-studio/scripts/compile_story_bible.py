from __future__ import annotations

import argparse
from pathlib import Path

from studio_core import atomic_write_text


def main() -> None:
    parser = argparse.ArgumentParser(description="合并分类 Story Bible")
    parser.add_argument("--project", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    project = Path(args.project).resolve()
    source = project / "StoryBible"
    files = [path for path in sorted(source.rglob("*.md")) if path.name != ".gitkeep"]
    output = Path(args.output).resolve() if args.output else project / "导出" / "StoryBible-合并版.md"
    body = [f"# {project.name} Story Bible\n"]
    for path in files:
        body += [f"\n<!-- source: {path.relative_to(project).as_posix()} -->\n", path.read_text(encoding="utf-8")]
    atomic_write_text(output, "\n".join(body))
    print(output)


if __name__ == "__main__":
    main()
