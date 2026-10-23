"""Offline archive download integrity tests; no GitHub requests or real tokens."""
import contextlib
from email.message import Message
from http.server import BaseHTTPRequestHandler, HTTPServer
import http.client
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import urllib.response
import zipfile


spec = importlib.util.spec_from_file_location("download_job", Path(__file__).with_name("job.py"))
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)


class OfflineRedirectTransport(job.urllib.request.BaseHandler):
    """Exercise urllib's real redirect chain without opening a socket."""

    handler_order = 100

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def https_open(self, request):
        self.requests.append(request)
        code, location, body = self.routes[request.full_url]
        headers = Message()
        if location is not None:
            headers["Location"] = location
        response = urllib.response.addinfourl(
            io.BytesIO(body), headers, request.full_url, code)
        response.msg = "OK" if code == 200 else "Redirect"
        return response


class RedirectAuthorizationTests(unittest.TestCase):
    """bazingagdy-star PR #647 + ocakciomursel-pixel PR #706: the GitHub
    token authenticates the initial API request only; urllib forwards
    headers added with add_header across redirects, so the Authorization
    header must be added unredirected and the redirect hops must be
    observed carrying no credential at all."""

    TOKEN = "offline-fixture-not-a-real-token"
    API_URL = "https://api.github.com/repos/fixture/repo/actions/artifacts/1/zip"
    DOWNLOAD_URL = "https://download.example.invalid/archive?signature=fixture"
    MIRROR_URL = "https://mirror.example.invalid/archive?signature=fixture"

    def opener(self, routes):
        transport = OfflineRedirectTransport(routes)
        opener = job.urllib.request.OpenerDirector()
        for handler in (transport, job.urllib.request.HTTPRedirectHandler(),
                        job.urllib.request.HTTPErrorProcessor(),
                        job.urllib.request.HTTPDefaultErrorHandler()):
            opener.add_handler(handler)
        return transport, opener

    def assert_token_stays_on_initial_request(self, requests):
        self.assertEqual(requests[0].get_header("Authorization"),
                         "token " + self.TOKEN)
        for request in requests[1:]:
            self.assertIsNone(request.get_header("Authorization"), request.full_url)

    def test_artifact_redirect_chain_does_not_forward_github_token(self):
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                transport, opener = self.opener({
                    self.API_URL: (code, self.DOWNLOAD_URL, b""),
                    self.DOWNLOAD_URL: (302, self.MIRROR_URL, b""),
                    self.MIRROR_URL: (200, None, b"archive fixture"),
                })
                artifact = {"name": "results", "archive_download_url": self.API_URL,
                            "size_in_bytes": len(b"archive fixture")}
                with mock.patch.object(job, "gh_token", return_value=self.TOKEN), \
                     mock.patch.object(job, "gh_api", return_value={"artifacts": [artifact]}), \
                     mock.patch.object(job.urllib.request, "urlopen", side_effect=opener.open), \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(job.actions_fetch(123, directory), 0)
                self.assertEqual((Path(directory) / "result/results.zip").read_bytes(),
                                 b"archive fixture")
                self.assertEqual(len(transport.requests), 3)
                self.assert_token_stays_on_initial_request(transport.requests)

    def test_api_redirect_does_not_forward_github_token(self):
        transport, opener = self.opener({
            self.API_URL: (302, self.DOWNLOAD_URL, b""),
            self.DOWNLOAD_URL: (200, None, b'{"ok": true}'),
        })
        with mock.patch.object(job, "gh_token", return_value=self.TOKEN), \
             mock.patch.object(job.urllib.request, "urlopen", side_effect=opener.open):
            self.assertEqual(job.gh_api(self.API_URL.removeprefix(job.GITHUB_API)),
                             {"ok": True})
        self.assertEqual(len(transport.requests), 2)
        self.assert_token_stays_on_initial_request(transport.requests)

    def test_direct_api_post_preserves_authentication_and_json(self):
        transport, opener = self.opener({self.API_URL: (200, None, b'{"ok": true}')})
        with mock.patch.object(job, "gh_token", return_value=self.TOKEN), \
             mock.patch.object(job.urllib.request, "urlopen", side_effect=opener.open):
            self.assertEqual(job.gh_api(self.API_URL.removeprefix(job.GITHUB_API),
                                       {"fixture": 1}, method="POST"), {"ok": True})
        self.assertEqual(len(transport.requests), 1)
        request = transport.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"fixture": 1})
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assert_token_stays_on_initial_request(transport.requests)


