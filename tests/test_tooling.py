"""The repository's own tooling: the secret scanner and its pre-push hook.

These are the only tests that run a subprocess, so they are the only ones that
could depend on the machine.  Every subprocess gets a minimal environment with
HOME and the XDG directories inside a temporary directory, and when a required
program is missing the test is skipped with a clear message rather than being
weakened into something that always passes.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _bootstrap import REPO, isolated_env, missing_tools

TOOLS = REPO / "tools"
SCANNER = TOOLS / "secret-scan.sh"
HOOK = TOOLS / "hooks" / "pre-push"
INSTALLER = TOOLS / "install-git-hooks.sh"

#: A credential-like string, assembled from fragments so that this file does not
#: match the scanner's own pattern (the scanner reads this file too).
FAKE_TOKEN = "gh" + "p_" + "A" * 36
ZERO_SHA = "0" * 40


class ToolingTestCase(unittest.TestCase):
    """Base class: run a tool with a temp HOME/XDG and a scrubbed environment."""

    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.env = isolated_env(self._home.name)

    def run_tool(self, *argv, stdin=None, cwd=REPO):
        missing = missing_tools("bash", "grep")
        if missing:
            self.skipTest("%s cannot run here: %s not on PATH"
                          % (Path(argv[0]).name, ", ".join(missing)))
        return subprocess.run([str(a) for a in argv], cwd=str(cwd), env=self.env,
                              input=stdin, capture_output=True, text=True)


class TestToolsAreShipped(ToolingTestCase):
    def test_scanner_hook_and_installer_are_present_and_executable(self):
        for path in (SCANNER, HOOK, INSTALLER):
            with self.subTest(tool=path.name):
                self.assertTrue(path.is_file(), "missing %s" % path)
                self.assertTrue(os.access(path, os.X_OK), "%s is not executable" % path)

    def test_the_installer_installs_the_pushed_hook(self):
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("tools/hooks/pre-push", text)


class TestScannerOverTheTree(ToolingTestCase):
    def test_the_tree_has_no_credential_like_strings(self):
        result = self.run_tool(SCANNER, REPO)
        if result.returncode != 0:
            # report the file names only: never echo a possible credential value
            paths = sorted({line.split(":", 1)[0]
                            for line in (result.stdout + result.stderr).splitlines()
                            if ":" in line and not line.startswith("secret-scan:")})
            self.fail("secret-scan exited %d; check: %s"
                      % (result.returncode, ", ".join(paths) or "see tools/secret-scan.sh"))
        self.assertIn("clean", result.stdout)


class TestScannerBehaviour(ToolingTestCase):
    def test_clean_input_passes(self):
        result = self.run_tool(SCANNER, "--stdin", stdin="nothing to see here\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_credential_like_input_is_refused(self):
        result = self.run_tool(SCANNER, "--stdin", stdin="token = %s\n" % FAKE_TOKEN)
        self.assertEqual(result.returncode, 1)
        self.assertIn("credential-like content", result.stderr)

    def test_forbidden_file_name_is_refused(self):
        result = self.run_tool(SCANNER, "--names", stdin="deploy/id_rsa.key\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("forbidden file name", result.stderr)

    def test_ordinary_file_names_pass(self):
        result = self.run_tool(SCANNER, "--names", stdin="docs/index.html\nbuild.py\n")
        self.assertEqual(result.returncode, 0)


class TestPrePushHook(ToolingTestCase):
    def test_it_refuses_outside_a_git_repository(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            result = self.run_tool(HOOK, stdin="", cwd=elsewhere)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not inside a git repository", result.stderr)

    def test_a_deleting_push_has_nothing_to_scan(self):
        result = self.run_tool(HOOK, stdin="refs/heads/topic %s refs/heads/topic %s\n"
                                           % (ZERO_SHA, ZERO_SHA))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("clean", result.stdout)

    def test_the_outgoing_commit_is_scanned(self):
        if missing_tools("git"):
            self.skipTest("git is not on PATH; the pre-push hook cannot run here")
        head = self._git("rev-parse", "HEAD")
        self.assertTrue(head, "not a git repository; the hook cannot be exercised")
        base = self._git("rev-parse", "HEAD~1") or ZERO_SHA
        ref = "refs/heads/main"
        result = self.run_tool(HOOK, stdin="%s %s %s %s\n" % (ref, head, ref, base))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _git(self, *args):
        done = subprocess.run(["git", *args], cwd=str(REPO), env=self.env,
                              capture_output=True, text=True)
        return done.stdout.strip() if done.returncode == 0 else ""


if __name__ == "__main__":
    unittest.main()
