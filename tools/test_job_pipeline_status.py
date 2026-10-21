"""Run committed job commands offline with synthetic tool outcomes.

Only tool implementations and fixed /tmp scratch locations are substituted.
The real shell, pipelines, runner CLI, summary, and result collection run.
No compiler, sanitizer, benchmark, installer, or network request is used.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools/runner/runner.py"


class JobPipelineStatusTests(unittest.TestCase):
    def run_step(self, job_name, step_name, outcome):
        source = json.loads((ROOT / "jobs" / job_name / "job.json").read_text())
        with tempfile.TemporaryDirectory(prefix="hybrid-job-pipeline-") as directory:
            root = Path(directory)
            repo = root / "repo"
            results = root / "results"
            (repo / "runtime/go").mkdir(parents=True)
            (repo / "runtime/go/fixture.txt").write_text("offline fixture\n")
            library = repo / "runtime/rust/hyrt/target/release/libhyrt.a"
            library.parent.mkdir(parents=True)
            library.write_text("offline library fixture\n")
            hyb = repo / "compiler/target/release/hyb"
            hyb.parent.mkdir(parents=True)
            diagnostic = "synthetic producer diagnostic"
            output = "\n".join("line-%02d" % n for n in range(35)) + "\n" + diagnostic
            if outcome != "missing":
                hyb.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(output)
                               + "\nexit " + str(outcome) + "\n")
                hyb.chmod(0o755)

            # Functions take precedence over any installed Go/Cargo binaries,
            # including the job's explicitly prepended /tmp/go/bin directory.
            producer = ("command missing-hybrid-tool-fixture" if outcome == "missing"
                        else "printf '%s\\n' " + shlex.quote(output)
                             + "; return " + str(outcome))
            prefix = "go() { " + producer + "; }; cargo() { return 0; }; "
            selected = [step for step in source["commands"]
                        if step["name"] == step_name or
                        (job_name.startswith("007-") and step["name"] == "collect-results")]
            for step in selected:
                command = step["run"]
                shell = command[-1] if isinstance(command, list) else command
                for scratch in ("asanws", "racews", "g2bench.out"):
                    shell = shell.replace("/tmp/" + scratch, str(root / scratch))
                shell = prefix + shell
                step["run"] = command[:-1] + [shell] if isinstance(command, list) else shell
                step["timeout"] = 10
            job = dict(source, setup=[], commands=selected, sha="HEAD")
            spec = root / "job.json"
            spec.write_text(json.dumps(job))
            env = dict(os.environ, HOME=str(root / "home"),
                       KAGGLE_TEST_FIXTURE="offline")
            env.pop("BASH_ENV", None)
            result = subprocess.run(
                [sys.executable, str(RUNNER), str(spec), "--repo-dir", str(repo),
                 "--results-dir", str(results)],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=30)
            self.assertNotIn("Traceback", result.stderr)
            summary = json.loads((results / "summary.json").read_text())
            executed = next(step for step in summary["steps"] if step["name"] == step_name)
            if outcome == "missing":
                self.assertRegex(executed["out_tail"], "not found|No such file or directory")
            else:
                lines = 20 if step_name == "asan-bridge" else 12 if step_name == "race-bridge" else 36
                self.assertEqual(executed["out_tail"].splitlines(), output.splitlines()[-lines:])
            if job_name.startswith("007-"):
                self.assertEqual((results / "g2bench.out").read_text(), executed["out_tail"])
                self.assertEqual(summary["steps"][-1]["name"], "collect-results")
                self.assertEqual(summary["steps"][-1]["exit"], 0)
            return result.returncode, summary["ok"], executed["exit"]

    def test_sanitizer_and_benchmark_failures_are_not_masked(self):
        for job, step in (("006-sanitizers-fuzzing", "asan-bridge"),
                          ("006-sanitizers-fuzzing", "race-bridge"),
                          ("007-worker-tier-bench", "g2-bench-worker-tier")):
            for outcome in (0, 7, "missing"):
                with self.subTest(job=job, step=step, outcome=outcome):
                    code, ok, producer_code = self.run_step(job, step, outcome)
                    expected = 127 if outcome == "missing" else outcome
                    self.assertEqual(producer_code, expected)
                    self.assertEqual(code, 0 if outcome == 0 else 1)
                    self.assertEqual(ok, outcome == 0)


if __name__ == "__main__":
    unittest.main()