class DownloadRedirectTests(unittest.TestCase):
    """PR #647's loopback variant: real cross-host redirects over 127.0.0.1."""

    def fetch_archive(self, redirects):
        observed = []
        routes = {}
        archive = b"complete fixture archive"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                observed.append((self.path, self.headers.get("Authorization")))
                target = routes.get(self.path)
                self.send_response(302 if target else 200)
                if target:
                    self.send_header("Location", target)
                self.end_headers()
                if not target:
                    self.wfile.write(archive)

            def log_message(self, *args):
                pass

        # HTTPServer names itself via reverse DNS, which is unrelated to this
        # loopback fixture and can block before the request timeout starts.
        with mock.patch("http.server.socket.getfqdn", return_value="localhost"):
            servers = [HTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
        origin = "http://127.0.0.1:{}".format(servers[0].server_port)
        storage = "http://127.0.0.1:{}".format(servers[1].server_port)
        if redirects:
            routes["/archive"] = storage + "/blob"
        if redirects == 2:
            routes["/archive"] = origin + "/intermediate"
            routes["/intermediate"] = storage + "/blob"
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
        for thread in threads:
            thread.start()
        try:
            # Exercise urllib's real redirect handling, without environment
            # proxies, live GitHub requests, or reading the user's credentials.
            opener = job.urllib.request.build_opener(job.urllib.request.ProxyHandler({}))
            artifact = {"name": "results", "archive_download_url": origin + "/archive",
                        "size_in_bytes": len(archive)}
            with tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(job, "gh_api", return_value={"artifacts": [artifact]}), \
                 mock.patch.object(job, "gh_token", return_value="offline-fixture"), \
                 mock.patch.object(job.urllib.request, "urlopen",
                                   side_effect=lambda req, timeout: opener.open(req, timeout=3)), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(job.actions_fetch(123, tmp), 0)
                self.assertEqual((Path(tmp) / "result/results.zip").read_bytes(), archive)
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=3)
        return observed

    def test_direct_download_keeps_initial_authentication(self):
        self.assertEqual(self.fetch_archive(0), [("/archive", "token offline-fixture")])

    def test_cross_origin_redirect_does_not_forward_authentication(self):
        self.assertEqual(self.fetch_archive(1),
                         [("/archive", "token offline-fixture"), ("/blob", None)])

    def test_redirect_chain_does_not_restore_authentication(self):
        self.assertEqual(self.fetch_archive(2),
                         [("/archive", "token offline-fixture"),
                          ("/intermediate", None), ("/blob", None)])


