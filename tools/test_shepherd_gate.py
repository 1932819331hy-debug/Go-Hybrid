#!/usr/bin/env python3
"""test_shepherd_gate.py — offline self-tests for tools/shepherd.py.

No network, no subprocesses: only the pure functions that the workflows lean
on. If the kill-switch matrix, the dup planner, the conflict classifier, or
the probe parser drift, CI fails before any workflow can.
"""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shepherd as sh  # noqa: E402


class GateMatrix(unittest.TestCase):
    """The kill switch is the safety floor for every workflow (plan §1.5)."""

    def test_off_blocks_everything(self):
        for req in ("read", "dry-run", "live"):
            self.assertFalse(sh.mode_allows("off", req), req)

    def test_dry_run_allows_read_and_comment_only(self):
        self.assertTrue(sh.mode_allows("dry-run", "read"))
        self.assertTrue(sh.mode_allows("dry-run", "dry-run"))
        self.assertFalse(sh.mode_allows("dry-run", "live"))

    def test_live_allows_all(self):
        for req in ("read", "dry-run", "live"):
            self.assertTrue(sh.mode_allows("live", req), req)

    def test_unknown_mode_falls_back_to_dry_run(self):
        self.assertTrue(sh.mode_allows("bogus", "read"))
        self.assertFalse(sh.mode_allows("bogus", "live"))


def _item(n, **kw):
    it = {"number": n, "wildcard_hold": False, "draft_parked": False,
          "dup_members": None, "dup_of": None}
    it.update(kw)
    return it


class DupPlanner(unittest.TestCase):
    """W4 Phase A must never propose against closed survivors, wildcard
    holds, or drafts (the [474, 616, 945] and [620, 910] lessons)."""

    def test_open_cluster_planned(self):
        items = [_item(5, dup_members=[6, 7]), _item(6), _item(7)]
        clusters = [[5, 6, 7]]
        plan = sh.plan_phase_a(items, clusters, is_open=lambda n: n in (5, 6, 7))
        self.assertEqual(plan, [(5, [6, 7])])

    def test_closed_survivor_skips_cluster(self):
        items = [_item(620, dup_members=[910]), _item(910)]
        clusters = [[620, 910]]
        plan = sh.plan_phase_a(items, clusters, is_open=lambda n: n == 910)
        self.assertEqual(plan, [])

    def test_wildcard_survivor_and_members_skipped(self):
        items = [_item(616, dup_members=[474, 945], wildcard_hold=True),
                 _item(474, wildcard_hold=True), _item(945)]
        clusters = [[474, 616, 945]]
        plan = sh.plan_phase_a(items, clusters, is_open=lambda n: True)
        self.assertEqual(plan, [])

    def test_wildcard_member_filtered_leaves_no_pairs(self):
        # cluster [384, 572]: survivor 572 open, member 384 wildcard-held ->
        # the only member is filtered out, so the cluster yields nothing.
        items = [_item(572, dup_members=[384]), _item(384, wildcard_hold=True)]
        clusters = [[384, 572]]
        plan = sh.plan_phase_a(items, clusters, is_open=lambda n: True)
        self.assertEqual(plan, [])


class ConflictClassifier(unittest.TestCase):
    """Docs-only conflicts resolve deterministically; anything else is human
    territory (the #870 policy, plan §3 W5)."""

    def test_docs_only(self):
        docs, real = sh.classify_conflicts(["PLAN.md", "STATUS.md"],
                                           {"PLAN.md", "STATUS.md"})
        self.assertEqual(docs, ["PLAN.md", "STATUS.md"])
        self.assertEqual(real, [])

    def test_any_code_conflict_is_real(self):
        docs, real = sh.classify_conflicts(["PLAN.md", "src/hycheck/buildable.rs"],
                                           {"PLAN.md", "STATUS.md"})
        self.assertEqual(docs, ["PLAN.md"])
        self.assertEqual(real, ["src/hycheck/buildable.rs"])


