"""Offline actual PUT404 success-accounting contract. No network writes.

Uses the real cmd_rebase and Gh HTTP adapter on the fetched checkout. Only the
transport, token loader, logging, sleep and step-summary sinks are replaced.
"""
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

sys.path.insert(0, str(Path.cwd() / 'tools'))
import shepherd as sh


class Response:
    def __init__(self, status, value):
        self.status = status
        self.value = value
        self.headers = {'x-ratelimit-remaining': '5000'}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return json.dumps(self.value).encode()


def invoke(*, update=202, mergeable=True, draft=False, labels=(), dry=False,
           low_quota=False):
    repo = sh.load_cfg()['repo']
    prefix = '/repos/' + repo
    calls, logs = [], []
    pr = {'number': 27, 'draft': draft, 'created_at': '2026-09-01T00:00:00Z',
          'labels': [{'name': v} for v in labels]}
    def transport(request, timeout):
        path = urlsplit(request.full_url).path
        method = request.get_method()
        body = json.loads(request.data) if request.data else None
        calls.append((method, path, body))
        if method == 'GET' and path == prefix + '/commits/main':
            return Response(200, {'sha': 'a' * 40})
        if method == 'GET' and path == prefix + '/pulls':
            return Response(200, [pr])
        if method == 'GET' and path == prefix + '/pulls/27':
            return Response(200, {'mergeable': mergeable, 'head': {'sha': 'b' * 40}})
        if method == 'GET' and path.startswith(prefix + '/compare/'):
            return Response(200, {'behind_by': 80})
        if method == 'PUT' and path == prefix + '/pulls/27/update-branch':
            if update >= 400:
                message = ('Head branch was modified' if update == 422
                           else 'Service unavailable' if update == 503
                           else 'Resource not accessible by integration')
                raise HTTPError(request.full_url, update, message, {},
                                io.BytesIO(json.dumps({'message': message}).encode()))
            response = Response(update, {'message': 'Updating branch'})
            if low_quota:
                response.headers['x-ratelimit-remaining'] = '119'
            return response
        if method == 'GET' and path == prefix + '/issues/27/labels':
            return Response(200, [])
        if method == 'POST' and path in (prefix + '/issues/27/labels',
                                        prefix + '/issues/27/comments'):
            return Response(201, body)
        raise AssertionError('Unexpected offline request: %r' % (calls[-1],))
    with patch.object(sh.tb, 'urlopen', transport), \
         patch.object(sh.tb, 'load_token', return_value='offline-fixture'), \
         patch.object(sh.tb.time, 'sleep'), \
         patch.object(sh.tb, 'log', side_effect=logs.append), \
         patch.object(sh, 'step_summary'):
        try:
            sh.cmd_rebase(SimpleNamespace(dry=dry))
        except sh.tb.RateGuard:
            if not low_quota:
                raise
            logs.append('RATE_GUARD_PROPAGATED')
    return calls, logs


class Contract(unittest.TestCase):
    def test_actual_404_response_must_not_be_claimed_success(self):
        calls, logs = invoke(update=404)
        self.assertEqual([c[2] for c in calls if c[0] == 'PUT'],
                         [{'expected_head_sha': 'b' * 40}])
        self.assertFalse(any(c[0] == 'POST' for c in calls))
        self.assertFalse(any('CAS ok' in line for line in logs),
                         'Actual HTTP404 is returned by Gh.req, not an accepted update: ' + repr(logs))
        self.assertTrue(any('404' in line and ('FAILED' in line or 'not accepted' in line) for line in logs),
                        'Non-accepted update needs a visible failure diagnostic')

    def test_202_success_preserves_expected_head_and_success(self):
        calls, logs = invoke(update=202)
        self.assertEqual([c[2] for c in calls if c[0] == 'PUT'],
                         [{'expected_head_sha': 'b' * 40}])
        self.assertTrue(any('202' in line and 'CAS ok' in line for line in logs))
        self.assertFalse(any(c[0] == 'POST' for c in calls))

    def test_cas_mismatch_remains_no_comment_or_label(self):
        calls, logs = invoke(update=422)
        self.assertTrue(any('CAS mismatch' in line for line in logs))
        self.assertFalse(any(c[0] == 'POST' for c in calls))

    def test_real_conflict_stays_outside_update(self):
        calls, logs = invoke(mergeable=False)
        self.assertFalse(any(c[0] in ('PUT', 'POST') for c in calls))

    def test_draft_and_exempt_preserved(self):
        for kwargs in ({'draft': True}, {'labels': ('bot:conflict',)}):
            with self.subTest(kwargs=kwargs):
                calls, logs = invoke(**kwargs)
                self.assertFalse(any(c[0] in ('PUT', 'POST') for c in calls))
                self.assertFalse(any(c[1].endswith('/pulls/27') for c in calls))

    def test_dry_run_stays_readonly(self):
        calls, logs = invoke(dry=True)
        self.assertTrue(any('DRY would update' in line for line in logs))
        self.assertFalse(any(c[0] in ('PUT', 'POST') for c in calls))

    def test_rate_guard_remains_visible(self):
        calls, logs = invoke(low_quota=True)
        self.assertIn('RATE_GUARD_PROPAGATED', logs)
        self.assertFalse(any(c[0] == 'POST' for c in calls))

if __name__ == '__main__':
    unittest.main(verbosity=2)
