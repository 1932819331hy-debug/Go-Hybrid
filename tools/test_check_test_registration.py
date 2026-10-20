"""Regression tests for the test-registration guard (offline, stdlib only).

Each case builds a throwaway repo tree plus a throwaway workflow, so the
guard is exercised against fixtures rather than the live repository. The
guard covers two invocation styles, and both are pinned here:

  tools/**/test_*.py  reached through `unittest discover -s DIR [-p PATTERN]`
  tests/**/test_*.py  reached by literal repo-relative path
"""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "check_test_registration", Path(__file__).with_name("check_test_registration.py"))
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class PatternExtractionTests(unittest.TestCase):
    def test_quoted_and_bare_patterns_are_both_read(self):
        workflow = (
            "          python3 -m unittest discover -s tools/fuzz -p 'test_hyfuzz.py'\n"
            "          python3 -m unittest discover -s tools -p test_managed_gate.py -v\n"
        )
        self.assertEqual(guard.registered_patterns(workflow),
                         ["test_hyfuzz.py", "test_managed_gate.py"])

    def test_default_pattern_matches_unittest(self):
        for options, start in (("", "."), ("-s tools", "tools"),
                               ("--start-directory=tools", "tools"),
                               ("-s tools -t tools", "tools"),
                               ("-s tools --top-level-directory tools", "tools")):
            with self.subTest(options=options):
                self.assertEqual(guard.registered_discoveries(
                    "python3 -m unittest discover " + options), [(start, "test*.py")])

    def test_explicit_pattern_overrides_default(self):
        for option in ("-p test_only.py", "--pattern test_only.py",
                       "--pattern=test_only.py"):
            with self.subTest(option=option):
                self.assertEqual(guard.registered_discoveries(
                    "python3 -m unittest discover -s tools " + option),
                    [("tools", "test_only.py")])

    def test_missing_option_values_do_not_fall_back_to_default(self):
        for options in ("-s tools -p", "-s tools --pattern", "-s", "--start-directory",
                        "-s tools -p -v", "-s tools -t --pattern=test_disabled.py",
                        "-s tools -k --pattern=test_disabled.py"):
            with self.subTest(options=options):
                self.assertEqual(guard.registered_discoveries(
                    "python3 -m unittest discover " + options), [])

    def test_option_values_are_consumed_once(self):
        # Even malformed values must not be reinterpreted as another option.
        self.assertEqual(guard.registered_discoveries(
            "python3 -m unittest discover -s tools -p --start-directory=elsewhere"), [])
        self.assertEqual(guard.registered_discoveries(
            "python3 -m unittest discover -s tools --pattern=--start-directory=elsewhere"),
            [("tools", "--start-directory=elsewhere")])

    def test_commented_and_echoed_default_discovery_is_not_registered(self):
        for prefix in ("# ", "echo "):
            with self.subTest(prefix=prefix):
                self.assertEqual(guard.registered_discoveries(
                    prefix + "python3 -m unittest discover -s tools"), [])

    def test_non_discovery_python_lines_are_ignored(self):
        workflow = (
            "          python3 -m py_compile tools/job.py tools/corpus_harness.py\n"
            "          python3 tests/test_install_checksums.py\n"
            "          python3 tools/runner/runner.py job.json\n"
        )
        self.assertEqual(guard.registered_patterns(workflow), [])

    def test_quoted_directories_and_long_options_keep_their_patterns(self):
        # from pr-540
        self.assertEqual(guard.registered_patterns(
            'python3 -m unittest discover -p "test_*.py" -s "tools/one"\n'
            'python3 -m unittest discover --start-directory=tools/two '
            '--pattern=test_x.py\n'),
            ["test_*.py", "test_x.py"])

    def test_direct_path_match_respects_word_boundaries(self):
        workflow = "          python3 tests/test_install_runtime.py\n"
        self.assertTrue(
            guard.is_registered_direct("tests/test_install_runtime.py", workflow))
        self.assertFalse(
            guard.is_registered_direct("tests/test_install_runtime_extra.py", workflow))

    # --- explicit-invocation parsing (from pr-600, fixes #596) ---

    def test_commented_discovery_is_not_a_registration(self):
        self.assertEqual(guard.registered_patterns(
            "  # python3 -m unittest discover -s tools -p test_disabled.py\n"), [])

    def test_echoed_discovery_is_not_a_registration(self):
        self.assertEqual(guard.registered_patterns(
            "  echo 'python3 -m unittest discover -s tools -p test_disabled.py'\n"), [])

    def test_direct_script_mentions_do_not_register_execution(self):
        for workflow in (
            "# python3 tests/test_disabled.py\n",
            "echo 'python3 tests/test_disabled.py'\n",
            "name: python3 tests/test_disabled.py\n",
            "python3 other.py tests/test_disabled.py\n",
            "python3 tests/test_live.py # tests/test_disabled.py\n",
        ):
            with self.subTest(workflow=workflow):
                self.assertFalse(guard.is_registered_direct("tests/test_disabled.py", workflow))

    def test_inline_run_and_quoted_patterns_are_registered(self):
        self.assertEqual(guard.registered_patterns(
            '  - run: python3 -m unittest discover -s tools -p "test_*.py" # run tests\n'),
            ["test_*.py"])
        self.assertTrue(guard.is_registered_direct(
            "tests/test_live.py", "  run: python3 tests/test_live.py\n"))

    def test_later_shell_command_does_not_supply_discovery_patterns(self):
        for separator in (" && ", "&&", ";", " || ", " | "):
            with self.subTest(separator=separator):
                self.assertEqual(guard.registered_patterns(
                    "python3 -m unittest discover -s tools -p test_live.py"
                    + separator + "echo -p test_disabled.py\n"), ["test_live.py"])

    def test_interpreter_flags_preserve_real_invocations(self):
        self.assertEqual(guard.registered_patterns(
            "python3 -B -W ignore -X dev -m unittest discover -s tools -p test_live.py\n"),
            ["test_live.py"])
        self.assertTrue(guard.is_registered_direct(
            "tests/test_live.py", "python3 -u tests/test_live.py\n"))
        self.assertFalse(guard.is_registered_direct(
            "tests/test_live.py", "python3 -c 'print(1)' tests/test_live.py\n"))

    def test_repeated_pattern_uses_the_last_value_like_unittest(self):
        self.assertEqual(guard.registered_patterns(
            "python3 -m unittest discover -s tools -p test_disabled.py -p test_live.py\n"),
            ["test_live.py"])


