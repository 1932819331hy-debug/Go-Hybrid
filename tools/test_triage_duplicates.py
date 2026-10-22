"""Offline collect -> classify regressions; public patch provenance in fixtures.

The GitHub transport is mocked, not collection or classification. Tree IDs and
mergeability are synthetic controls; the #608/#786 per-file API objects are
stored unmodified public responses, not invented lookalike patches.
"""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import triage_bot as bot


FIXTURE = Path(__file__).with_name("fixtures") / "triage_979_public.json"
MAIN = "a" * 40
BASE = "b" * 40


def text_file(path="src/lib.rs", old="old", new="new", line=1):
    return {"filename": path, "status": "modified", "sha": "d" * 40,
            "additions": 1, "deletions": 1, "changes": 2,
            "patch": "@@ -%d +%d @@\n-%s\n+%s" % (line, line, old, new)}


def pull(number, files=None, base=BASE):
    files = copy.deepcopy(files if files is not None else [text_file()])
    return {"number": number, "title": "test contribution", "user": {"login": "contributor"},
            "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z",
            "head": {"sha": "%040x" % number}, "base": {"sha": MAIN},
            "merge_base": base, "draft": False, "mergeable": True, "mergeable_state": "clean",
            "changed_files": len(files), "additions": sum(f["additions"] for f in files),
            "deletions": sum(f["deletions"] for f in files), "files_data": files}


class FakeGitHub:
    repo = "example/project"

    def __init__(self, prs):
        self.prs = copy.deepcopy(prs)
        self.reads = {}

    def paginate(self, path, **kwargs):
        if "state=open" in path:
            return copy.deepcopy(self.prs)
        match = re.search(r"/pulls/(\d+)/files", path)
        if match:
            return copy.deepcopy(self.by_number(int(match[1]))["files_data"])
        raise AssertionError("unexpected paginate: " + path)

    def by_number(self, number):
        return next(p for p in self.prs if p["number"] == number)

    def get(self, path):
        if path.endswith("/commits/main"):
            return {"sha": MAIN}
        match = re.search(r"/pulls/(\d+)$", path)
        if match:
            n = int(match[1])
            self.reads[n] = self.reads.get(n, 0) + 1
            p = copy.deepcopy(self.by_number(n))
            if self.reads[n] > 1:
                p.update(p.get("after", {}))
            return p
        match = re.search(r"/compare/([0-9a-f]+)\.\.\.([0-9a-f]+)", path)
        if match:
            p = next(p for p in self.prs if p["head"]["sha"] == match[2])
            if p.get("no_compare"):
                return None
            return {"behind_by": 0, "base_commit": {"sha": match[1]},
                    "merge_base_commit": {"sha": p["merge_base"]}}
        match = re.search(r"/git/commits/([0-9a-f]+)$", path)
        if match:
            p = next(p for p in self.prs if p["head"]["sha"] == match[1])
            if p.get("no_tree"):
                return None
            tree = p.get("tree", hashlib.sha1(json.dumps(p["files_data"], sort_keys=True).encode()).hexdigest())
            return {"sha": match[1], "tree": {"sha": tree}}
        raise AssertionError("unexpected get: " + path)


