import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("export_public", Path(__file__).resolve().parents[1] / "tools/export_public.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class PublicExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "private"
        self.target = self.root / "public"
        self.source.mkdir()
        (self.source / "tools").mkdir()
        (self.source / "tools/public.gitignore").write_text("/作品/\n", encoding="utf-8")
        (self.source / "README.md").write_text("Public program", encoding="utf-8")
        self.manifest(["README.md"])

    def manifest(self, names):
        (self.source / "public-files.json").write_text(json.dumps(names), encoding="utf-8")

    def test_does_not_copy_private_files_or_history(self):
        for name in ("作品/example/events.jsonl", ".git/config", "output/private.md", "workbench/unreviewed.md"):
            item = self.source / name
            item.parent.mkdir(parents=True, exist_ok=True)
            item.write_text("PRIVATE_MARKER", encoding="utf-8")
        exporter.export(self.source, self.target)
        paths = {p.relative_to(self.target).as_posix() for p in self.target.rglob("*") if p.is_file()}
        self.assertEqual(paths, {"README.md", ".gitignore", exporter.MARKER})
        self.assertFalse(any("PRIVATE_MARKER" in p.read_text(encoding="utf-8") for p in self.target.rglob("*") if p.is_file()))

    def test_rejects_private_and_escaping_paths_before_writing(self):
        for name in ("作品/example.md", "../secret.md", "/README.md", "workbench/../../secret.md", "workbench/.env", "workbench/secret.key", "workbench/会话/events.json", "C:/secret.md"):
            with self.subTest(name=name):
                self.manifest([name])
                with self.assertRaises(ValueError):
                    exporter.export(self.source, self.target)
                self.assertFalse(self.target.exists())

    def test_updates_managed_files_and_preserves_public_git(self):
        exporter.export(self.source, self.target)
        (self.target / ".git").mkdir()
        (self.target / ".git/config").write_text("public repository", encoding="utf-8")
        (self.source / "AGENTS.md").write_text("New instructions", encoding="utf-8")
        self.manifest(["AGENTS.md"])
        exporter.export(self.source, self.target)
        self.assertFalse((self.target / "README.md").exists())
        self.assertTrue((self.target / "AGENTS.md").is_file())
        self.assertEqual((self.target / ".git/config").read_text(encoding="utf-8"), "public repository")

    def test_refuses_unknown_files_and_source_directory(self):
        with self.assertRaises(ValueError):
            exporter.export(self.source, self.source)
        exporter.export(self.source, self.target)
        (self.target / "private-draft.md").write_text("Keep me", encoding="utf-8")
        with self.assertRaises(ValueError):
            exporter.export(self.source, self.target)
        self.assertEqual((self.target / "private-draft.md").read_text(encoding="utf-8"), "Keep me")

    def test_tampered_previous_manifest_cannot_delete_outside_export(self):
        exporter.export(self.source, self.target)
        outside = self.root / "secret.md"
        outside.write_text("Keep me", encoding="utf-8")
        (self.target / exporter.MARKER).write_text(json.dumps({"format": 1, "files": ["../secret.md"]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            exporter.export(self.source, self.target)
        self.assertTrue(outside.is_file())

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_windows_junction_to_private_data_is_rejected(self):
        private = self.source / "作品"
        private.mkdir()
        (private / "private.md").write_text("PRIVATE_MARKER", encoding="utf-8")
        junction = self.source / "workbench"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(private)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        # Remove only this verified temporary junction; never traverse its target.
        self.addCleanup(junction.rmdir)
        self.manifest(["workbench/private.md"])
        with self.assertRaises(ValueError):
            exporter.export(self.source, self.target)
        self.assertFalse(self.target.exists())
        self.assertTrue((private / "private.md").is_file())

    def test_hardlinked_destination_cannot_overwrite_external_file(self):
        exporter.export(self.source, self.target)
        outside = self.root / "private.md"
        outside.write_text("PRIVATE_MARKER", encoding="utf-8")
        target = self.target / "README.md"
        target.unlink()
        os.link(outside, target)
        with self.assertRaises(ValueError):
            exporter.export(self.source, self.target)
        self.assertEqual(outside.read_text(encoding="utf-8"), "PRIVATE_MARKER")

    def test_rejects_shared_git_worktree_pointer(self):
        exporter.export(self.source, self.target)
        (self.target / ".git").write_text("gitdir: ../private/.git/worktrees/public\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            exporter.export(self.source, self.target)
