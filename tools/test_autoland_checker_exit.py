"""Autoland must distinguish checker rejection from process failure.

The checker and Cargo are deterministic executable fixtures, not a real
compiler. Autoland, its subprocess calls, patch application, commits and
push assertions run unchanged against a disposable local-only Git remote.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix" and shutil.which("git"),
                     "requires Git and POSIX executable fixtures")
class AutolandCheckerExitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="autoland-checker-exit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo with spaces"
        self.root.mkdir()
        self.remote = Path(self.temp.name) / "remote.git"
        self.trace = Path(self.temp.name) / "trace.jsonl"
        self.env = dict(os.environ)
        self.env.update(SHEPHERD_MODE="live", FIXTURE_BASELINE_EXIT="0",
                        FIXTURE_PATCHED_EXIT="1", FIXTURE_OUTPUT="HYB-E6002",
                        FIXTURE_TRACE=str(self.trace))
        # Neither a developer's Git identity nor an inherited target override
        # should change the disposable repository's behavior.
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                     "CARGO_TARGET_DIR"):
            self.env.pop(name, None)
        self.env.update(GIT_AUTHOR_NAME="Autoland test",
                        GIT_AUTHOR_EMAIL="autoland@example.invalid",
                        GIT_COMMITTER_NAME="Autoland test",
                        GIT_COMMITTER_EMAIL="autoland@example.invalid",
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        (self.root / "tools").mkdir()
        shutil.copyfile(ROOT / "tools/autoland.py", self.root / "tools/autoland.py")
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        self.executable(self.bin / "cargo", "pass\n")
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env.get("PATH", "")
        self.executable(self.root / "compiler/target/debug/hyb", textwrap.dedent("""\
            import os
            from pathlib import Path

            phase = Path('checker-state').read_text().strip().upper()
            code = int(os.environ['FIXTURE_' + phase + '_EXIT'])
            print(os.environ['FIXTURE_OUTPUT'], flush=True)
            if code < 0:
                os.kill(os.getpid(), -code)
            raise SystemExit(code)
            """))
        for name in ("corpus_harness.py", "check_corpus_census.py",
                     "differential_loop.py", "shepherd.py"):
            (self.root / "tools" / name).write_text(textwrap.dedent("""\
                import json
                import os
                from pathlib import Path
                import sys
                with Path(os.environ['FIXTURE_TRACE']).open('a') as trace:
                    trace.write(json.dumps(sys.argv) + '\\n')
                """), encoding="utf-8")
        self.bundle = self.root / "automation/land-queue/example"
        self.bundle.mkdir(parents=True)
        (self.bundle / "repro.hy").write_text("// fixture repro\n", encoding="utf-8")
        self.meta = {"issue": 123, "expected_code": "HYB-E6002"}
        (self.bundle / "meta.json").write_text(json.dumps(self.meta), encoding="utf-8")
        (self.root / "checker-state").write_text("baseline\n", encoding="utf-8")
        (self.bundle / "fix.patch").write_text(
            "diff --git a/checker-state b/checker-state\n"
            "--- a/checker-state\n+++ b/checker-state\n"
            "@@ -1 +1 @@\n-baseline\n+patched\n", encoding="utf-8")
        self.git("init", "--quiet", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "fixture baseline")
        self.git("init", "--quiet", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "--quiet", "origin", "HEAD:main")
        self.before = self.git("rev-parse", "HEAD")

    @staticmethod
    def executable(path, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!{sys.executable}\n" + source, encoding="utf-8")
        path.chmod(0o755)

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.root, env=self.env,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def run_bundle(self, baseline=0, patched=1, expected="HYB-E6002", live=True):
        self.meta["expected_code"] = expected
        (self.bundle / "meta.json").write_text(json.dumps(self.meta), encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "--quiet", "--allow-empty", "-m", "fixture metadata")
        self.git("push", "--quiet", "origin", "HEAD:main")
        self.before = self.git("rev-parse", "HEAD")
        self.env.update(FIXTURE_BASELINE_EXIT=str(baseline),
                        FIXTURE_PATCHED_EXIT=str(patched),
                        SHEPHERD_MODE="live" if live else "dry-run")
        return subprocess.run([sys.executable, "tools/autoland.py", "run",
                               "--slug", "example"], cwd=self.root, env=self.env,
                              text=True, capture_output=True, timeout=15)

    def assert_not_landed(self, result, verdict):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AUTOLAND-" + verdict, result.stdout)
        self.assertNotIn("AUTOLAND-LANDED", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.before)
        self.assertEqual(self.git("ls-remote", "origin", "refs/heads/main").split()[0],
                         self.before)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertNotEqual(json.loads((self.bundle / "meta.json").read_text()).get("status"),
                            "landed")
        if self.trace.exists():
            self.assertNotIn("corpus_harness.py", self.trace.read_text())

    def assert_check_error(self, result, code):
        self.assert_not_landed(result, "ERROR")
        self.assertIn(f"hyb check failed (exit {code}):", result.stdout)
        self.assertIn(self.env["FIXTURE_OUTPUT"], result.stdout)

    def test_patched_input_error_is_not_a_rejection(self):
        self.env["FIXTURE_OUTPUT"] = "error reading repro: permission denied"
        self.assert_check_error(self.run_bundle(patched=2, expected=None), 2)

    def test_patched_panic_is_not_a_rejection_without_expected_code(self):
        self.env["FIXTURE_OUTPUT"] = "thread main panicked during analysis"
        self.assert_check_error(self.run_bundle(patched=101, expected=None), 101)

    def test_expected_code_before_panic_does_not_authorize_landing(self):
        self.assert_check_error(self.run_bundle(patched=101), 101)

    def test_expected_code_before_signal_does_not_authorize_landing(self):
        import signal
        self.assert_check_error(self.run_bundle(patched=-signal.SIGTERM), -signal.SIGTERM)

    def test_baseline_input_error_is_not_a_stale_premise(self):
        self.assert_check_error(self.run_bundle(baseline=2), 2)

    def test_baseline_panic_is_not_a_stale_premise(self):
        self.assert_check_error(self.run_bundle(baseline=101), 101)

    def test_baseline_rejection_remains_stale(self):
        self.assert_not_landed(self.run_bundle(baseline=1), "STALE-PREMISE")

    def test_patched_acceptance_remains_ineffective(self):
        self.assert_not_landed(self.run_bundle(patched=0), "INEFFECTIVE")

    def test_wrong_diagnostic_remains_blocked(self):
        self.assert_not_landed(self.run_bundle(expected="HYB-E3001"), "WRONG-CODE")

    def assert_lands(self, expected):
        result = self.run_bundle(expected=expected)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AUTOLAND-LANDED", result.stdout)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), self.before)
        self.assertEqual(self.git("ls-remote", "origin", "refs/heads/main").split()[0],
                         self.git("rev-parse", "HEAD"))
        self.assertEqual(json.loads((self.bundle / "meta.json").read_text())["status"],
                         "landed")

    def test_valid_rejection_lands_with_expected_code(self):
        self.assert_lands("HYB-E6002")

    def test_valid_rejection_lands_without_expected_code(self):
        self.assert_lands(None)

    def test_dry_run_keeps_baseline_only(self):
        result = self.run_bundle(patched=101, live=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AUTOLAND-DRYRUN", result.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.before)
        self.assertEqual((self.root / "checker-state").read_text(), "baseline\n")


if __name__ == "__main__":
    unittest.main()
