#!/usr/bin/env python3
"""Offline author-stage regressions using real temporary Git repositories."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import author_stage as author  # noqa: E402


class AcceptControlGate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-author-controls-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.queue = self.root / "automation" / "land-queue"
        self.out = self.queue / "author-1"
        self.blocked = self.root / "target" / "author-blocked"
        self.controls = list(author.ACCEPT_CONTROLS)
        self.sources = {path: f"// HEAD control {index}\nfn main() {{}}\n"
                        for index, path in enumerate(self.controls)}
        self.git("init", "-q")
        self.git("config", "user.name", "Author-stage test")
        self.git("config", "user.email", "author-stage@example.invalid")
        self.write(".gitignore", "compiler/target/\ntarget/\n")
        self.write("compiler/probe.rs", "// original\n")
        for path, source in self.sources.items():
            self.write(path, source)
        self.commit()
        self.write("compiler/probe.rs", "// candidate\n")
        self.candidate = self.git("diff", "--", "compiler/probe.rs").stdout
        self.git("checkout", "--", ".")
        self.checks = []
        self.fail_read = None
        self.reject_control = False
        self.real_sh = author.sh
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in {
            "ROOT": self.root,
            "COMPILER": self.root / "compiler",
            "QUEUE": self.queue,
            "BLOCKED_DIR": self.blocked,
            "REPRO_DIR": self.root / "compiler" / "target" / "author-stage",
            "HYB": self.root / "compiler" / "target" / "debug" / "hyb",
        }.items():
            self.stack.enter_context(patch.object(author, name, value))
        self.stack.enter_context(patch.dict(os.environ, {
            "AUTHOR_API_BASE": "https://author.invalid",
            "AUTHOR_MODEL": "offline-test",
        }))
        self.stack.enter_context(patch.object(author, "api_get", return_value={
            "number": 1, "title": "Offline control-gate fixture",
            "body": "```hy\nfn main() { x := 1 / 0 }\n```\nExpected HYB-E6001",
        }))
        self.stack.enter_context(patch.object(
            author, "chat", side_effect=lambda messages: f"```diff\n{self.candidate}```"))
        self.stack.enter_context(patch.object(author, "sh", side_effect=self.sh))
        # Keep the production trace writer real, but clean its directory with
        # the rest of the fixture (and outside the Git worktree).
        trace_dir = self.root / "target" / "trace"
        trace_dir.mkdir(parents=True)
        self.stack.enter_context(patch.object(author.tempfile, "mkdtemp",
                                             return_value=str(trace_dir)))

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, text=True,
                              capture_output=True, check=True)

    def write(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "Fixture")

    def sh(self, args, **kwargs):
        if args[:2] == ["cargo", "build"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == str(author.HYB):
            path = self.root / args[2]
            self.checks.append((path.name, path.read_text()))
            reject = path.name == "repro.hy" or self.reject_control
            return subprocess.CompletedProcess(
                args, int(reject), "", "error[HYB-E6001]" if reject else "")
        if self.fail_read is not None and args == [
            "git", "show", f"HEAD:{self.controls[0]}"
        ]:
            return subprocess.CompletedProcess(args, *self.fail_read)
        return self.real_sh(args, **kwargs)

    def run_author(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return author.run_author(1, self.out, max_rounds=1)

    def assert_blocked(self, control, detail):
        self.assertEqual(self.run_author(), 3)
        self.assertFalse(self.out.exists(), "failed controls must not create a ready bundle")
        evidence = self.blocked / "issue-1"
        meta = json.loads((evidence / "meta.json").read_text())
        self.assertEqual(meta["status"], "blocked")
        note = (evidence / "note.txt").read_text()
        self.assertIn(control, note)
        self.assertIn(detail, note)
        records = [json.loads(line) for line in (evidence / "trace.log").read_text().splitlines()]
        self.assertFalse(any(record["kind"] == "ready" for record in records))
        self.assertEqual((self.root / "compiler/probe.rs").read_text(), "// original\n")
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        return records

    def test_missing_head_control_blocks_bundle(self):
        control = self.controls[0]
        self.git("rm", control)
        self.commit()
        records = self.assert_blocked(control, "128")
        failure = next(record for record in records if record["kind"] == "gate-control-read-failed")
        self.assertEqual(failure["control"], control)
        self.assertIn("does not exist", failure["error"])
        self.assertEqual([name for name, _ in self.checks], ["repro.hy"])

    def test_nonzero_control_read_with_stdout_blocks_bundle(self):
        self.fail_read = (1, self.sources[self.controls[0]], "fatal: simulated read failure")
        records = self.assert_blocked(self.controls[0], "simulated read failure")
        failure = next(record for record in records if record["kind"] == "gate-control-read-failed")
        self.assertEqual(failure["exit"], 1)
        self.assertEqual([name for name, _ in self.checks], ["repro.hy"])

    def test_empty_head_control_blocks_bundle(self):
        control = self.controls[0]
        self.write(control, "")
        self.commit()
        records = self.assert_blocked(control, "empty")
        failure = next(record for record in records if record["kind"] == "gate-control-read-failed")
        self.assertEqual(failure["exit"], 0)
        self.assertEqual([name for name, _ in self.checks], ["repro.hy"])

    def test_missing_later_control_blocks_after_first_accepts(self):
        control = self.controls[1]
        self.git("rm", control)
        self.commit()
        self.assert_blocked(control, "128")
        self.assertEqual([name for name, _ in self.checks], [
            "repro.hy", "control-" + Path(self.controls[0]).name])

    def test_rejecting_control_still_blocks_bundle(self):
        self.reject_control = True
        self.assert_blocked(self.controls[0], "OVER-REACH")

    def test_success_checks_every_control_from_head_before_ready(self):
        # A candidate may edit a control, but the gate must check the HEAD
        # bytes, not the candidate's replacement.
        self.write(self.controls[0], "// candidate replacement\n")
        self.candidate += self.git("diff", "--", self.controls[0]).stdout
        self.git("checkout", "--", ".")
        self.assertEqual(self.run_author(), 0)
        meta = json.loads((self.out / "meta.json").read_text())
        self.assertEqual(meta["status"], "ready")
        self.assertEqual(meta["gates"]["acceptControls"], self.controls)
        self.assertEqual(self.checks[1:], [
            ("control-" + Path(path).name, source)
            for path, source in self.sources.items()])
        self.assertEqual((self.out / "fix.patch").read_text(), self.candidate)
        self.assertEqual((self.root / "compiler/probe.rs").read_text(), "// original\n")
        self.assertFalse(self.blocked.exists())


if __name__ == "__main__":
    unittest.main()
