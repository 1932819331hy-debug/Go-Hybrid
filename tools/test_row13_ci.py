"""Run the committed row-13 CI shell with offline tool outcomes.

Bash, file edits, and the workflow body are real. Cargo/Hybrid/Go fixtures
avoid compiler builds in quick CI. Scratch paths are isolated per run.
"""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
STEP = "      - name: Row-13 sample module (existing go.mod integration)"


def row13_script():
    lines = (ROOT / ".github/workflows/ci.yml").read_text().splitlines()
    start = lines.index("        run: |", lines.index(STEP)) + 1
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("      - name:"))
    return textwrap.dedent("\n".join(lines[start:end]))


def run_step(failure="", drift_detected=True, real_go=None):
    with tempfile.TemporaryDirectory(prefix="hybrid-row13-") as directory:
        root = Path(directory)
        if real_go:
            # Bound Go VCS discovery to this fixture, not an unrelated ancestor.
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           capture_output=True, timeout=10)
        sample = root / "examples/gomod-integration"
        sample.mkdir(parents=True)
        (sample / "calc.hy").write_text("acc += x\n")
        (sample / "go.mod").write_text("module row13.fixture\n\ngo 1.26\n")
        (sample / "main.go").write_text("package main\nfunc main() { undefinedRow13Symbol() }\n")
        log = root / "calls"
        bindir = root / "bin"
        bindir.mkdir()
        hyb = root / "compiler/target/release/hyb"
        hyb.parent.mkdir(parents=True)
        fixture = (
            f"#!{sys.executable}\n"
            "import os, sys\nfrom pathlib import Path\n"
            "name = Path(sys.argv[0]).name\nargs = sys.argv[1:]\n"
            "with open(os.environ['ROW13_LOG'], 'a') as log:\n"
            "    log.write(name + ' ' + ' '.join(args) + '\\n')\n"
            "if name == 'go':\n"
            "    if os.environ.get('ROW13_REAL_GO'):\n"
            "        os.execv(os.environ['ROW13_REAL_GO'], [os.environ['ROW13_REAL_GO'], *args])\n"
            "    if args[0] == os.environ['ROW13_FAIL']:\n"
            "        print('controlled Go failure: ' + args[0], file=sys.stderr)\n"
            "        sys.exit(7)\n"
            "if name == 'hyb' and args == ['generate', '--check', '.']:\n"
            "    if '2 * x' in Path('calc.hy').read_text() and os.environ['ROW13_DRIFT'] == '1':\n"
            "        sys.exit(1)\n"
        )
        for tool in (bindir / "go", bindir / "cargo", hyb):
            tool.write_text(fixture)
            tool.chmod(0o755)
        env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""),
                   ROW13_LOG=str(log), ROW13_FAIL=failure, ROW13_DRIFT=str(int(drift_detected)),
                   ROW13_REAL_GO=str(real_go or ""))
        env.pop("BASH_ENV", None)
        script = row13_script().replace("/tmp/calc.bak", shlex.quote(str(root / "calc.bak")))
        result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c", script],
                                cwd=root, env=env, capture_output=True, text=True, timeout=60)
        return result, log.read_text().splitlines(), (sample / "calc.hy").read_text()


class Row13CiTests(unittest.TestCase):
    def test_each_go_failure_stops_before_freshness_and_success(self):
        commands = ["build ./...", "vet ./...", "test ./...", "run ."]
        for index, command in enumerate(commands):
            with self.subTest(command=command):
                result, calls, source = run_step(failure=command.split()[0])
                self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
                self.assertEqual([c for c in calls if c.startswith("go ")],
                                 ["go " + c for c in commands[:index + 1]])
                self.assertNotIn("hyb generate --check .", calls)
                self.assertNotIn("row-13 sample: OK", result.stdout)
                self.assertEqual(source, "acc += x\n")

    def test_success_runs_every_go_command_and_restores_source(self):
        result, calls, source = run_step()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, ["cargo build --release -p hyb --quiet",
                                "hyb vendor-runtime .", "hyb generate .",
                                "go build ./...", "go vet ./...", "go test ./...", "go run .",
                                "hyb generate --check .", "hyb generate --check .",
                                "hyb generate --check ."])
        self.assertEqual(source, "acc += x\n")
        self.assertIn("row-13 sample: OK", result.stdout)

    def test_missed_semantic_drift_still_fails(self):
        result, calls, _ = run_step(drift_detected=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAILED to catch semantic drift", result.stderr)
        self.assertNotIn("row-13 sample: OK", result.stdout)
        self.assertEqual(calls.count("hyb generate --check ."), 2)


if __name__ == "__main__":
    unittest.main()
