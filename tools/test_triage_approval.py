#!/usr/bin/env python3
"""Offline approval-result regressions through the real HTTP client."""

import contextlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage_bot as tb


class Response(io.BytesIO):
    def __init__(self, status, body=None, remaining="5000"):
        super().__init__(json.dumps(body).encode() if body is not None else b"")
        self.status = status
        self.headers = {"x-ratelimit-remaining": remaining}


class ApprovalResultTests(unittest.TestCase):
    def run_approve(self, statuses, dry=False, remaining="5000"):
        output = io.StringIO()
        prs = [{"number": n, "head": {"sha": "head-%d" % n}}
               for n in range(1, len(statuses) + 1)]
        runs = [{"id": n, "head_sha": "head-%d" % n, "name": "CI"}
                for n in range(1, len(statuses) + 1)]

        def respond(request, timeout):
            if request.method == "GET":
                if "/pulls?" in request.full_url:
                    return Response(200, prs)
                if "/actions/runs?" in request.full_url:
                    return Response(200, {"workflow_runs": runs})
            elif request.method == "POST" and request.full_url.endswith("/approve"):
                n = int(request.full_url.split("/")[-2])
                status = statuses[n - 1]
                if status >= 400:
                    raise HTTPError(request.full_url, status, "failure", {},
                                    io.BytesIO(b'{"message":"Not Found"}'))
                return Response(status, remaining=remaining)
            self.fail("unexpected HTTP request: %s %s" % (request.method, request.full_url))

        with patch.object(tb, "urlopen", side_effect=respond), \
                patch.object(tb, "load_token", return_value="offline-placeholder"), \
                contextlib.redirect_stdout(output):
            tb.cmd_approve(SimpleNamespace(max=30, dry=dry))
        return output.getvalue()

    def test_not_found_is_not_counted_as_approved(self):
        output = self.run_approve([404])
        self.assertIn("FAIL #1", output)
        self.assertIn("404", output)
        self.assertIn("approved 0/1", output)
        self.assertNotIn("  approved: #1", output)

    def test_missing_resource_get_semantics_are_preserved(self):
        error = HTTPError("https://api.github.com/offline", 404, "Not Found", {},
                          io.BytesIO(b'{"message":"Not Found"}'))
        with patch.object(tb, "urlopen", side_effect=error):
            self.assertEqual(tb.Gh("offline/repo", "offline-placeholder").req(
                "GET", "/offline"), (404, None))

    def test_successful_empty_response_is_counted(self):
        output = self.run_approve([204])
        self.assertIn("  approved: #1", output)
        self.assertIn("approved 1/1", output)

    def test_not_found_does_not_hide_following_success(self):
        output = self.run_approve([404, 204])
        self.assertIn("FAIL #1", output)
        self.assertIn("  approved: #2", output)
        self.assertIn("approved 1/2", output)

    def test_other_http_failure_remains_a_failure(self):
        output = self.run_approve([403])
        self.assertIn("FAIL #1", output)
        self.assertIn("approved 0/1", output)

    def test_dry_run_does_not_claim_approval(self):
        output = self.run_approve([404], dry=True)
        self.assertIn("DRY would approve", output)
        self.assertIn("approved 0/1", output)
        self.assertNotIn("FAIL", output)

    def test_quota_guard_stops_before_following_approval(self):
        output = self.run_approve([204, 204], remaining="1")
        self.assertIn("rate limit low", output)
        self.assertNotIn("  approved: #2", output)


if __name__ == "__main__":
    unittest.main()