class RegistrationTests(unittest.TestCase):
    def build(self, workflow, suites):
        """Write a temp root with `suites` and return (root, workflow_text)."""
        temporary = tempfile.TemporaryDirectory(prefix="hybrid-registration-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for rel in suites:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# suite\n", encoding="utf-8")
        return root, workflow

    def test_glob_pattern_registers_a_whole_family(self):
        workflow = "          python3 -m unittest discover -s tools -p 'test_job*.py'\n"
        root, text = self.build(
            workflow,
            ["tools/test_job_cli.py", "tools/test_job_payload.py", "tools/test_job_artifacts.py"])
        self.assertEqual(guard.find_test_files(root, "tools"), [
            "tools/test_job_artifacts.py", "tools/test_job_cli.py", "tools/test_job_payload.py"])
        self.assertEqual(guard.unregistered(root, text), [])

    def test_default_discovery_registers_tests_that_unittest_runs(self):
        root, text = self.build("python3 -m unittest discover -s tools\n",
                                ["tools/test_real.py"])
        (root / "tools/test_real.py").write_text(
            "import unittest\nclass Real(unittest.TestCase):\n"
            "    def test_failure(self): self.fail('default discovery really ran')\n",
            encoding="utf-8")
        actual = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tools"],
                                cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(actual.returncode, 1, actual.stderr)
        self.assertIn("default discovery really ran", actual.stderr)
        self.assertEqual(guard.unregistered(root, text), [])

    def test_missing_pattern_is_a_real_unittest_error(self):
        root, _ = self.build("", ["tools/test_real.py"])
        for options in (("-p",), ("--pattern",), ("-p", "-v"),
                        ("-t", "--pattern=test_real.py"),
                        ("-k", "--pattern=test_real.py")):
            with self.subTest(options=options):
                command = [sys.executable, "-m", "unittest", "discover", "-s", "tools", *options]
                actual = subprocess.run(command, cwd=root, capture_output=True,
                                        text=True, timeout=15)
                self.assertEqual(actual.returncode, 2, actual.stderr)
                self.assertIn("expected one argument", actual.stderr)
                self.assertEqual(guard.registered_discoveries(
                    "python3 -m unittest discover -s tools " + " ".join(options)), [])

    def test_help_and_unknown_options_do_not_register_unexecuted_tests(self):
        root, _ = self.build("", ["tools/test_real.py"])
        (root / "tools/test_real.py").write_text(
            "raise AssertionError('unexecuted test sentinel')\n", encoding="utf-8")
        for options, expected_exit in ((("--help",), 0), (("-h",), 0),
                                       (("-v", "--help"), 0), (("--bogus",), 2),
                                       (("-p", "test_real.py", "--help"), 0),
                                       (("-p", "test_real.py", "--bogus"), 2)):
            with self.subTest(options=options):
                command = [sys.executable, "-m", "unittest", "discover", "-s", "tools", *options]
                actual = subprocess.run(command, cwd=root, capture_output=True,
                                        text=True, timeout=15)
                self.assertEqual(actual.returncode, expected_exit, actual.stderr)
                self.assertNotIn("unexecuted test sentinel", actual.stderr)
                workflow = "python3 -m unittest discover -s tools " + " ".join(options)
                self.assertEqual([rel for rel, _ in guard.unregistered(root, workflow)],
                                 ["tools/test_real.py"])

    def test_attached_short_options_match_real_discovery_and_last_value_wins(self):
        root, _ = self.build("", ["tools/test_only.py", "tools/test_other.py"])
        for name in ("only", "other"):
            (root / f"tools/test_{name}.py").write_text(
                "import unittest\nclass Real(unittest.TestCase):\n"
                f"    def test_failure(self): self.fail('{name} sentinel')\n",
                encoding="utf-8")
        for options in (("-s", "tools", "-ptest_only.py"),
                        ("-stools", "-p=test_only.py"),
                        ("-s=tools", "-ttools", "-ptest_only.py"),
                        ("-s", "tools", "-p", "test_other.py", "-ptest_only.py")):
            with self.subTest(options=options):
                command = [sys.executable, "-m", "unittest", "discover", *options]
                actual = subprocess.run(command, cwd=root, capture_output=True,
                                        text=True, timeout=15)
                self.assertEqual(actual.returncode, 1, actual.stderr)
                self.assertIn("only sentinel", actual.stderr)
                self.assertNotIn("other sentinel", actual.stderr)
                workflow = "python3 -m unittest discover " + " ".join(options)
                self.assertEqual([rel for rel, _ in guard.unregistered(root, workflow)],
                                 ["tools/test_other.py"])

    def test_default_pattern_keeps_start_directory_and_package_boundaries(self):
        root, text = self.build(
            "python3 -m unittest discover -s tools/suite\n",
            ["tools/test_outside.py", "tools/suite/test_one.py",
             "tools/suite/pkg/__init__.py", "tools/suite/pkg/test_two.py",
             "tools/suite/plain/test_hidden.py"])
        self.assertEqual([rel for rel, _ in guard.unregistered(root, text)],
                         ["tools/suite/plain/test_hidden.py", "tools/test_outside.py"])

    def test_unregistered_suite_is_detected(self):
        workflow = "          python3 -m unittest discover -s tools -p test_job_cli.py -v\n"
        root, text = self.build(workflow, ["tools/test_job_cli.py", "tools/test_job_payload.py"])
        missing = guard.unregistered(root, text)
        self.assertEqual([rel for rel, _ in missing], ["tools/test_job_payload.py"])

    def test_nested_suite_is_found_and_checked(self):
        workflow = "          python3 -m unittest discover -s tools -p test_none.py\n"
        root, text = self.build(
            workflow, ["tools/fuzz/test_hyfuzz.py", "tools/runner/test_runner.py"])
        self.assertEqual(guard.find_test_files(root, "tools"),
                         ["tools/fuzz/test_hyfuzz.py", "tools/runner/test_runner.py"])
        self.assertEqual(sorted(rel for rel, _ in guard.unregistered(root, text)),
                         ["tools/fuzz/test_hyfuzz.py", "tools/runner/test_runner.py"])

    def test_direct_script_is_registered_only_when_its_path_is_named(self):
        root, text = self.build(
            "          python3 tests/test_install_runtime.py\n",
            ["tests/test_install_runtime.py"])
        self.assertEqual(guard.unregistered(root, text), [])

    def test_uninvoked_direct_script_is_detected(self):
        root, text = self.build(
            "          python3 -m unittest discover -s tools -p test_x.py\n",
            ["tests/test_install_checksums.py"])
        missing = guard.unregistered(root, text)
        self.assertEqual([rel for rel, _ in missing], ["tests/test_install_checksums.py"])

    def test_both_halves_are_checked_together(self):
        workflow = "          python3 -m unittest discover -s tools -p test_x.py\n"
        root, text = self.build(
            workflow, ["tools/test_x.py", "tests/test_install_upgrade.py"])
        missing = guard.unregistered(root, text)
        self.assertEqual([rel for rel, _ in missing], ["tests/test_install_upgrade.py"])

    # --- discovery start-directory scoping (from pr-602, fixes #574; pr-705) ---

    def test_matching_basename_outside_start_directory_is_unregistered(self):
        root, text = self.build(
            "python3 -m unittest discover -s tools/fuzz -p 'test_*.py'\n",
            ["tools/test_orphan.py", "tools/fuzz/test_control.py",
             "tools/runner/test_runner.py"])
        self.assertEqual([rel for rel, _ in guard.unregistered(root, text)],
                         ["tools/runner/test_runner.py", "tools/test_orphan.py"])

    def test_discovery_does_not_descend_into_non_package_directory(self):
        root, text = self.build(
            "python3 -m unittest discover -s tools -p 'test_*.py'\n",
            ["tools/test_control.py", "tools/fuzz/test_orphan.py"])
        self.assertEqual([rel for rel, _ in guard.unregistered(root, text)],
                         ["tools/fuzz/test_orphan.py"])
        # An explicit start directory needs no __init__.py of its own.
        text += "python3 -m unittest discover -s tools/fuzz -p 'test_*.py'\n"
        self.assertEqual(guard.unregistered(root, text), [])

    def test_discovery_descends_through_packages_only(self):
        root, text = self.build(
            "python3 -m unittest discover -s tools -p 'test_*.py'\n",
            ["tools/pkg/__init__.py", "tools/pkg/test_one.py",
             "tools/pkg/deep/test_two.py"])
        self.assertEqual([rel for rel, _ in guard.unregistered(root, text)],
                         ["tools/pkg/deep/test_two.py"])
        (root / "tools/pkg/deep/__init__.py").write_text("", encoding="utf-8")
        self.assertEqual(guard.unregistered(root, text), [])

    def test_every_intermediate_directory_must_be_a_package(self):
        # from pr-540
        root, text = self.build(
            "python3 -m unittest discover -s tools -p 'test_*.py'\n",
            ["tools/plain/package/test_x.py"])
        (root / "tools/plain/package/__init__.py").touch()
        self.assertEqual([rel for rel, _ in guard.unregistered(root, text)],
                         ["tools/plain/package/test_x.py"])

    def test_patterns_stay_paired_with_their_start_directories(self):
        root, text = self.build(
            "python3 -m unittest discover -s tools/fuzz -p test_one.py\n"
            "python3 -m unittest discover -s tools/runner -p test_two.py\n",
            ["tools/fuzz/test_one.py", "tools/fuzz/test_two.py",
             "tools/runner/test_two.py"])
        self.assertEqual([rel for rel, _ in guard.unregistered(root, text)],
                         ["tools/fuzz/test_two.py"])

    def test_quoted_paths_and_reordered_flags_keep_their_scope(self):
        root, text = self.build(
            'python3 -m unittest discover -p "test_*.py" -s "./tools/with space"\n',
            ["tools/with space/test_one.py", "tools/test_orphan.py"])
        self.assertEqual([rel for rel, _ in guard.unregistered(root, text)],
                         ["tools/test_orphan.py"])

    def test_file_cannot_be_a_start_directory(self):
        root, text = self.build(
            "python3 -m unittest discover -s tools/test_one.py -p test_one.py\n",
            ["tools/test_one.py"])
        self.assertEqual([rel for rel, _ in guard.unregistered(root, text)],
                         ["tools/test_one.py"])

    def test_actual_discovery_skips_failing_test_but_guard_must_reject_it(self):
        root, text = self.build(
            "python3 -m unittest discover -s tools/fuzz -p 'test_*.py'\n",
            ["tools/test_orphan.py", "tools/fuzz/test_control.py"])
        (root / "tools/test_orphan.py").write_text(
            "import unittest\nclass Orphan(unittest.TestCase):\n"
            "    def test_failure(self): self.fail('orphan really ran')\n",
            encoding="utf-8")
        (root / "tools/fuzz/test_control.py").write_text(
            "import unittest\nclass Control(unittest.TestCase):\n"
            "    def test_pass(self): pass\n", encoding="utf-8")
        command = [sys.executable, "-m", "unittest", "discover", "-s"]
        skipped = subprocess.run(command + ["tools/fuzz", "-p", "test_*.py"],
                                 cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertIn("Ran 1 test", skipped.stderr)
        exercised = subprocess.run(command + ["tools", "-p", "test_orphan.py"],
                                   cwd=root, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(exercised.returncode, 0)
        self.assertIn("orphan really ran", exercised.stderr)
        self.assertEqual([rel for rel, _ in guard.unregistered(root, text)],
                         ["tools/test_orphan.py"])

    # --- explicit-invocation end-to-end (from pr-600, fixes #596) ---

    def test_successful_shell_with_disabled_tests_must_fail_registration(self):
        # The shell succeeds without running either deliberately failing test.
        # The guard must detect the missing execution, not approve the mentions.
        commands = (
            "# python3 -m unittest discover -s tools -p test_disabled.py\n"
            "echo 'python3 tests/test_disabled.py'\n"
        )
        root, text = self.build(commands, ["tools/test_disabled.py", "tests/test_disabled.py"])
        for rel in ("tools/test_disabled.py", "tests/test_disabled.py"):
            (root / rel).write_text(
                "raise AssertionError('this test must be executed')\n", encoding="utf-8")
        if os.name != "nt":
            shell = subprocess.run(["/bin/sh", "-c", commands], cwd=root,
                                   capture_output=True, text=True, timeout=10)
            self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertEqual([rel for rel, _ in guard.unregistered(root, text)],
                         ["tools/test_disabled.py", "tests/test_disabled.py"])


class GuardMisuseTests(unittest.TestCase):
    def run_main(self, workflow, suites=()):
        """Run the real main() against a throwaway workflow/root pair."""
        with tempfile.TemporaryDirectory(prefix="hybrid-guard-") as temporary:
            root = Path(temporary)
            path = root / "ci.yml"
            path.write_text(workflow, encoding="utf-8")
            for rel in suites:
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("# suite\n", encoding="utf-8")
            if not suites:
                (root / "tools").mkdir()
            original = guard.sys.argv
            guard.sys.argv = ["check_test_registration.py",
                              "--workflow", str(path), "--root", str(root)]
            try:
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    return guard.main()
            finally:
                guard.sys.argv = original

    def test_workflow_without_any_reference_fails_closed(self):
        self.assertEqual(
            self.run_main("python3 -m py_compile tools/job.py\n", ["tools/test_x.py"]), 2)

    def test_root_without_suites_fails_closed(self):
        self.assertEqual(
            self.run_main("python3 -m unittest discover -s tools -p test_x.py\n"), 2)

    def test_fully_registered_tree_passes(self):
        self.assertEqual(
            self.run_main("python3 -m unittest discover -s tools -p test_x.py\n",
                          ["tools/test_x.py"]), 0)

    def test_orphaned_suite_exits_one(self):
        self.assertEqual(
            self.run_main("python3 -m unittest discover -s tools -p test_x.py\n",
                          ["tools/test_x.py", "tools/test_y.py"]), 1)

    def test_matching_orphan_outside_discovery_directory_exits_one(self):
        self.assertEqual(
            self.run_main("python3 -m unittest discover -s tools/fuzz -p 'test_*.py'\n",
                          ["tools/fuzz/test_control.py", "tools/test_orphan.py"]), 1)


if __name__ == "__main__":
    unittest.main()
