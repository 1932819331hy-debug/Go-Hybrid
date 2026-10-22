"""Offline worker preparation tests using real Git and subprocess boundaries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "worker" / "script.py"
GIT = shutil.which("git")
TAR = shutil.which("tar")
RUNNER = '''import argparse, json, os
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("job")
p.add_argument("--results-dir")
p.add_argument("--repo-dir")
a = p.parse_args()
record = {"job": json.loads(Path(a.job).read_text()),
          "revision": (Path(a.repo_dir) / "revision.txt").read_text(),
          "results_dir": a.results_dir}
Path(os.environ["RUNNER_MARKER"]).write_text(json.dumps(record))
raise SystemExit(int(os.environ.get("RUNNER_EXIT", "0")))
'''


@unittest.skipUnless(GIT and TAR, "requires Git and tar")
class WorkerPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="worker-preparation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.work = self.root / "work"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.marker = self.root / "runner.json"
        self.trace = self.root / "tools.jsonl"
        self.archive = self.root / "fallback.tar.gz"
        self.env = dict(os.environ, HOME=str(self.root), GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL="file",
                        GIT_TERMINAL_PROMPT="0", RUNNER_MARKER=str(self.marker),
                        TOOL_TRACE=str(self.trace), ARCHIVE=str(self.archive),
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        self.source.mkdir()
        self.git("init", "--quiet")
        runner = self.source / "tools" / "runner" / "runner.py"
        runner.parent.mkdir(parents=True)
        runner.write_text(RUNNER, encoding="utf-8")
        (self.source / "revision.txt").write_text("pinned", encoding="utf-8")
        self.git("add", ".")
        self.git("-c", "user.name=Worker Test", "-c", "user.email=worker@example.invalid",
                 "commit", "--quiet", "-m", "pinned revision")
        self.sha = self.git("rev-parse", "HEAD").strip()
        (self.source / "revision.txt").write_text("head", encoding="utf-8")
        self.git("-c", "user.name=Worker Test", "-c", "user.email=worker@example.invalid",
                 "commit", "--quiet", "-am", "newer revision")
        payload = self.root / "archive-payload"
        (payload / "tools" / "runner").mkdir(parents=True)
        (payload / "tools" / "runner" / "runner.py").write_text(RUNNER, encoding="utf-8")
        (payload / "revision.txt").write_text("archive", encoding="utf-8")
        with tarfile.open(self.archive, "w:gz") as archive:
            archive.add(payload, arcname="snapshot")

        # Only informational environment probes are replaced. Git is real;
        # curl and tar remain subprocesses with independently controlled exits.
        for name in ("nproc", "free", "df", "go", "rustc"):
            self.stub(name, "raise SystemExit(0)\n")
        self.stub("curl", '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["TOOL_TRACE"], "a") as f:
    f.write(json.dumps({"tool": "curl", "args": args}) + "\\n")
data = Path(os.environ["ARCHIVE"]).read_bytes()
if "-o" in args:
    Path(args[args.index("-o") + 1]).write_bytes(data)
elif "--output" in args:
    Path(args[args.index("--output") + 1]).write_bytes(data)
else:
    sys.stdout.buffer.write(data)
rc = int(os.environ.get("CURL_EXIT", "0"))
if os.environ.get("HTTP_ERROR") and any(a == "--fail" or
        (a.startswith("-") and not a.startswith("--") and "f" in a) for a in args):
    rc = 22
raise SystemExit(rc)
''')
        self.stub("tar", '''import json, os, subprocess, sys
with open(os.environ["TOOL_TRACE"], "a") as f:
    f.write(json.dumps({"tool": "tar", "args": sys.argv[1:]}) + "\\n")
rc = subprocess.run([''' + repr(TAR) + '''] + sys.argv[1:]).returncode
# A failing extractor can leave a complete-looking runner behind.
raise SystemExit(int(os.environ.get("TAR_EXIT", str(rc))))
''')

    def stub(self, name, source):
        path = self.bin / name
        path.write_text("#!" + sys.executable + "\n" + source, encoding="utf-8")
        path.chmod(0o755)

    def git(self, *args):
        return subprocess.run([GIT, *args], cwd=self.source, env=self.env,
                              check=True, capture_output=True, text=True,
                              timeout=10).stdout

    def run_worker(self, repo=None, sha=None, **env):
        job = {"repo": str(self.source) if repo is None else str(repo),
               "sha": self.sha if sha is None else sha, "job": "offline-preparation"}
        launch = '''import importlib.util, sys
spec = importlib.util.spec_from_file_location("worker", sys.argv[1])
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
worker.WORK = sys.argv[2]
worker.JOB_JSON = sys.argv[3]
worker.main()
'''
        return subprocess.run([sys.executable, "-c", launch, str(WORKER),
                               str(self.work), json.dumps(job)], cwd=self.root,
                              env=dict(self.env, **env), capture_output=True,
                              text=True, timeout=20)

    def calls(self):
        if not self.trace.exists():
            return []
        return [json.loads(line) for line in self.trace.read_text().splitlines()]

    def assert_runner(self, result, revision, status=0):
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        self.assertTrue(self.marker.exists(), result.stdout + result.stderr)
        record = json.loads(self.marker.read_text())
        self.assertEqual(record["revision"], revision)
        self.assertEqual(record["job"]["job"], "offline-preparation")
        self.assertEqual(record["results_dir"], str(self.work / "results"))

    def test_git_checkout_runs_pinned_revision_without_download(self):
        result = self.run_worker()
        self.assert_runner(result, "pinned")
        self.assertEqual(self.calls(), [])

    def test_git_success_preserves_runner_exit(self):
        result = self.run_worker(RUNNER_EXIT="37")
        self.assert_runner(result, "pinned", 37)
        self.assertEqual(self.calls(), [])

    def test_clone_failure_uses_archive(self):
        result = self.run_worker(repo=self.root / "missing")
        self.assert_runner(result, "archive")
        self.assertEqual([c["tool"] for c in self.calls()], ["curl", "tar"])
        self.assertIn("does not exist", result.stdout)

    def test_checkout_failure_replaces_checkout_with_archive(self):
        result = self.run_worker(sha="missing-ref")
        self.assert_runner(result, "archive")
        self.assertFalse((self.work / "repo" / ".git").exists())
        self.assertIn("missing-ref", result.stdout)
        self.assertEqual([c["tool"] for c in self.calls()], ["curl", "tar"])

    def test_option_like_ref_cannot_skip_checkout(self):
        result = self.run_worker(sha="--help")
        self.assert_runner(result, "archive")
        self.assertEqual([c["tool"] for c in self.calls()], ["curl", "tar"])

    def test_tracked_filename_is_not_a_successful_revision_checkout(self):
        result = self.run_worker(sha="revision.txt")
        self.assert_runner(result, "archive")
        self.assertEqual([c["tool"] for c in self.calls()], ["curl", "tar"])

    def test_failed_download_with_valid_archive_never_runs(self):
        result = self.run_worker(repo=self.root / "missing", CURL_EXIT="22")
        self.assertEqual(result.returncode, 22, result.stdout + result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual([c["tool"] for c in self.calls()], ["curl"])

    def test_failed_checkout_then_download_cannot_run_stale_checkout(self):
        result = self.run_worker(sha="missing-ref", CURL_EXIT="7")
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual([c["tool"] for c in self.calls()], ["curl"])

    def test_http_error_is_a_failed_download(self):
        result = self.run_worker(repo=self.root / "missing", HTTP_ERROR="1")
        self.assertEqual(result.returncode, 22, result.stdout + result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertEqual([c["tool"] for c in self.calls()], ["curl"])

    def test_failed_extraction_cannot_run_extracted_runner(self):
        result = self.run_worker(repo=self.root / "missing", TAR_EXIT="9")
        self.assertEqual(result.returncode, 9, result.stdout + result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertTrue((self.work / "repo" / "tools" / "runner" / "runner.py").exists())
        self.assertEqual([c["tool"] for c in self.calls()], ["curl", "tar"])

    def test_fallback_preserves_runner_exit(self):
        result = self.run_worker(repo=self.root / "missing", RUNNER_EXIT="29")
        self.assert_runner(result, "archive", 29)

    def test_git_paths_and_ref_with_spaces_or_quotes_are_literal(self):
        ref = "release'candidate"
        self.git("tag", ref, self.sha)
        renamed = self.root / "source owner's copy"
        self.source.rename(renamed)
        self.source = renamed
        self.work = self.root / "working owner's directory"
        result = self.run_worker(sha=ref)
        self.assert_runner(result, "pinned")
        self.assertEqual(self.calls(), [])

    def test_archive_url_and_runner_paths_are_quoted(self):
        self.work = self.root / "working owner's directory"
        ref = "release'candidate"
        result = self.run_worker(repo=self.root / "missing", sha=ref)
        self.assert_runner(result, "archive")
        curl = self.calls()[0]
        url = "https://codeload.github.com/limitlessprojects/Go-Hybrid/tar.gz/" + ref
        self.assertIn(url, curl["args"])


if __name__ == "__main__":
    unittest.main()
