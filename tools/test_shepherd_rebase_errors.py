#!/usr/bin/env python3
"""Offline HTTP regressions for W2 failure classification (issue #986)."""
import contextlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shepherd as sh


class Response:
    def __init__(self, body, status=200):
        self.status = status
        self.headers = {}
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class RebaseErrors(unittest.TestCase):
    def run_rebase(self, status=202, message="failure", dry=False,
                   labels=(), draft=False, raw_error=None):
        """Run the real command and Gh client; replace only offline boundaries."""
        calls = []
        logs = io.StringIO()
        cfg = {"repo": "owner/repo", "rebase": {
            "exempt_labels": ["bot:conflict"], "scan_window": 30,
            "behind_min": 50, "max_per_run": 1}}

        def transport(request, timeout):
            method = request.get_method()
            path = urlsplit(request.full_url).path
            body = json.loads(request.data) if request.data else None
            calls.append((method, path, body))
            if method == "GET" and path.endswith("/commits/main"):
                return Response({"sha": "base"})
            if method == "GET" and path.endswith("/pulls"):
                return Response([{"number": 12, "created_at": "2026-01-01",
                                  "draft": draft,
                                  "labels": [{"name": name} for name in labels]}])
            if method == "GET" and path.endswith("/pulls/12"):
                return Response({"mergeable": True, "head": {"sha": "head"}})
            if method == "GET" and "/compare/" in path:
                return Response({"behind_by": 60})
            if method == "PUT" and path.endswith("/update-branch"):
                if raw_error is not None:
                    raise RuntimeError(raw_error)
                if status != 202:
                    raise HTTPError(request.full_url, status, message, {},
                                    io.BytesIO(json.dumps({"message": message}).encode()))
                return Response({}, status=202)
            if method == "GET" and path.endswith("/labels"):
                return Response([])
            if method == "POST" and path.endswith(("/labels", "/comments")):
                return Response({}, status=201)
            raise AssertionError("Unexpected HTTP request: %s %s" % (method, path))

        with patch.object(sh, "load_cfg", return_value=cfg), \
             patch.object(sh.tb, "load_token", return_value="offline-test"), \
             patch.object(sh.tb, "urlopen", side_effect=transport), \
             patch.object(sh.tb.time, "sleep") as sleep, \
             patch.object(sh, "SUMMARY", None), \
             contextlib.redirect_stdout(logs):
            sh.cmd_rebase(SimpleNamespace(dry=dry))
        return calls, logs.getvalue(), sleep.call_count

    def assert_non_conflict_failure(self, status):
        calls, logs, sleeps = self.run_rebase(status)
        self.assertIn("#12 FAILED:", logs)
        self.assertIn("-> %d" % status, logs)
        self.assertNotIn("CAS ok", logs)
        self.assertEqual([], [c for c in calls if c[0] == "POST"])
        retries = 3 if status in (429, 502, 503, 504) else 0
        self.assertEqual(retries, sleeps)
        self.assertEqual(retries + 1, sum(c[0] == "PUT" for c in calls))

    def test_forbidden_is_not_a_merge_conflict(self):
        self.assert_non_conflict_failure(403)

    def test_exhausted_secondary_rate_limit_is_not_a_merge_conflict(self):
        calls, logs, sleeps = self.run_rebase(403, "secondary rate limit exceeded")
        self.assertEqual(3, sleeps)
        self.assertEqual(4, sum(c[0] == "PUT" for c in calls))
        self.assertIn("#12 FAILED:", logs)
        self.assertIn("-> 403", logs)
        self.assertNotIn("CAS ok", logs)
        self.assertEqual([], [c for c in calls if c[0] == "POST"])

    def test_exhausted_rate_limit_is_not_a_merge_conflict(self):
        self.assert_non_conflict_failure(429)

    def test_exhausted_bad_gateway_is_not_a_merge_conflict(self):
        self.assert_non_conflict_failure(502)

    def test_exhausted_unavailable_is_not_a_merge_conflict(self):
        self.assert_non_conflict_failure(503)

    def test_exhausted_gateway_timeout_is_not_a_merge_conflict(self):
        self.assert_non_conflict_failure(504)

    def test_accepted_update_preserves_cas_and_success(self):
        calls, logs, _ = self.run_rebase()
        self.assertIn("CAS ok", logs)
        self.assertEqual([("PUT", "/repos/owner/repo/pulls/12/update-branch",
                           {"expected_head_sha": "head"})],
                         [c for c in calls if c[0] != "GET"])

    def test_cas_failure_keeps_skip_behavior(self):
        calls, logs, _ = self.run_rebase(422, "Head branch was modified")
        self.assertIn("CAS mismatch", logs)
        self.assertEqual([], [c for c in calls if c[0] == "POST"])

    def test_other_error_preserves_existing_conflict_behavior(self):
        calls, logs, _ = self.run_rebase(409, "Merge conflict")
        posts = [c for c in calls if c[0] == "POST"]
        self.assertEqual(2, len(posts))
        self.assertEqual({"labels": ["bot:conflict"]}, posts[0][2])
        self.assertIn("Merge conflict", posts[1][2]["body"])
        self.assertIn("FAILED", logs)

    def test_status_digits_in_body_do_not_change_unknown_error(self):
        calls, _, _ = self.run_rebase(409, "Details mention 403 and -> 503")
        self.assertEqual(2, sum(c[0] == "POST" for c in calls))

    def test_unstructured_error_preserves_existing_behavior(self):
        calls, _, _ = self.run_rebase(raw_error="unexpected error 503")
        self.assertEqual(2, sum(c[0] == "POST" for c in calls))

    def test_dry_run_never_mutates(self):
        calls, logs, _ = self.run_rebase(dry=True)
        self.assertIn("DRY would update-branch", logs)
        self.assertTrue(all(c[0] == "GET" for c in calls))

    def test_exempt_or_draft_pr_is_not_updated(self):
        for options in ({"labels": ["bot:conflict"]}, {"draft": True}):
            with self.subTest(options=options):
                calls, logs, _ = self.run_rebase(**options)
                self.assertTrue(all(c[0] == "GET" for c in calls))
                self.assertNotIn("CAS ok", logs)


if __name__ == "__main__":
    unittest.main()
