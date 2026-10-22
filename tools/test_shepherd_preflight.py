#!/usr/bin/env python3
"""Run W3's actual shell offline against checked module/manifest probes."""

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
WORKFLOW = ROOT / ".github/workflows/shepherd-preflight.yml"


def preflight_script():
    """Extract the literal run block without requiring a YAML dependency."""
    lines = WORKFLOW.read_text().splitlines()
    start = lines.index("      - name: Preflight checks")
    start = lines.index("        run: |", start) + 1
    block = []
    for line in lines[start:]:
        if line and not line.startswith("          "):
            break
        block.append(line[10:])
    if not block:
        raise AssertionError("Preflight checks has no shell body")
    return "\n".join(block) + "\n"


PROBE = r'''
import json
import os
from pathlib import Path
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
root = Path(os.environ["PROBE_ROOT"])
if tool == "go" and args == ["version"]:
    print("go version go1.26.9 linux/amd64")
    sys.exit(0)
if tool == "rustc" and args == ["--version"]:
    print("rustc 1.99.0 (offline probe)")
    sys.exit(0)
if tool == "go":
    manifest = Path.cwd() / "go.mod"
    expected = root / "examples/gomod-integration/go.mod"
elif tool == "cargo":
    manifest = Path.cwd() / "Cargo.toml"
    if "--manifest-path" in args:
        manifest = Path(args[args.index("--manifest-path") + 1]).resolve()
    expected = root / "compiler/Cargo.toml"
else:
    sys.exit("unexpected tool invocation")
with open(os.environ["PROBE_LOG"], "a") as log:
    log.write(json.dumps({"tool": tool, "args": args,
                          "cwd": str(Path.cwd()),
                          "manifest": str(manifest)}) + "\n")
if manifest != expected or not manifest.is_file():
    sys.exit("missing or incorrect manifest: " + str(manifest))
if os.environ.get("PROBE_FAIL") == tool + " " + args[0]:
    sys.exit(42)
'''


class PreflightShell(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="preflight path ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for relative in ("examples/gomod-integration/go.mod", "compiler/Cargo.toml",
                         "tools/shepherd.py", "tools/triage_bot.py"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        for tool in ("go", "cargo", "rustc"):
            target = self.bin / tool
            target.write_text("#!" + sys.executable + "\n" + textwrap.dedent(PROBE))
            target.chmod(0o755)
        (self.bin / "python3").symlink_to(sys.executable)
        self.log = self.root / "calls.jsonl"

    def run_checks(self, fail="", changed="tools/test_example.py\n"):
        (self.output / "changed.txt").write_text(changed)
        # Isolate the workflow's fixed scratch filenames; commands stay intact.
        script = preflight_script().replace("/tmp/", str(self.output) + "/")
        # The temporary path deliberately contains spaces, so quote scratch paths.
        for name in ("preflight.md", "changed.txt", "testcheck.txt", "preflight-warn.txt"):
            path = str(self.output / name)
            script = script.replace(path, '"' + path + '"')
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
            cwd=self.root,
            env={"PATH": str(self.bin) + os.pathsep + os.defpath,
                 "HOME": str(self.root), "PR_NUM": "123",
                 "PROBE_ROOT": str(self.root), "PROBE_LOG": str(self.log),
                 "PROBE_FAIL": fail},
            text=True, capture_output=True, timeout=15,
        )
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        report = (self.output / "preflight.md").read_text()
        return result, calls, report

    def assert_commands(self, calls, count=3):
        self.assertEqual([(c["tool"], c["args"][0]) for c in calls],
                         [("go", "build"), ("go", "vet"), ("cargo", "build")][:count])
        for call in calls:
            if call["tool"] == "go":
                self.assertEqual(call["cwd"], str(self.root / "examples/gomod-integration"))
                self.assertEqual(call["args"][1:], ["./..."])
                self.assertEqual(call["manifest"], str(self.root / "examples/gomod-integration/go.mod"))
            else:
                self.assertEqual(call["manifest"], str(self.root / "compiler/Cargo.toml"))
                args = call["args"][1:]
                if "--manifest-path" in args:
                    index = args.index("--manifest-path")
                    del args[index:index + 2]
                self.assertEqual(args, ["-p", "hyb", "--quiet"])

    def test_valid_modules_build_and_report_from_repo_root(self):
        result, calls, report = self.run_checks()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_commands(calls)
        self.assertEqual(report.count("**pass**"), 3)
        self.assertIn("test-file presence: tests: present", report)
        self.assertEqual((self.output / "preflight-warn.txt").read_text(),
                         (self.output / "testcheck.txt").read_text())

    def test_each_build_failure_stops_later_checks(self):
        for failure, count, label in (("go build", 1, "go build"),
                                      ("go vet", 2, "go vet"),
                                      ("cargo build", 3, "classifier build (hyb)")):
            with self.subTest(failure=failure):
                self.log.unlink(missing_ok=True)
                result, calls, report = self.run_checks(fail=failure)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assert_commands(calls, count)
                self.assertEqual(report.count("**pass**"), count - 1)
                self.assertIn("- " + label + ": **FAIL**", report)
                self.assertFalse((self.output / "testcheck.txt").exists())

    def test_missing_go_module_fails_before_vet_or_cargo(self):
        (self.root / "examples/gomod-integration/go.mod").unlink()
        result, calls, report = self.run_checks()
        self.assertEqual(result.returncode, 1)
        self.assert_commands(calls, 1)
        self.assertIn("go build: **FAIL**", report)

    def test_missing_cargo_manifest_fails_after_go_checks(self):
        (self.root / "compiler/Cargo.toml").unlink()
        result, calls, report = self.run_checks()
        self.assertEqual(result.returncode, 1)
        self.assert_commands(calls)
        self.assertIn("classifier build (hyb): **FAIL**", report)
        self.assertNotIn("test-file presence", report)

    def test_testless_diff_remains_a_nonfatal_warning(self):
        result, calls, report = self.run_checks(changed="README.md\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_commands(calls)
        self.assertIn("test-file presence: tests: ABSENT", report)


if __name__ == "__main__":
    unittest.main()