class DuplicatePipelineTests(unittest.TestCase):
    def scan(self, prs):
        cfg = dict(bot.load_cfg(), wildcard_holds=[], drafts_parked=[])
        with patch.object(bot, "log"), contextlib.redirect_stdout(io.StringIO()):
            items, _ = bot.collect(cfg, FakeGitHub(prs), SimpleNamespace(jobs=1))
            items, clusters = bot.classify(cfg, items)
        return items, clusters

    def assert_unproven(self, items, clusters):
        self.assertEqual(clusters, [])
        for item in items:
            self.assertNotIn("dup_of", item)
            self.assertNotIn("dup_members", item)
            self.assertNotIn("triage:duplicate", item["labels"])
            self.assertNotEqual(item["bucket"], "DUP")
            self.assertEqual(item["duplicate_status"], "unproven")
            self.assertIn("duplicate status unproven", item["action"])

    def test_public_608_786_patches_do_not_make_a_duplicate_cluster(self):
        records = json.loads(FIXTURE.read_text())["sources"]
        prs = [pull(p["number"], p["files"], p["base"]["sha"]) for p in records]
        for pr, source in zip(prs, records):
            pr.update({key: source[key] for key in ("title", "user", "head", "base", "created_at", "updated_at")})
        items, clusters = self.scan(prs)
        # First assertion reproduces the original bug, independently of new fields.
        self.assertEqual(clusters, [])
        self.assert_unproven(items, clusters)
        self.assertEqual([i["score"] for i in items], [6, 6])
        self.assertTrue(all(i["duplicate_evidence"]["state"] == "complete" for i in items))

    def test_public_patches_differ_even_when_given_a_common_base(self):
        records = json.loads(FIXTURE.read_text())["sources"]
        self.assert_unproven(*self.scan([pull(p["number"], p["files"]) for p in records]))

    def test_public_patch_copy_on_same_base_remains_a_duplicate(self):
        files = json.loads(FIXTURE.read_text())["sources"][0]["files"]
        self.assertEqual(self.scan([pull(1, files), pull(2, files)])[1], [[1, 2]])

    def test_equal_complete_changes_on_same_base_remain_duplicates(self):
        items, clusters = self.scan([pull(1), pull(2)])
        self.assertEqual(clusters, [[1, 2]])
        self.assertEqual(next(i for i in items if i["number"] == 2)["dup_of"], 1)
        self.assertEqual(next(i for i in items if i["number"] == 2)["bucket"], "DUP")

    def test_different_changes_at_identical_coordinates_are_unproven(self):
        self.assert_unproven(*self.scan([pull(1), pull(2, [text_file(new="different")])]))

    def test_overlapping_context_is_not_duplicate_evidence(self):
        first = text_file()
        first["patch"] = "@@ -1,3 +1,3 @@\n-old\n+new\n shared\n other"
        second = text_file()
        second["patch"] = "@@ -1,3 +1,3 @@\n old\n shared\n-other\n+different"
        self.assert_unproven(*self.scan([pull(1, [first]), pull(2, [second])]))

    def test_all_files_beyond_twelve_participate(self):
        files = [text_file("src/%02d.rs" % n) for n in range(13)]
        other = copy.deepcopy(files)
        other[-1] = text_file("src/12.rs", new="unique work")
        items, clusters = self.scan([pull(1, files), pull(2, other)])
        self.assertEqual(items[0]["paths_sample"], items[1]["paths_sample"])
        self.assertEqual(items[0]["files"], 13)
        self.assert_unproven(items, clusters)

    def test_similar_intermediate_pr_does_not_bridge_distinct_changes(self):
        files = [text_file("src/%d.rs" % n) for n in range(3)]
        self.assert_unproven(*self.scan([pull(1, files[:2]), pull(2, files), pull(3, files[1:])]))

    def test_same_patch_different_merge_bases_remains_unproven(self):
        self.assert_unproven(*self.scan([pull(1), pull(2, base="c" * 40)]))

    def test_same_patch_and_base_but_different_tree_is_unproven(self):
        # Mode changes are not represented by REST's per-file patch text.
        self.assert_unproven(*self.scan([pull(1), dict(pull(2), tree="e" * 40)]))

    def test_missing_binary_and_truncated_patches_remain_unknown(self):
        good = text_file()
        variants = [None, "", "Binary files differ", "@@ -1 +1 @@\n-old",
                    "@@ -1 +1 @@\n-old\n+new\n@@ -9 +9 @@\n-nine",
                    "@@ malformed @@\n-old\n+new", "@@ -1 +1 @@\n-old\n+new\n+extra"]
        for raw in variants:
            with self.subTest(patch=raw):
                bad = dict(good, patch=raw)
                items, clusters = self.scan([pull(1, [bad]), pull(2, [bad])])
                self.assert_unproven(items, clusters)
                self.assertTrue(all(i["duplicate_evidence"]["state"] == "unknown" for i in items))

    def test_malformed_hunk_ranges_remain_unknown(self):
        patches = [
            ("@@ -0 +0 @@\n-old\n+new", 1),
            ("@@ -1 +1 @@\n-old\n+new\n@@ -1 +1 @@\n-old\n+new", 2),
            ("@@ -10 +10 @@\n-old\n+new\n@@ -1 +1 @@\n-old\n+new", 2),
            ("@@ -1 +1 @@\n-old\n+new\n@@ -10 +11 @@\n-old\n+new", 2),
            ("@@ -1 +1 @@junk\n-old\n+new", 1),
        ]
        for raw, count in patches:
            with self.subTest(patch=raw):
                file = dict(text_file(), patch=raw, additions=count, deletions=count, changes=count * 2)
                self.assert_unproven(*self.scan([pull(1, [file]), pull(2, [file])]))

    def test_misplaced_no_newline_markers_remain_unknown(self):
        patches = [
            "@@ -1 +1 @@\n\\ No newline at end of file\n-old\n+new",
            "@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file\n\\ No newline at end of file",
            "@@ -1,2 +1,2 @@\n-old\n\\ No newline at end of file\n+new\n context",
            "@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file\n@@ -10 +10 @@\n-old\n+new",
        ]
        for raw in patches:
            with self.subTest(patch=raw):
                count = raw.count("-old")
                file = dict(text_file(), patch=raw, additions=count, deletions=count, changes=count * 2)
                self.assert_unproven(*self.scan([pull(1, [file]), pull(2, [file])]))

    def test_file_status_must_agree_with_added_or_removed_content(self):
        files = [dict(text_file(), status="added"), dict(text_file(), status="removed"),
                 dict(text_file(), status="added", deletions=0, changes=1,
                      patch="@@ -2,0 +3 @@\n+new"),
                 dict(text_file(), status="removed", additions=0, changes=1,
                      patch="@@ -3 +2,0 @@\n-old")]
        for file in files:
            with self.subTest(file=file):
                self.assert_unproven(*self.scan([pull(1, [file]), pull(2, [file])]))

    def test_non_integer_file_and_pr_totals_remain_unknown(self):
        for value in [1.0, True]:
            for field in ["additions", "deletions", "changes"]:
                with self.subTest(value=value, field=field):
                    file = dict(text_file(), **{field: value if field != "changes" else 2.0})
                    self.assert_unproven(*self.scan([pull(1, [file]), pull(2, [file])]))
            for field in ["changed_files", "additions", "deletions"]:
                with self.subTest(value=value, field=field):
                    self.assert_unproven(*self.scan([dict(pull(1), **{field: value}),
                                                    dict(pull(2), **{field: value})]))

    def test_oversized_hunk_numbers_are_unknown_without_disabling_int_limits(self):
        for raw in ["@@ -1," + "9" * 5000 + " +1 @@\n-old\n+new",
                    "@@ -" + "9" * 5000 + " +1 @@\n-old\n+new"]:
            with self.subTest(length=len(raw)):
                file = dict(text_file(), patch=raw)
                self.assert_unproven(*self.scan([pull(1, [file]), pull(2, [file])]))

    def test_malformed_commit_tree_objects_remain_unknown(self):
        original = FakeGitHub.get
        for tree in [["unexpected"], "unexpected", 1, {"sha": []}]:
            def malformed_tree(gh, path):
                value = original(gh, path)
                if "/git/commits/" in path:
                    value["tree"] = tree
                return value
            with self.subTest(tree=tree), patch.object(FakeGitHub, "get", malformed_tree):
                self.assert_unproven(*self.scan([pull(1), pull(2)]))

    def test_valid_ordered_hunks_and_zero_count_insertion_remain_duplicates(self):
        for raw, adds, dels in [
                ("@@ -1 +1 @@\n-old\n+new\n@@ -10 +10 @@\n-before\n+after", 2, 2),
                ("@@ -0,0 +1 @@\n+new\n@@ -10 +11 @@\n-old\n+replacement", 2, 1),
                ("@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file", 1, 1)]:
            with self.subTest(patch=raw):
                file = dict(text_file(), patch=raw, additions=adds, deletions=dels, changes=adds + dels)
                self.assertEqual(self.scan([pull(1, [file]), pull(2, [file])])[1], [[1, 2]])

    def test_complete_hunk_but_missing_later_hunk_is_unknown(self):
        truncated = dict(text_file(), additions=2, deletions=2, changes=4)
        self.assert_unproven(*self.scan([pull(1, [truncated]), pull(2, [truncated])]))

    def test_file_count_totals_and_missing_late_file_are_unknown(self):
        for changes in [{"changed_files": 2}, {"additions": 2}, {"deletions": 2}]:
            with self.subTest(changes=changes):
                self.assert_unproven(*self.scan([dict(pull(1), **changes), dict(pull(2), **changes)]))

    def test_empty_and_repeated_file_lists_are_unknown(self):
        for files in [[], [text_file(), text_file()]]:
            with self.subTest(files=files):
                self.assert_unproven(*self.scan([pull(1, files), pull(2, files)]))

    def test_unsupported_file_status_is_unknown(self):
        for status in ["renamed", "copied", "changed", None]:
            with self.subTest(status=status):
                files = [dict(text_file(), status=status)]
                self.assert_unproven(*self.scan([pull(1, files), pull(2, files)]))

    def test_missing_merge_base_or_tree_remains_unknown(self):
        for changes in [{"no_compare": True}, {"no_tree": True}, {"merge_base": None}]:
            with self.subTest(changes=changes):
                self.assert_unproven(*self.scan([dict(pull(1), **changes), dict(pull(2), **changes)]))

    def test_snapshot_head_base_or_totals_change_remains_unknown(self):
        for after in [{"head": {"sha": "f" * 40}}, {"base": {"sha": "f" * 40}},
                      {"changed_files": 2}, {"additions": 2}]:
            with self.subTest(after=after):
                self.assert_unproven(*self.scan([dict(pull(1), after=after), dict(pull(2), after=after)]))

    def test_lookup_failure_remains_unknown_but_rate_guard_propagates(self):
        original = FakeGitHub.get
        for error in [RuntimeError("unavailable"), OSError("timeout"), ValueError("invalid JSON")]:
            def fail_tree(gh, path):
                if "/git/commits/" in path:
                    raise error
                return original(gh, path)
            with self.subTest(error=error), patch.object(FakeGitHub, "get", fail_tree):
                self.assert_unproven(*self.scan([pull(1), pull(2)]))
        def rate_guard(gh, path):
            if "/git/commits/" in path:
                raise bot.RateGuard("low quota")
            return original(gh, path)
        with patch.object(FakeGitHub, "get", rate_guard), self.assertRaises(bot.RateGuard):
            self.scan([pull(1), pull(2)])

    def test_missing_refs_or_list_head_mismatch_is_unknown(self):
        original = FakeGitHub.get
        for changes in [{"head": {}}, {"base": {}}, {"head": {"sha": "f" * 40}}]:
            def changed_full(gh, path):
                value = original(gh, path)
                if re.search(r"/pulls/\d+$", path):
                    value.update(changes)
                return value
            with self.subTest(changes=changes), patch.object(FakeGitHub, "get", changed_full):
                self.assert_unproven(*self.scan([pull(1), pull(2)]))

    def test_compare_or_commit_identity_mismatch_is_unknown(self):
        original = FakeGitHub.get
        for target in ["/compare/", "/git/commits/"]:
            def mismatched(gh, path):
                value = original(gh, path)
                if target in path:
                    value.update({"sha": "f" * 40, "base_commit": {"sha": "f" * 40}})
                return value
            with self.subTest(target=target), patch.object(FakeGitHub, "get", mismatched):
                self.assert_unproven(*self.scan([pull(1), pull(2)]))

    def test_partial_evidence_never_inherits_the_known_patch(self):
        unknown = dict(text_file(), patch=None)
        self.assert_unproven(*self.scan([pull(1), pull(2, [unknown])]))

    def test_file_order_does_not_change_complete_evidence(self):
        files = [text_file("src/a.rs"), text_file("src/b.rs")]
        # Identical trees are independent of the files endpoint's list order.
        prs = [dict(pull(1, files), tree="e" * 40), dict(pull(2, files[::-1]), tree="e" * 40)]
        self.assertEqual(self.scan(prs)[1], [[1, 2]])

    def test_non_main_base_uses_its_own_comparison(self):
        prs = [pull(1), pull(2)]
        for pr in prs:
            pr["base"]["sha"] = "c" * 40
        self.assertEqual(self.scan(prs)[1], [[1, 2]])

    def test_addition_deletion_and_no_newline_exact_duplicates(self):
        files = [dict(text_file("new.rs"), status="added", additions=1, deletions=0,
                      changes=1, patch="@@ -0,0 +1 @@\n+new\n\\ No newline at end of file"),
                 dict(text_file("gone.rs"), status="removed", additions=0, deletions=1,
                      changes=1, patch="@@ -1 +0,0 @@\n-old\n\\ No newline at end of file")]
        self.assertEqual(self.scan([pull(1, files), pull(2, files)])[1], [[1, 2]])

    def test_reclassification_clears_stale_duplicate_fields(self):
        items, _ = self.scan([pull(1), pull(2)])
        for item in items:
            item.pop("duplicate_evidence")
        cfg = dict(bot.load_cfg(), wildcard_holds=[], drafts_parked=[])
        items, clusters = bot.classify(cfg, items)
        self.assert_unproven(items, clusters)
        self.assertEqual([i["score"] for i in items], [6, 6])

    def test_reclassification_does_not_repeat_members(self):
        items, clusters = self.scan([pull(1), pull(2), pull(3)])
        items, clusters = bot.classify(bot.load_cfg(), items)
        self.assertEqual(clusters, [[1, 2, 3]])
        self.assertEqual(next(i for i in items if i["number"] == 1)["dup_members"], [2, 3])

    def test_holds_conflicts_and_stale_base_keep_precedence(self):
        for changes, bucket in [({"draft": True}, "PARKED-DRAFT"),
                                ({"mergeable": False}, "CONFLICTS")]:
            with self.subTest(bucket=bucket):
                items, clusters = self.scan([dict(pull(1), **changes), dict(pull(2), **changes)])
                self.assertEqual(clusters, [[1, 2]])
                self.assertTrue(all(i["bucket"] == bucket for i in items))
        items, _ = self.scan([pull(1), pull(2)])
        for item in items:
            item["behind_by"] = 100
        items, _ = bot.classify(dict(bot.load_cfg(), wildcard_holds=[1]), items)
        self.assertEqual({i["number"]: i["bucket"] for i in items}, {1: "PARKED-WILDCARD", 2: "STALE-BASE"})


if __name__ == "__main__":
    unittest.main()