class ProbeParser(unittest.TestCase):
    """W6 parses added diff lines only, and matches the #804 registry entry."""

    DIFF = ("diff --git a/x.hy b/x.hy\n"
            "--- a/x.hy\n"
            "+++ b/x.hy\n"
            "@@ -1,3 +1,4 @@\n"
            " keep me\n"
            "-removed line with make(<-chan int)\n"
            "+let c = make(<-chan int, 1)\n"
            "+++ not an added line\n")

    def test_added_lines_skip_context_removed_and_file_headers(self):
        # '+'-lines only: context, '-'-lines, and '+++' file headers are not
        # additions (a removed line proving a premise does not count).
        lines = list(sh.added_lines(self.DIFF))
        self.assertEqual(lines, ["let c = make(<-chan int, 1)"])

    def test_registry_matches_added_only(self):
        rule = sh.PROBES[0]
        added = list(sh.added_lines(self.DIFF))
        self.assertTrue(any(re.search(rule["pattern"], ln) for ln in added))
        self.assertFalse(any("removed line" in ln for ln in added))



class BoardW7Verdict(unittest.TestCase):
    """A failed W1 board/label refresh must never emit an OK W7 receipt."""

    def test_always_log_uses_actual_prior_job_status(self):
        path = Path(__file__).resolve().parent.parent / ".github/workflows/shepherd-board.yml"
        workflow = path.read_text(encoding="utf-8")
        match = re.search(
            r"- name: W7 session log\s*\n\s*if: always\(\)\s*\n\s*run: ([^\n]+)",
            workflow,
        )
        self.assertIsNotNone(match, "W7 must still run after failures")
        command = match.group(1)
        self.assertIn('--verdict "${{ job.status }}"', command)
        self.assertNotIn("board + labels refreshed", command)


class BoardLabelErrors(unittest.TestCase):
    """The W1 label step must fail closed when a write did not complete."""

    @staticmethod
    def _palette(error):
        gh = SimpleNamespace(repo="owner/repo", post=Mock(side_effect=error))
        cfg = {"bot_labels": {
            "bot:conflict": {"color": "123456", "description": "test"}
        }}
        return gh, cfg

    def test_existing_label_is_idempotent(self):
        response = (
            'POST /repos/owner/repo/labels -> 422 '
            '{"message":"Validation Failed","errors":'
            '[{"resource":"Label","code":"already_exists","field":"name"}]}'
        )
        gh, cfg = self._palette(RuntimeError(response))
        sh.ensure_palette(gh, cfg)
        gh.post.assert_called_once()

    def test_unrelated_422_is_not_silenced(self):
        gh, cfg = self._palette(RuntimeError(
            'POST /repos/owner/repo/labels -> 422 '
            '{"errors":[{"resource":"Label","code":"invalid","field":"color"}]}'
        ))
        with self.assertRaisesRegex(RuntimeError, "422"):
            sh.ensure_palette(gh, cfg)

    def test_forbidden_palette_write_fails(self):
        gh, cfg = self._palette(RuntimeError(
            "POST /repos/owner/repo/labels -> 403 permission denied"
        ))
        with self.assertRaisesRegex(RuntimeError, "403"):
            sh.ensure_palette(gh, cfg)

    def _sync(self, error=None):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scan.json"
            path.write_text(json.dumps({"items": [
                {"number": 42, "bucket": "LAND-CANDIDATE"}
            ]}), encoding="utf-8")
            args = SimpleNamespace(from_scan=str(path), dry=False)
            gh = SimpleNamespace(
                repo="owner/repo", calls=0, remaining=500,
                post=Mock(side_effect=error),
            )
            with patch.object(sh, "load_cfg", return_value={
                "repo": "owner/repo", "bot_labels": {}
            }), patch.object(sh.tb, "Gh", return_value=gh), patch.object(
                sh.tb, "load_token", return_value="fake-test-token"
            ):
                sh.cmd_sync_labels(args)
            return gh

    def test_successful_sync_exits_successfully(self):
        self.assertEqual(self._sync().post.call_count, 1)

    def test_forbidden_sync_fails_instead_of_exiting_zero(self):
        with self.assertRaisesRegex(RuntimeError, "failed PRs: #42"):
            self._sync(RuntimeError("403 Resource not accessible"))

    def test_rate_guard_sync_fails_instead_of_exiting_zero(self):
        with self.assertRaisesRegex(RuntimeError, "failed PRs: #42"):
            self._sync(sh.tb.RateGuard("rate limit low"))


if __name__ == "__main__":
    unittest.main()
