"""Offline subprocess regressions for the compiler that autoland verifies.

The Cargo fixture implements target-dir precedence and emits an executable
checker. Autoland's build, subprocess runner and verification calls are real;
no Rust toolchain, network, Git mutations or live landing are involved.
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


class AutolandTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="autoland-target-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo with spaces"
        self.tools = self.root / "tools"
        self.tools.mkdir(parents=True)
        self.compiler = self.root / "compiler"
        self.compiler.mkdir()
        self.target = self.compiler / "target"
        self.hyb = self.target / "debug" / "hyb"
        shutil.copyfile(ROOT / "tools" / "autoland.py", self.tools / "autoland.py")
        (self.root / "repro.hy").write_text("// fixture\n", encoding="utf-8")
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        self.write_executable(self.bin / "cargo", textwrap.dedent("""\
            import os
            from pathlib import Path
            import sys

            args = sys.argv[1:]
            target = os.environ.get("CARGO_TARGET_DIR", "target")
            if "--target-dir" in args:
                target = args[args.index("--target-dir") + 1]
            target = Path(target).resolve()
            if args[0] == "build":
                if os.environ.get("FIXTURE_BUILD_FAIL"):
                    sys.exit("fixture build failure")
                binary = target / "debug" / "hyb"
                binary.parent.mkdir(parents=True, exist_ok=True)
                code = int(os.environ.get("FIXTURE_CHECK_EXIT", "1"))
                label = "FRESH-ACCEPT" if code == 0 else "FRESH-REJECT HYB-E6002"
                binary.write_text(
                    f"#!{sys.executable}\\nprint({label!r})\\nraise SystemExit({code})\\n",
                    encoding="utf-8",
                )
                binary.chmod(0o755)
            elif args[0] == "test":
                if os.environ.get("FIXTURE_TEST_FAIL"):
                    sys.exit("fixture workspace failure")
                target.mkdir(parents=True, exist_ok=True)
                (target / "workspace-tests-passed").write_text("passed\\n")
            else:
                sys.exit("unexpected Cargo operation")
            """))
        # Battery consumers really execute the --bin passed by autoland and
        # reject stale output. Their compiler semantics are deliberately tiny.
        consumer = textwrap.dedent("""\
            import subprocess
            import sys
            binary = sys.argv[sys.argv.index("--bin") + 1]
            result = subprocess.run([binary, "check", "repro.hy"],
                                    capture_output=True, text=True, check=False)
            if (result.returncode, result.stdout.strip()) != (1, "FRESH-REJECT HYB-E6002"):
                sys.exit("battery saw a stale compiler")
            """)
        for name in ("corpus_harness.py", "differential_loop.py"):
            (self.tools / name).write_text(consumer, encoding="utf-8")
        (self.tools / "check_corpus_census.py").write_text("", encoding="utf-8")
        self.env = dict(os.environ)
        for name in ("CARGO_TARGET_DIR", "FIXTURE_BUILD_FAIL", "FIXTURE_TEST_FAIL",
                     "FIXTURE_CHECK_EXIT"):
            self.env.pop(name, None)
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env.get("PATH", "")

    @staticmethod
    def write_executable(path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
        path.chmod(0o755)

    def seed_stale_compiler(self):
        self.write_executable(self.hyb, "print('STALE-ACCEPT')\n")

    def run_autoland(self, body):
        result = subprocess.run(
            [sys.executable, "-c", "import json, os, sys; "
             "sys.path.insert(0, 'tools'); import autoland; " + body],
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def check_fresh_compiler(self):
        result = self.run_autoland(
            "autoland.build(); print(json.dumps(autoland.check_repro('repro.hy')))"
        )
        self.assertEqual(result, [1, "FRESH-REJECT HYB-E6002\n"])

    def absolute_override(self):
        self.env["CARGO_TARGET_DIR"] = str(Path(self.temp.name) / "shared target")

    def relative_override(self):
        self.env["CARGO_TARGET_DIR"] = "../shared target"

    def test_default_target_executes_fresh_compiler(self):
        self.seed_stale_compiler()
        self.check_fresh_compiler()

    def test_absolute_target_override_cannot_reuse_stale_compiler(self):
        self.absolute_override()
        self.seed_stale_compiler()
        self.check_fresh_compiler()

    def test_relative_target_override_cannot_reuse_stale_compiler(self):
        self.relative_override()
        self.seed_stale_compiler()
        self.check_fresh_compiler()

    def test_absolute_target_override_works_without_local_binary(self):
        self.absolute_override()
        self.check_fresh_compiler()

    def test_relative_target_override_works_without_local_binary(self):
        self.relative_override()
        self.check_fresh_compiler()

    def test_rebuild_changes_the_verified_compiler(self):
        self.absolute_override()
        self.seed_stale_compiler()
        result = self.run_autoland(
            "os.environ['FIXTURE_CHECK_EXIT'] = '0'; autoland.build(); "
            "before = autoland.check_repro('repro.hy'); "
            "os.environ['FIXTURE_CHECK_EXIT'] = '1'; autoland.build(); "
            "print(json.dumps([before, autoland.check_repro('repro.hy')]))"
        )
        self.assertEqual(result, [[0, "FRESH-ACCEPT\n"],
                                  [1, "FRESH-REJECT HYB-E6002\n"]])

    def test_workspace_battery_uses_the_verified_compiler_target(self):
        for override in (self.absolute_override, self.relative_override):
            with self.subTest(override=override.__name__):
                override()
                self.seed_stale_compiler()
                stamp = self.target / "workspace-tests-passed"
                stamp.unlink(missing_ok=True)
                result = self.run_autoland(
                    "autoland.build(); print(json.dumps(autoland.battery()))"
                )
                self.assertEqual(result, [[name, True, ""] for name in
                                          ("workspace", "corpus", "census", "differential")])
                self.assertTrue(stamp.is_file(), "workspace tests used a different Cargo target")

    def test_build_failure_is_not_hidden_by_stale_binary(self):
        self.absolute_override()
        self.seed_stale_compiler()
        self.env["FIXTURE_BUILD_FAIL"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, 'tools'); "
             "import autoland; autoland.build()"],
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cargo build failed: fixture build failure", result.stderr)

    def test_workspace_failure_stops_the_battery(self):
        self.env["FIXTURE_TEST_FAIL"] = "1"
        result = self.run_autoland("print(json.dumps(autoland.battery()))")
        self.assertEqual(result, [["workspace", False, "fixture workspace failure\n"]])


if __name__ == "__main__":
    unittest.main()
