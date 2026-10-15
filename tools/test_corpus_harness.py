"""CLI regressions for optional exact diagnostic metadata and verdict markers (stdlib only)."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HARNESS = Path(__file__).with_name("corpus_harness.py")
SPEC = importlib.util.spec_from_file_location("corpus_harness", HARNESS)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ExactDiagnosticCliTests(unittest.TestCase):
    def run_harness(self, header, code="HYB-E3002", verdict="REJECT A1c"):
        # A tiny deterministic checker exercises the complete harness CLI:
        # fixture metadata -> subprocess JSON -> rule/code comparison -> exit.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "01-example.hy").write_text(
                f"// EXPECTED-VERDICT: {verdict}\n{header}\nfn main() {{}}\n",
                encoding="utf-8",
            )
            payload = {
                "summary": {"verdict": "reject"},
                "diagnostics": [{"severity": "error", "tier0_rule": "A1c", "code": code}],
            }
            checker = root / "hyb"
            checker.write_text(
                f"#!{sys.executable}\nimport json\n"
                f"print(json.dumps({payload!r}))\nraise SystemExit(1)\n",
                encoding="utf-8",
            )
            checker.chmod(0o700)
            return subprocess.run(
                [sys.executable, str(HARNESS), "--bin", str(checker), "--corpus", str(corpus)],
                capture_output=True, text=True, timeout=10,
            )

    def test_wrong_code_with_same_rule_fails(self):
        run = self.run_harness("// EXPECTED-DIAGNOSTIC: HYB-E3002", code="HYB-E3001")
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("expected first diagnostic HYB-E3002, got HYB-E3001", run.stdout)
        self.assertIn("verdict agreement: 0/1", run.stdout)

    def test_exact_code_match_passes(self):
        run = self.run_harness("// EXPECTED-DIAGNOSTIC: HYB-E3002")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("verdict agreement: 1/1", run.stdout)

    def test_old_fixture_without_header_keeps_rule_only_contract(self):
        run = self.run_harness("", code="HYB-E3001")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("verdict agreement: 1/1", run.stdout)

    def test_malformed_header_fails_closed(self):
        for header in (
            "// EXPECTED-DIAGNOSTIC: not-a-code",
            "// EXPECTED-DIAGNOSTIC HYB-E3002",
            "// EXPECTED-DIAGNOSTIC:",
        ):
            with self.subTest(header=header):
                run = self.run_harness(header)
                self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
                self.assertIn("malformed EXPECTED-DIAGNOSTIC", run.stderr)

    def test_duplicate_headers_fail_closed(self):
        run = self.run_harness(
            "// EXPECTED-DIAGNOSTIC: HYB-E3002\n// EXPECTED-DIAGNOSTIC: HYB-E3002"
        )
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("multiple EXPECTED-DIAGNOSTIC", run.stderr)

    def test_accept_fixture_cannot_declare_error_code(self):
        run = self.run_harness("// EXPECTED-DIAGNOSTIC: HYB-E3002", verdict="ACCEPT")
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("without REJECT", run.stderr)


class CheckerEnvelopeCliTests(unittest.TestCase):
    def run_payload(self, payload, expected="ACCEPT", status=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "01-example.hy").write_text(
                f"// EXPECTED-VERDICT: {expected}\nfn main() {{}}\n", encoding="utf-8"
            )
            checker = root / "hyb"
            checker.write_text(
                f"#!{sys.executable}\nimport json\n"
                f"print(json.dumps({payload!r}))\nraise SystemExit({status})\n",
                encoding="utf-8",
            )
            checker.chmod(0o700)
            return subprocess.run(
                [sys.executable, str(HARNESS), "--bin", str(checker), "--corpus", str(corpus)],
                capture_output=True, text=True, timeout=10,
            )

    def assert_invalid(self, payload, expected="ACCEPT", status=0):
        run = self.run_payload(payload, expected, status)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn("harness-error", run.stdout)
        self.assertIn("verdict agreement: 0/1", run.stdout)
        self.assertNotIn("Traceback", run.stderr)

    def test_matching_counts_accept(self):
        payload = {"summary": {"verdict": "accept", "errors": 0, "warnings": 1},
                   "diagnostics": [{"severity": "warning", "code": "HYB-E8001"}]}
        run = self.run_payload(payload, expected="ACCEPT-MANAGED")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_matching_counts_reject(self):
        payload = {"summary": {"verdict": "reject", "errors": 1, "warnings": 0},
                   "diagnostics": [{"severity": "error", "tier0_rule": "A1c"}]}
        run = self.run_payload(payload, expected="REJECT A1c", status=1)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_omitted_counts_keep_existing_contract(self):
        run = self.run_payload({"summary": {"verdict": "accept"}, "diagnostics": []})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_reported_errors_must_match_diagnostics(self):
        self.assert_invalid({"summary": {"verdict": "accept", "errors": 1},
                             "diagnostics": []})
        self.assert_invalid({"summary": {"verdict": "reject", "errors": 0},
                             "diagnostics": [{"severity": "error", "tier0_rule": "A1c"}]},
                            expected="REJECT A1c", status=1)

    def test_reported_warnings_must_match_diagnostics(self):
        self.assert_invalid({"summary": {"verdict": "accept", "warnings": 1},
                             "diagnostics": []})

    def test_counts_require_nonnegative_integers(self):
        for count in (False, True, None, "0", 0.0, -1):
            with self.subTest(count=count):
                self.assert_invalid({"summary": {"verdict": "accept", "errors": count},
                                     "diagnostics": []})

    def test_invalid_envelope_shapes_fail_without_traceback(self):
        for payload in (
            None, [], {"summary": None, "diagnostics": []},
            {"summary": [], "diagnostics": []},
            {"summary": {"verdict": "accept"}, "diagnostics": None},
            {"summary": {"verdict": "accept"}, "diagnostics": {}},
            {"summary": {"verdict": "accept"}, "diagnostics": [None]},
            {"summary": {"verdict": "accept"}, "diagnostics": ["error"]},
        ):
            with self.subTest(payload=payload):
                self.assert_invalid(payload)

    def test_extra_fields_do_not_change_acceptance(self):
        run = self.run_payload({"summary": {"verdict": "accept", "errors": 0,
                                            "warnings": 0, "future": "kept"},
                                "diagnostics": [], "version": 1, "future": {}})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class VerdictMarkerParserTests(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "case.hy"
            path.write_text(text, encoding="utf-8")
            return MODULE.read_expected(path)

    def assert_invalid(self, text):
        with self.assertRaises(SystemExit) as caught:
            self.parse(text)
        self.assertIn("corpus error", str(caught.exception))

    def test_unknown_verdict_cannot_be_silently_skipped(self):
        self.assert_invalid("// EXPECTED-VERDICT: REJET A1\nfn main() {}\n")

    def test_empty_verdict_is_invalid(self):
        self.assert_invalid("// EXPECTED-VERDICT: \nfn main() {}\n")

    def test_accept_suffix_is_not_a_valid_verdict(self):
        self.assert_invalid("// EXPECTED-VERDICT: ACCEPT-BROKEN\nfn main() {}\n")

    def test_managed_suffix_is_not_a_valid_verdict(self):
        self.assert_invalid("// EXPECTED-VERDICT: ACCEPT-MANAGED-BROKEN\n")

    def test_conflicting_markers_are_not_first_match_wins(self):
        self.assert_invalid("// EXPECTED-VERDICT: ACCEPT\n// EXPECTED-VERDICT: REJECT A1\n")

    def test_repeated_identical_markers_are_ambiguous(self):
        self.assert_invalid("// EXPECTED-VERDICT: ACCEPT\n// EXPECTED-VERDICT: ACCEPT\n")

    def test_reject_without_rule_does_not_consume_next_source_line(self):
        self.assert_invalid("// EXPECTED-VERDICT: REJECT\nfn main() {}\n")

    def test_reject_without_rule_does_not_consume_next_comment(self):
        self.assert_invalid("// EXPECTED-VERDICT: REJECT\n// reason follows\n")

    def test_accept_does_not_capture_a_rule_from_next_line(self):
        self.assertEqual(self.parse("// EXPECTED-VERDICT: ACCEPT\nfn main() {}\n"),
                         ("ACCEPT", None))

    def test_valid_reject_preserves_rule_and_inline_explanation(self):
        self.assertEqual(self.parse("// EXPECTED-VERDICT: REJECT A1b (move rule)\n"),
                         ("REJECT", "A1b"))

    def test_valid_managed(self):
        source = "// EXPECTED-VERDICT: ACCEPT-MANAGED\nfn main() {}\n"
        self.assertEqual(self.parse(source), ("ACCEPT-MANAGED", None))

    def test_unregistered_helper_is_still_skipped(self):
        self.assertIsNone(self.parse("// helper, no registered expected verdict\nfn helper() {}\n"))

    def test_tabs_and_crlf_are_accepted(self):
        self.assertEqual(self.parse("//\tEXPECTED-VERDICT:\tREJECT\tA1b\r\nfn main() {}\r\n"),
                         ("REJECT", "A1b"))


class VerdictMarkerCliTests(unittest.TestCase):
    def run_case(self, second_source):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "01-control.hy").write_text(
                "// EXPECTED-VERDICT: ACCEPT\nfn main() {}\n", encoding="utf-8")
            (corpus / "02-case.hy").write_text(second_source, encoding="utf-8")
            fake = root / "hyb"
            payload = json.dumps({"summary": {"verdict": "accept", "errors": 0}, "diagnostics": []})
            fake.write_text(
                f"#!{sys.executable}\nimport json, sys\n"
                f"print(json.dumps({payload!r}))\nsys.exit(0)\n",
                encoding="utf-8")
            fake.chmod(0o755)
            return subprocess.run(
                [sys.executable, str(HARNESS), "--bin", str(fake), "--corpus", str(corpus)],
                capture_output=True, text=True, timeout=10)

    def test_malformed_marker_causes_cli_error(self):
        res = self.run_case("// EXPECTED-VERDICT: REJET A1\nfn main() {}\n")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("corpus error", res.stderr + res.stdout)


if __name__ == "__main__":
    unittest.main()
