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
import tarfile
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
        # Owner-execute only (we run it as ourselves) — not a permissive mask.
        os.chmod(sudo, os.stat(sudo).st_mode | stat.S_IXUSR)
        # A local repo tarball mimicking GitHub's archive (a `<repo>-<ref>/` wrapper
        # dir) so install_web_package vendors scry_web with NO network.
        self.web_tarball = os.path.join(self.stubdir, "scry-src.tar.gz")
        with tarfile.open(self.web_tarball, "w:gz") as tf:
            tf.add(str(h.REPO_ROOT / "scry_web"), arcname="scry-main/scry_web")

    def _run(self, *, install_dir=None, no_web=False):
        env = dict(os.environ)
        env["HOME"] = self.home
        env["RAW_BASE"] = f"file://{h.REPO_ROOT}"   # curl reads local files, no network
        env["SUDO_MARKER"] = self.sudo_marker
        env.pop("INSTALL_DIR", None)
        env.pop("CLAUDE_CONFIG_DIR", None)           # skills land under the temp $HOME
        # Keep the whole installer hermetic: vendor scry_web from a local tarball, and
        # never let the best-effort pip step reach PyPI.
        env["SCRY_WEB_TARBALL"] = f"file://{self.web_tarball}"
        env["SCRY_NO_WEB_DEPS"] = "1"
        if no_web:
            env["SCRY_NO_WEB"] = "1"
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

    def test_installs_glm_adapter_alongside(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(os.path.exists(self._user_bin("scry-glm")))

    def test_prints_path_export_when_not_on_path(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        out = r.stdout + r.stderr
        self.assertIn("export PATH", out)
        self.assertIn(self._user_bin(), out)

    def test_vendors_scry_web_package_next_to_binary(self):
        # `scry web` must work after the standard install (no clone): the scry_web
        # package lands next to the binary, where the web subcommand + engine look.
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(os.path.exists(self._user_bin("scry_web", "__init__.py")),
                        r.stdout + r.stderr)
        # the vendored SPA assets ride along
        self.assertTrue(os.path.exists(
            self._user_bin("scry_web", "static", "index.html")))

    def test_scry_no_web_skips_the_package(self):
        r = self._run(no_web=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(os.path.exists(self._user_bin("scry")))      # core still installs
        self.assertFalse(os.path.exists(self._user_bin("scry_web")))  # but not the web pkg

    def _run_from_fake_repo(self, fake_repo):
        """Run the installer with RAW_BASE pointed at a hand-built fake repo dir
        (so we control exactly what `curl` "downloads" for each file). Returns the
        completed process. Web steps are skipped — we only care about `scry` here.
        """
        env = dict(os.environ)
        env["HOME"] = self.home
        env["RAW_BASE"] = f"file://{fake_repo}"
        env["SUDO_MARKER"] = self.sudo_marker
        env.pop("INSTALL_DIR", None)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env["SCRY_NO_WEB"] = "1"          # no web pkg/deps to fetch
        env["SCRY_NO_WEB_DEPS"] = "1"
        env["PATH"] = self.stubdir + os.pathsep + env["PATH"]
        return subprocess.run(["sh", str(INSTALL_SH)], env=env,
                              capture_output=True, text=True, timeout=120)

    def _make_fake_repo(self, scry_body):
        """A minimal fake repo dir holding a `scry` file with the given body, plus
        the sibling adapter scripts (real ones, so the install only fails — if at
        all — on the `scry` payload under test)."""
        repo = tempfile.mkdtemp(prefix="scry-fake-repo-", dir=self.stubdir)
        with open(os.path.join(repo, "scry"), "w") as f:
            f.write(scry_body)
        # Real sibling scripts so install_file for them succeeds.
        for sib in ("scry-deepseek", "scry-glm"):
            shutil.copy(str(h.REPO_ROOT / sib), os.path.join(repo, sib))
        return repo

    def test_truncated_payload_with_shebang_is_rejected(self):
        # A download that keeps the `python3` shebang but is truncated mid-file —
        # so it neither carries the entry-point marker nor compiles. The old
        # head-of-file-only check passed it through; the installer must now reject
        # it and NOT leave a broken `scry` on the user's PATH.
        truncated = "#!/usr/bin/env python3\nimport sys\ndef main(\n"  # syntactically broken
        repo = self._make_fake_repo(truncated)
        r = self._run_from_fake_repo(repo)
        self.assertNotEqual(r.returncode, 0,
                            "installer must reject a truncated/non-compiling scry payload")
        self.assertFalse(os.path.exists(self._user_bin("scry")),
                         "a rejected payload must not be installed")

    def test_noncompiling_payload_with_entry_marker_is_rejected(self):
        # Has the shebang AND the entry marker, but a syntax error — so it would
        # pass a grep-only check yet fail py_compile.
        bad = ('#!/usr/bin/env python3\n'
               'def main(:\n'         # syntax error
               '    pass\n'
               'if __name__ == "__main__":\n'
               '    main()\n')
        repo = self._make_fake_repo(bad)
        r = self._run_from_fake_repo(repo)
        self.assertNotEqual(r.returncode, 0,
                            "installer must reject a non-compiling scry payload")
        self.assertFalse(os.path.exists(self._user_bin("scry")))

    def test_valid_payload_with_marker_and_compiles_is_accepted(self):
        # The positive control: a tiny but COMPLETE, compilable scry-shaped file
        # (shebang + entry marker + valid Python) must still install cleanly.
        good = ('#!/usr/bin/env python3\n'
                'import sys\n'
                'def main():\n'
                '    print("ok")\n'
                'if __name__ == "__main__":\n'
                '    main()\n')
        repo = self._make_fake_repo(good)
        r = self._run_from_fake_repo(repo)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(os.path.exists(self._user_bin("scry")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