class DownloadIntegrityTests(unittest.TestCase):
    def fetch(self, directory, response):
        artifact = {"name": "results", "archive_download_url": "https://example.invalid/archive",
                    "size_in_bytes": 9}
        with mock.patch.object(job, "gh_api", return_value={"artifacts": [artifact]}), \
             mock.patch.object(job, "gh_token", return_value="offline-fixture"), \
             mock.patch.object(job.urllib.request, "urlopen", return_value=response), \
             contextlib.redirect_stdout(io.StringIO()):
            job.actions_fetch(123, str(directory))

    def test_interrupted_read_preserves_existing_archive(self):
        for existing in (None, b"previous validated archive"):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = root / "result"
                result.mkdir()
                dest = result / "results.zip"
                if existing is not None:
                    dest.write_bytes(existing)
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.read.side_effect = http.client.IncompleteRead(b"partial", 100)
                with self.assertRaises(http.client.IncompleteRead):
                    self.fetch(root, response)
                self.assertEqual(dest.read_bytes() if dest.exists() else None, existing)
                self.assertEqual(sorted(p.name for p in result.iterdir()),
                                 [] if existing is None else ["results.zip"])

    def test_success_replaces_archive_only_after_response_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "result"
            result.mkdir()
            dest = result / "results.zip"
            dest.write_bytes(b"old")
            observed = []

            class Response(io.BytesIO):
                def read(self, *args):
                    observed.append(dest.read_bytes())
                    return super().read(*args)

            self.fetch(root, Response(b"new complete archive"))
            self.assertTrue(observed)
            self.assertTrue(all(data == b"old" for data in observed))
            self.assertEqual(dest.read_bytes(), b"new complete archive")
            self.assertEqual([p.name for p in result.iterdir()], ["results.zip"])

    def test_publish_failure_keeps_previous_archive_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "result"
            result.mkdir()
            dest = result / "results.zip"
            dest.write_bytes(b"old")
            with mock.patch.object(job.os, "replace", side_effect=OSError("fixture publish failure")):
                with self.assertRaisesRegex(OSError, "fixture publish failure"):
                    self.fetch(root, io.BytesIO(b"new"))
            self.assertEqual(dest.read_bytes(), b"old")
            self.assertEqual([p.name for p in result.iterdir()], ["results.zip"])

    def test_later_shard_download_failure_preserves_all_previous_archives(self):
        for preexisting in (False, True):
            with self.subTest(preexisting=preexisting), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = root / "result"
                result.mkdir()
                first = result / "first.zip"
                second = result / "second.zip"
                if preexisting:
                    first.write_bytes(b"reviewed first")
                    second.write_bytes(b"reviewed second")
                artifacts = [
                    {"name": name, "archive_download_url": "https://example.invalid/" + name,
                     "size_in_bytes": 9}
                    for name in ("first", "second")
                ]
                with mock.patch.object(job, "gh_api",
                                       return_value={"artifacts": artifacts}), \
                     mock.patch.object(job, "gh_token", return_value="fixture"), \
                     mock.patch.object(job.urllib.request, "urlopen",
                                       side_effect=[io.BytesIO(b"new first"),
                                                    http.client.IncompleteRead(b"partial", 10)]), \
                     contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(http.client.IncompleteRead):
                        job.actions_fetch(123, str(root))
                self.assertEqual(first.read_bytes() if first.exists() else None,
                                 b"reviewed first" if preexisting else None)
                self.assertEqual(second.read_bytes() if second.exists() else None,
                                 b"reviewed second" if preexisting else None)
                self.assertEqual(sorted(p.name for p in result.iterdir()),
                                 ["first.zip", "second.zip"] if preexisting else [])

    def test_all_shards_publish_after_complete_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = [
                {"name": name, "archive_download_url": "https://example.invalid/" + name,
                 "size_in_bytes": 1}
                for name in ("first", "second")
            ]
            with mock.patch.object(job, "gh_api",
                                   return_value={"artifacts": artifacts}), \
                 mock.patch.object(job, "gh_token", return_value="fixture"), \
                 mock.patch.object(job.urllib.request, "urlopen",
                                   side_effect=[io.BytesIO(b"A"), io.BytesIO(b"B")]), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(job.actions_fetch(123, tmp), 0)
            self.assertEqual((Path(tmp) / "result/first.zip").read_bytes(), b"A")
            self.assertEqual((Path(tmp) / "result/second.zip").read_bytes(), b"B")

    def test_later_staging_write_failure_preserves_all_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "result"
            result.mkdir()
            for name in ("first", "second"):
                (result / (name + ".zip")).write_bytes(b"reviewed " + name.encode())
            artifacts = [
                {"name": name, "archive_download_url": "https://example.invalid/" + name,
                 "size_in_bytes": 1}
                for name in ("first", "second")
            ]
            real_open = open

            def fail_second_staged_write(path, *args, **kwargs):
                if Path(path).name == "1.zip":
                    raise OSError("fixture staging disk full")
                return real_open(path, *args, **kwargs)

            with mock.patch.object(job, "gh_api", return_value={"artifacts": artifacts}), \
                 mock.patch.object(job, "gh_token", return_value="fixture"), \
                 mock.patch.object(job.urllib.request, "urlopen",
                                   side_effect=[io.BytesIO(b"A"), io.BytesIO(b"B")]), \
                 mock.patch("builtins.open", side_effect=fail_second_staged_write), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(OSError, "staging disk full"):
                    job.actions_fetch(123, tmp)
            for name in ("first", "second"):
                self.assertEqual((result / (name + ".zip")).read_bytes(),
                                 b"reviewed " + name.encode())
            self.assertEqual(sorted(p.name for p in result.iterdir()),
                             ["first.zip", "second.zip"])

    def test_interrupt_cleans_staging_and_preserves_reviewed_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "result"
            result.mkdir()
            dest = result / "results.zip"
            dest.write_bytes(b"reviewed")
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.read.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                self.fetch(Path(tmp), response)
            self.assertEqual(dest.read_bytes(), b"reviewed")
            self.assertEqual([p.name for p in result.iterdir()], ["results.zip"])

    def test_later_publication_failure_is_not_a_batch_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "result"
            result.mkdir()
            artifacts = [
                {"name": name, "archive_download_url": "https://example.invalid/" + name,
                 "size_in_bytes": 1}
                for name in ("first", "second")
            ]
            for name in ("first", "second"):
                (result / (name + ".zip")).write_bytes(b"reviewed")
            real_replace = job.os.replace

            def fail_second_publication(source, dest):
                if Path(dest).name == "second.zip":
                    raise OSError("fixture publish failure")
                return real_replace(source, dest)

            with mock.patch.object(job, "gh_api", return_value={"artifacts": artifacts}), \
                 mock.patch.object(job, "gh_token", return_value="fixture"), \
                 mock.patch.object(job.urllib.request, "urlopen",
                                   side_effect=[io.BytesIO(b"A"), io.BytesIO(b"B")]), \
                 mock.patch.object(job.os, "replace", side_effect=fail_second_publication), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(OSError, "fixture publish failure"):
                    job.actions_fetch(123, tmp)
            self.assertEqual((result / "first.zip").read_bytes(), b"A")
            self.assertEqual((result / "second.zip").read_bytes(), b"reviewed")
            self.assertEqual(sorted(p.name for p in result.iterdir()),
                             ["first.zip", "second.zip"])

    def test_zip_members_are_stored_without_extraction(self):
        body = io.BytesIO()
        with zipfile.ZipFile(body, "w") as archive:
            archive.writestr("../outside.txt", "dummy archive member")
        with tempfile.TemporaryDirectory() as tmp:
            self.fetch(Path(tmp), io.BytesIO(body.getvalue()))
            self.assertEqual((Path(tmp) / "result/results.zip").read_bytes(), body.getvalue())
            self.assertFalse((Path(tmp) / "outside.txt").exists())
            self.assertEqual([p.name for p in (Path(tmp) / "result").iterdir()], ["results.zip"])

    def test_publication_replaces_leaf_symlink_without_following_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "result"
            result.mkdir()
            reviewed = root / "reviewed.zip"
            reviewed.write_bytes(b"reviewed dummy")
            dest = result / "results.zip"
            dest.symlink_to(reviewed)
            self.fetch(root, io.BytesIO(b"new archive"))
            self.assertEqual(reviewed.read_bytes(), b"reviewed dummy")
            self.assertFalse(dest.is_symlink())
            self.assertEqual(dest.read_bytes(), b"new archive")

    def test_success_creates_new_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fetch(root, io.BytesIO(b"complete archive"))
            self.assertEqual((root / "result/results.zip").read_bytes(), b"complete archive")
            self.assertEqual([p.name for p in (root / "result").iterdir()], ["results.zip"])


if __name__ == "__main__":
    unittest.main()
