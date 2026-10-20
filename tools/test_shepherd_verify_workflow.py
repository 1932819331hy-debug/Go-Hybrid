#!/usr/bin/env python3
"""Offline regression tests for Shepherd W5's merge and compiler gates."""

import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/shepherd-verify.yml"
SETUP = ROOT / ".github/actions/shepherd-setup/action.yml"


def merge_script():
    """Extract the exact merge-step shell script, not a hand-copied version."""
    text = WORKFLOW.read_text(encoding="utf-8")
    marker = "      - name: Merge main into the PR tree (docs-only conflicts auto-resolve)\n"
    if marker not in text:
        raise AssertionError("Shepherd W5 merge step is missing")
    section = text.split(marker, 1)[1].split("\n      - name: ", 1)[0]
    run_marker = "        run: |\n"
    if run_marker not in section:
        raise AssertionError("Shepherd W5 merge step has no shell script")
    return textwrap.dedent(section.split(run_marker, 1)[1]).strip() + "\n"


class CargoWorkflowConfiguration(unittest.TestCase):
    def test_compiler_commands_start_in_compiler_workspace(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for step, command in (
            ("Battery — cargo workspace", "cargo test --workspace"),
            ("Battery — build hyb", "cargo build -p hyb --quiet"),
        ):
            with self.subTest(step=step):
                self.assertRegex(
                    text,
                    rf"(?m)^      - name: {re.escape(step)}\n"
                    rf"        working-directory: compiler\n"
                    rf"        run: {re.escape(command)}$",
                )

    def test_cargo_cache_tracks_the_real_compiler_workspace(self):
        setup = SETUP.read_text(encoding="utf-8")
        self.assertIn("compiler/target", setup)
        self.assertIn("hashFiles('compiler/Cargo.lock'", setup)


class MergeMainIntoPr(unittest.TestCase):
    """W5 must accept both current PR branches and branches needing a merge."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shepherd-w5-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "W5 Test")
        self.git("config", "user.email", "test@example.com")
        (self.repo / "main.txt").write_text("base\n", encoding="utf-8")
        self.git("add", "main.txt")
        self.git("commit", "-qm", "main base")
        self.git("checkout", "-qb", "feature")
        (self.repo / "feature.txt").write_text("work\n", encoding="utf-8")
        self.git("add", "feature.txt")
        self.git("commit", "-qm", "feature work")
        self.update_remote_main()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def update_remote_main(self):
        self.git("update-ref", "refs/remotes/origin/main", self.git("rev-parse", "main"))

    def run_workflow_merge(self):
        return subprocess.run(
            ["bash", "-c", merge_script()], cwd=self.repo,
            capture_output=True, text=True,
        )

    def test_up_to_date_branch_is_successful_noop(self):
        original = self.git("rev-parse", "HEAD")
        completed = self.run_workflow_merge()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), original)
        self.assertFalse((self.repo / ".git/MERGE_HEAD").exists())

    def test_advanced_main_is_merged_without_dropping_pr_changes(self):
        original = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        (self.repo / "main-next.txt").write_text("new upstream\n", encoding="utf-8")
        self.git("add", "main-next.txt")
        self.git("commit", "-qm", "upstream update")
        upstream = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "feature")
        self.update_remote_main()

        completed = self.run_workflow_merge()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        parents = self.git("rev-list", "--parents", "-n", "1", "HEAD").split()
        self.assertEqual(parents[1:], [original, upstream])
        self.assertEqual((self.repo / "feature.txt").read_text(), "work\n")
        self.assertEqual((self.repo / "main-next.txt").read_text(), "new upstream\n")


if __name__ == "__main__":
    unittest.main()
