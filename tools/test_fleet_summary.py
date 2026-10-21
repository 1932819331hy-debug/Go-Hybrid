"""Run the heavy workflow's actual reducer offline against shard artifacts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/heavy.yml"


class FleetSummaryTests(unittest.TestCase):
    def reduce(self, artifacts, expected=2, extra_files=None):
        # Execute the production heredoc, not a second implementation of it.
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step = workflow.split("      - name: Merge into fleet summary\n", 1)[1]
        block = step.split("          python3 - <<'EOF'\n", 1)[1].split("          EOF", 1)[0]
        script = textwrap.dedent(block)
        for expression, value in (
            ("github.event.inputs.job", "fixture-job"),
            ("github.event.inputs.sha", "a" * 40),
            ("github.run_id", "42"),
            ("github.event.inputs.shards", str(expected)),
        ):
            script = script.replace("${{ " + expression + " }}", value)
        generator = workflow.split("      - id: mk\n        run: |\n", 1)[1].split("\n  shard:", 1)[0]
        generator = textwrap.dedent(generator).replace("${{ github.event.inputs.shards }}", str(expected))
        with tempfile.TemporaryDirectory(prefix="fleet-summary-") as directory:
            root = Path(directory)
            output = root / "github-output"
            generated = subprocess.run(
                ["bash", "-e", "-c", generator], cwd=root,
                env={**os.environ, "GITHUB_OUTPUT": str(output)},
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
            matrix = output.read_text(encoding="utf-8").strip().removeprefix("matrix=")
            for index, (shard, payload) in enumerate(artifacts):
                target = root / "shards" / ("artifact-%d" % index)
                target.mkdir(parents=True)
                (target / ("shard-%s-summary.json" % shard)).write_text(payload, encoding="utf-8")
            for relative, contents in (extra_files or {}).items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(contents, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=root,
                env={**os.environ, "EXPECTED_SHARDS_JSON": matrix},
                capture_output=True, text=True, timeout=10,
            )
            summary = json.loads((root / "fleet-summary.json").read_text(encoding="utf-8"))
            return result, summary

    @staticmethod
    def shard(shard, ok=True):
        return (shard, json.dumps({"ok": ok, "hardware": {"nproc": "2"},
                                  "steps": [{"name": "check", "exit": 0 if ok is True else 1,
                                             "dur_s": 0.5}], "wall_clock_s": 1.0}))

    def assert_failed(self, result, summary):
        self.assertFalse(summary["ok"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_no_shards_cannot_report_success(self):
        result, summary = self.reduce([])
        self.assert_failed(result, summary)
        self.assertEqual(summary["missing_shards"], ["0", "1"])

    def test_missing_shard_cannot_report_success(self):
        result, summary = self.reduce([self.shard("0")])
        self.assert_failed(result, summary)
        self.assertEqual(summary["missing_shards"], ["1"])
        self.assertEqual(summary["shard_count"], 1)

    def test_duplicate_cannot_stand_in_for_missing_shard(self):
        result, summary = self.reduce([self.shard("0"), self.shard("0")])
        self.assert_failed(result, summary)
        self.assertEqual(summary["missing_shards"], ["1"])
        self.assertEqual(summary["duplicate_shards"], ["0"])

    def test_duplicate_is_rejected_even_when_all_ids_are_present(self):
        result, summary = self.reduce([self.shard("0"), self.shard("1"), self.shard("1")])
        self.assert_failed(result, summary)
        self.assertEqual(summary["duplicate_shards"], ["1"])

    def test_unexpected_or_malformed_id_is_not_a_complete_fleet(self):
        for shard in ("2", "01", "-1", "invalid"):
            with self.subTest(shard=shard):
                result, summary = self.reduce([self.shard("0"), self.shard(shard)])
                self.assert_failed(result, summary)
                self.assertEqual(summary["unexpected_shards"], [shard])

    def test_failed_shard_fails_reducer_after_writing_summary(self):
        result, summary = self.reduce([self.shard("0"), self.shard("1", False)])
        self.assert_failed(result, summary)
        self.assertEqual(summary["shard_count"], 2)

    def test_non_boolean_verdict_does_not_pass(self):
        for verdict in ("false", 1, {"ok": True}, None):
            with self.subTest(verdict=verdict):
                result, summary = self.reduce([self.shard("0"), self.shard("1", verdict)])
                self.assert_failed(result, summary)

    def test_malformed_summary_is_reported_without_losing_valid_results(self):
        for payload in ("{invalid", "null", "[]", "{}",
                        '{"ok":true,"hardware":null}',
                        '{"ok":true,"steps":[{}]}'):
            with self.subTest(payload=payload):
                result, summary = self.reduce([self.shard("0"), ("1", payload)])
                self.assert_failed(result, summary)
                self.assertTrue(summary["errors"])
                self.assertEqual(summary["shards"][0]["shard"], "0")

    def test_complete_success_preserves_shard_details_and_ignores_other_files(self):
        result, summary = self.reduce([self.shard("1"), self.shard("0")], extra_files={
            "shards/diagnostics/runner.log": "optional diagnostic",
            "shards/diagnostics/summary.json": "unrelated optional output",
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["shard_count"], 2)
        self.assertEqual(summary["job"], "fixture-job")
        self.assertEqual(summary["sha"], "a" * 40)
        self.assertEqual(summary["shards"][0]["hardware"]["nproc"], "2")
        self.assertEqual(summary["shards"][0]["steps"],
                         [{"name": "check", "exit": 0, "dur_s": 0.5}])

    def test_workflow_passes_matrix_and_uploads_summary_even_on_failure(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        reducer = workflow.split("\n  reduce:\n", 1)[1]
        self.assertIn("    needs: [gen, shard]\n", reducer)
        merge = reducer.split("      - name: Merge into fleet summary\n", 1)[1]
        self.assertIn("          EXPECTED_SHARDS_JSON: ${{ needs.gen.outputs.matrix }}\n", merge)
        upload = reducer.split("      - name: Upload fleet summary (3-day retention)\n", 1)[1]
        self.assertIn("        if: always()\n", upload)

    def test_zero_requested_shards_do_not_pass_vacuously(self):
        result, summary = self.reduce([], expected=0)
        self.assert_failed(result, summary)


if __name__ == "__main__":
    unittest.main()
