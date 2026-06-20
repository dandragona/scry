"""Tests for install.sh — run the REAL installer as a subprocess against a
`file://` RAW_BASE (no network) into a sandboxed $HOME, with a `sudo` stub on PATH
that records if it is ever invoked. Asserts the traditional user-owned, no-sudo
install pattern: scry lands in ~/.local/bin (mode 755), sudo is never called, and
the installer prints PATH guidance when that dir isn't on $PATH.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402

INSTALL_SH = h.REPO_ROOT / "install.sh"


class InstallShTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="scry-install-home-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        # A stub `sudo` that records its invocation and fails — so any attempt to
        # escalate is both detectable AND harmless (never touches the real system).
        self.stubdir = tempfile.mkdtemp(prefix="scry-install-stub-")
        self.addCleanup(shutil.rmtree, self.stubdir, ignore_errors=True)
        self.sudo_marker = os.path.join(self.stubdir, "sudo-was-called")
        sudo = os.path.join(self.stubdir, "sudo")
        with open(sudo, "w") as f:
            f.write('#!/bin/sh\necho "$@" >> "$SUDO_MARKER"\nexit 1\n')
        os.chmod(sudo, 0o755)

    def _run(self, *, install_dir=None):
        env = dict(os.environ)
        env["HOME"] = self.home
        env["RAW_BASE"] = f"file://{h.REPO_ROOT}"   # curl reads local files, no network
        env["SUDO_MARKER"] = self.sudo_marker
        env.pop("INSTALL_DIR", None)
        env.pop("CLAUDE_CONFIG_DIR", None)           # skills land under the temp $HOME
        if install_dir is not None:
            env["INSTALL_DIR"] = install_dir
        # PATH: stub `sudo` first, then the real tools. Intentionally does NOT contain
        # ~/.local/bin, so the "add to PATH" guidance fires.
        env["PATH"] = self.stubdir + os.pathsep + env["PATH"]
        return subprocess.run(["sh", str(INSTALL_SH)], env=env,
                              capture_output=True, text=True, timeout=120)

    def _user_bin(self, *parts):
        return os.path.join(self.home, ".local", "bin", *parts)

    def test_installs_to_user_dir_without_sudo(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        dest = self._user_bin("scry")
        self.assertTrue(os.path.exists(dest), r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.sudo_marker),
                         "installer must not invoke sudo")

    def test_installed_binary_is_world_readable_755(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        mode = stat.S_IMODE(os.stat(self._user_bin("scry")).st_mode)
        self.assertEqual(mode, 0o755, oct(mode))

    def test_installs_deepseek_adapter_alongside(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(os.path.exists(self._user_bin("scry-deepseek")))

    def test_prints_path_export_when_not_on_path(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        out = r.stdout + r.stderr
        self.assertIn("export PATH", out)
        self.assertIn(self._user_bin(), out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
