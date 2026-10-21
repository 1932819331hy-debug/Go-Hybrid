#!/usr/bin/env python3
"""test_shepherd_gate.py — offline self-tests for tools/shepherd.py.

No network, no subprocesses: pure functions and an in-memory HTTP boundary.
Covers the kill-switch matrix, dup planner, conflict classifier, probe parser,
and content-preserving session-log retries.
"""

import base64
import io
import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

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


class ContentsAPI:
    """In-memory CAS store behind the real triage_bot.Gh HTTP client."""

    def __init__(self, content, concurrent=(), fail_read=None, low_quota=False):
        self.content = content
        self.version = 1
        self.concurrent = list(concurrent)
        self.fail_read = fail_read
        self.low_quota = low_quota
        self.puts = []
        self.reads = 0

    def urlopen(self, request, timeout):
        method, url = request.get_method(), request.full_url
        headers = {"x-ratelimit-remaining": "500"}
        if "/git/ref/heads/" in url:
            status, data = 200, {"object": {"sha": "branch-head"}}
        elif method == "GET" and "/contents/" in url:
            self.reads += 1
            if self.fail_read == self.reads:
                raise HTTPError(url, 403, "forbidden", {}, io.BytesIO(b"read failed"))
            if self.content is None:
                raise HTTPError(url, 404, "missing", {}, io.BytesIO(b""))
            status, data = 200, {"sha": str(self.version), "content":
                base64.b64encode(self.content.encode()).decode()}
        elif method == "PUT" and "/contents/" in url:
            payload = json.loads(request.data)
            self.puts.append(payload)
            if self.concurrent:
                # A competing writer commits after our GET, before our PUT.
                self.content = self.concurrent.pop(0)
                self.version += 1
            expected_sha = str(self.version) if self.content is not None else None
            if payload.get("sha") != expected_sha:
                raise HTTPError(url, 409, "conflict", {}, io.BytesIO(b"stale SHA"))
            status = 201 if self.content is None else 200
            self.content = base64.b64decode(payload["content"]).decode()
            self.version += 1
            data = {"content": {"sha": str(self.version)}}
            if self.low_quota:
                headers["x-ratelimit-remaining"] = "0"
        else:
            raise AssertionError("unexpected HTTP request: %s %s" % (method, url))
        response = io.BytesIO(json.dumps(data).encode())
        response.status, response.headers = status, headers
        return response


class LogAppend(unittest.TestCase):
    EXISTING = '{"event":"existing"}\n'
    OTHER = '{"event":"other"}\n'
    THIRD = '{"event":"third"}\n'

    def run_log(self, api):
        args = SimpleNamespace(event="new", pr=42, verdict="ok", sha="head",
                               note="café", token_env="UNUSED")
        with patch.object(sh.tb, "urlopen", side_effect=api.urlopen), \
             patch.object(sh.tb, "load_token", return_value="offline-fixture"), \
             patch.object(sh, "load_cfg", return_value={"repo": "offline/repo",
                 "log_branch": "automation/session-log", "log_path": "logs/SESSION.jsonl"}), \
             patch.object(sh, "run_url", return_value="offline-run"), \
             patch.object(sh, "step_summary") as summary, \
             patch.object(sh.tb, "log") as log:
            sh.cmd_log(args)
        messages = [call.args[0] for call in log.call_args_list]
        self.assertEqual(summary.call_count, 1)
        self.assertIn('"event": "new"', summary.call_args.args[0])
        return messages

    def assert_appended(self, api, prefix):
        self.assertIsNotNone(api.content)
        self.assertTrue(api.content.startswith(prefix), api.content)
        suffix = api.content[len(prefix):]
        self.assertEqual(len(suffix.splitlines()), 1)
        record = json.loads(suffix)
        self.assertEqual(record["event"], "new")
        self.assertEqual(record["note"], "café")
        self.assertEqual(record["pr"], 42)
        self.assertEqual(record["run"], "offline-run")
        self.assertTrue(suffix.endswith("\n"))
        self.assertIn("café", suffix)
        self.assertEqual(api.puts[-1]["branch"], "automation/session-log")
        self.assertEqual(api.puts[-1]["message"], "shepherd-log: new 42")

    def test_no_conflict_preserves_formatting_and_creates_missing_log(self):
        for original in (None, "", self.EXISTING, self.EXISTING.rstrip("\n")):
            with self.subTest(original=original):
                api = ContentsAPI(original)
                messages = self.run_log(api)
                self.assert_appended(api, self.EXISTING if original else "")
                self.assertEqual(len(api.puts), 1)
                self.assertTrue(any(m.startswith("logged:") for m in messages))
                self.assertEqual("sha" in api.puts[0], original is not None)

    def test_conflict_preserves_concurrent_append(self):
        api = ContentsAPI(self.EXISTING, [self.EXISTING + self.OTHER])
        self.run_log(api)
        self.assert_appended(api, self.EXISTING + self.OTHER)
        self.assertEqual(len(api.puts), 2)

    def test_multiple_conflicts_preserve_every_append(self):
        api = ContentsAPI(self.EXISTING, [self.EXISTING + self.OTHER,
                                         self.EXISTING + self.OTHER + self.THIRD])
        self.run_log(api)
        self.assert_appended(api, self.EXISTING + self.OTHER + self.THIRD)
        self.assertEqual(len(api.puts), 3)

    def test_competing_creation_keeps_other_writers_first_record(self):
        api = ContentsAPI(None, [self.OTHER])
        self.run_log(api)
        self.assert_appended(api, self.OTHER)
        self.assertEqual(len(api.puts), 2)

    def test_retry_normalizes_missing_final_newline(self):
        api = ContentsAPI(self.EXISTING, [self.EXISTING + self.OTHER.rstrip("\n")])
        self.run_log(api)
        self.assert_appended(api, self.EXISTING + self.OTHER)

    def test_deleted_log_is_recreated_without_stale_sha_or_content(self):
        api = ContentsAPI(self.EXISTING, [None])
        self.run_log(api)
        self.assert_appended(api, "")
        self.assertEqual(len(api.puts), 2)
        self.assertNotIn("sha", api.puts[-1])

    def test_terminal_conflict_is_nonfatal_and_does_not_claim_success(self):
        latest = self.EXISTING + self.OTHER + self.THIRD
        api = ContentsAPI(self.EXISTING, [self.OTHER, self.THIRD, latest])
        messages = self.run_log(api)
        self.assertEqual(api.content, latest)
        self.assertEqual(len(api.puts), 3)
        self.assertTrue(any("LOG-WRITE-FAILED after retries" in m for m in messages))
        self.assertFalse(any(m.startswith("logged:") for m in messages))

    def test_read_error_is_nonfatal_and_does_not_write_stale_content(self):
        for fail_read in (1, 2):
            with self.subTest(fail_read=fail_read):
                api = ContentsAPI(self.EXISTING, [self.EXISTING + self.OTHER],
                                  fail_read=fail_read)
                messages = self.run_log(api)
                self.assertEqual(len(api.puts), fail_read - 1)
                self.assertTrue(any("LOG-WRITE-FAILED (non-fatal" in m and
                                    "403" in m for m in messages))
                self.assertFalse(any(m.startswith("logged:") for m in messages))

    def test_rate_guard_is_nonfatal_and_not_retried(self):
        api = ContentsAPI(self.EXISTING, low_quota=True)
        messages = self.run_log(api)
        self.assertEqual(len(api.puts), 1)
        self.assertTrue(any("rate limit low" in m for m in messages))
        self.assertFalse(any(m.startswith("logged:") for m in messages))


if __name__ == "__main__":
    unittest.main()
