"""Tests for `scry update` (do_update) — exercised via SUBPROCESS on a COPY of
scry so the in-place file swap never touches the repo's tracked ./scry.

Every test points env SCRY_UPDATE_URL at a localhost FileServer serving a fixed
byte payload (no network, no GitHub). Payloads are derived from the real scry
source so they pass / fail do_update's validation as intended.
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


def _run_update(copy, url, *extra, cwd=None, link=None, timeout=60):
    """Run `<copy|link> update [extra...]` with SCRY_UPDATE_URL=url."""
    target = str(link) if link is not None else str(copy)
    env = dict(os.environ, SCRY_UPDATE_URL=url)
    return subprocess.run(
        [target, "update", *extra], env=env, cwd=cwd or os.path.dirname(str(copy)),
        capture_output=True, text=True, timeout=timeout)


class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.cur = self.scry.VERSION
        self.src = h.SCRY.read_text()
        # Guard: the version string must appear verbatim so .replace works.
        self.assertIn(f'VERSION = "{self.cur}"', self.src)
        self.tmp = tempfile.mkdtemp(prefix="scry-update-test-")
        self.copy = h.make_scry_copy(self.tmp)
        # Snapshot the copy's original bytes to assert "unchanged" on no-op paths.
        self.orig_bytes = self.copy.read_bytes()
        # Sanity: never run against the repo's real scry.
        self.assertNotEqual(self.copy.resolve(), h.SCRY.resolve())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bump(self, new_version: str) -> bytes:
        """The real source with VERSION rewritten to `new_version`."""
        return self.src.replace(
            f'VERSION = "{self.cur}"', f'VERSION = "{new_version}"', 1).encode()

    # ------------------------------------------------------------------ #
    def test_already_up_to_date(self):
        # Serve the EXACT bytes of the installed copy => byte-identical => no-op.
        payload = self.copy.read_bytes()
        with h.FileServer(payload) as srv:
            r = _run_update(self.copy, srv.url)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("already up to date", r.stdout)
        # Copy must be untouched.
        self.assertEqual(self.copy.read_bytes(), self.orig_bytes)

    def test_upgrade(self):
        payload = self._bump("99.0.0")
        with h.FileServer(payload) as srv:
            r = _run_update(self.copy, srv.url)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("updated", r.stdout)
        self.assertIn("99.0.0", self.copy.read_text())
        # And the file actually changed.
        self.assertNotEqual(self.copy.read_bytes(), self.orig_bytes)
        self.assertEqual(self.copy.read_bytes(), payload)

    def test_downgrade_without_force_refused(self):
        payload = self._bump("0.0.1")
        with h.FileServer(payload) as srv:
            r = _run_update(self.copy, srv.url)
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("older", r.stdout)
        # Copy must be untouched.
        self.assertEqual(self.copy.read_bytes(), self.orig_bytes)

    def test_downgrade_with_force_applies(self):
        payload = self._bump("0.0.1")
        with h.FileServer(payload) as srv:
            r = _run_update(self.copy, srv.url, "--force")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("updated", r.stdout)
        self.assertIn('VERSION = "0.0.1"', self.copy.read_text())
        self.assertEqual(self.copy.read_bytes(), payload)

    def test_truncated_download(self):
        # Advertise a Content-Length larger than the actual payload => the body is
        # short. urllib's resp.read() raises http.client.IncompleteRead (a dropped
        # connection / CDN short read), which do_update now catches explicitly and
        # turns into a graceful rc-1 "incomplete download" message — no traceback,
        # and the installed copy is left untouched.
        payload = self._bump("99.0.0")
        with h.FileServer(payload, content_length=len(payload) + 50) as srv:
            r = _run_update(self.copy, srv.url)
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("incomplete download", r.stdout)
        # No uncaught exception leaked to stderr.
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("IncompleteRead", r.stderr)
        # The installed copy is still left untouched (failure happens pre-swap).
        self.assertEqual(self.copy.read_bytes(), self.orig_bytes)

    def test_not_a_scry_payload(self):
        payload = b"just some text\n"
        with h.FileServer(payload) as srv:
            r = _run_update(self.copy, srv.url)
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("doesn't look like a complete scry", r.stdout)
        self.assertEqual(self.copy.read_bytes(), self.orig_bytes)

    def test_invalid_python_but_scry_ish(self):
        # Passes the "looks like scry" gate (python3 shebang on line 1, a VERSION=
        # line, and the __main__ marker) but fails to compile (unterminated string /
        # truncated def). do_update should reject it at the compile() step.
        payload = (
            "#!/usr/bin/env python3\n"
            'VERSION = "99.0.0"\n'
            "def broken(\n"          # unterminated function signature -> SyntaxError
            'x = "oops\n'            # also an unterminated string literal
            'if __name__ == "__main__":\n'
            "    main()\n"
        ).encode()
        with h.FileServer(payload) as srv:
            r = _run_update(self.copy, srv.url)
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("isn't valid Python", r.stdout)
        self.assertEqual(self.copy.read_bytes(), self.orig_bytes)

    def test_http_error(self):
        with h.FileServer(b"", status=404) as srv:
            r = _run_update(self.copy, srv.url)
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("download failed", r.stdout)
        self.assertEqual(self.copy.read_bytes(), self.orig_bytes)

    def test_symlink_install_refused(self):
        link = os.path.join(self.tmp, "scry-link")
        os.symlink(str(self.copy), link)
        # A newer payload would otherwise upgrade — but a symlinked install must
        # refuse and send the user to `git pull`, leaving both files untouched.
        payload = self._bump("99.0.0")
        with h.FileServer(payload) as srv:
            r = _run_update(self.copy, srv.url, link=link)
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("symlink", r.stdout)
        # Neither the link target nor the symlink content should be rewritten.
        self.assertEqual(self.copy.read_bytes(), self.orig_bytes)
        self.assertTrue(os.path.islink(link))

    def test_repo_scry_untouched(self):
        # Sanity guard for the whole module: the real ./scry is byte-identical to
        # the snapshot taken at module import (no test ever wrote to it).
        self.assertEqual(h.SCRY.read_text(), self.src)


if __name__ == "__main__":
    unittest.main()
