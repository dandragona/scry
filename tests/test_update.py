"""Tests for `scry update` (do_update) — exercised via SUBPROCESS on a COPY of
scry so the in-place file swap never touches the repo's tracked ./scry.

Every test points env SCRY_UPDATE_URL at a localhost FileServer serving a fixed
byte payload (no network, no GitHub). Payloads are derived from the real scry
source so they pass / fail do_update's validation as intended.
"""
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


def _run_update(copy, url, *extra, cwd=None, link=None, timeout=60, env_extra=None):
    """Run `<copy|link> update [extra...]` with SCRY_UPDATE_URL=url.

    By default the post-swap aux refresh (do_update's _update_aux) is neutered so it
    can't reach the real ~/.claude skills or the network: CLAUDE_CONFIG_DIR points at
    an empty dir and SCRY_WEB_TARBALL at a nonexistent file. Aux tests pass env_extra
    with a sandboxed CLAUDE_CONFIG_DIR + a real file:// tarball to exercise it."""
    target = str(link) if link is not None else str(copy)
    env = dict(os.environ, SCRY_UPDATE_URL=url)
    env["CLAUDE_CONFIG_DIR"] = os.path.join(os.path.dirname(str(copy)), ".no-claude")
    env["SCRY_WEB_TARBALL"] = "file:///nonexistent-scry-aux.tar.gz"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [target, "update", *extra], env=env, cwd=cwd or os.path.dirname(str(copy)),
        capture_output=True, text=True, timeout=timeout)


def _read(path):
    with open(path) as f:
        return f.read()


def _make_aux_tarball(path, files: dict):
    """Build a gzip tarball mimicking GitHub's archive: every entry under one wrapper
    dir (`pkg/`). `files` maps a repo-relative path to its bytes."""
    with tarfile.open(path, "w:gz") as tf:
        for rel, data in files.items():
            ti = tarfile.TarInfo("pkg/" + rel)
            ti.size = len(data)
            ti.mode = 0o644
            tf.addfile(ti, io.BytesIO(data))


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

    def test_upgrade_makes_file_world_readable(self):
        # Regression: install.sh could leave scry mode 711 (root-owned → a python
        # script non-owners can't READ, so the interpreter fails with Errno 13 and
        # you're forced to `sudo scry`). An update must HEAL that, not preserve it:
        # the swapped-in file must be readable by group + other.
        import stat
        # Reproduce the 711 install bug by REMOVING group/other read from the copy
        # (which starts 755) — a permission-reducing chmod, not a permissive mask.
        os.chmod(self.copy, stat.S_IMODE(os.stat(self.copy).st_mode)
                 & ~(stat.S_IRGRP | stat.S_IROTH))
        payload = self._bump("99.0.0")
        with h.FileServer(payload) as srv:
            r = _run_update(self.copy, srv.url)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        mode = stat.S_IMODE(os.stat(self.copy).st_mode)
        self.assertTrue(mode & stat.S_IRGRP, oct(mode))   # group can read
        self.assertTrue(mode & stat.S_IROTH, oct(mode))   # other can read
        self.assertTrue(mode & stat.S_IXUSR, oct(mode))   # still executable

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

    # ----- the aux refresh: keep the WHOLE install current ------------------ #
    def test_update_refreshes_aux_artifacts(self):
        # After swapping the binary, `scry update` refreshes the OTHER install
        # artifacts that drift (adapters, the scry_web package, the skills) from the
        # repo tarball — only the ones already present.
        install_dir = os.path.dirname(str(self.copy))
        deepseek = os.path.join(install_dir, "scry-deepseek")
        with open(deepseek, "w") as f:
            f.write("OLD-DEEPSEEK\n")
        os.makedirs(os.path.join(install_dir, "scry_web"))
        web_init = os.path.join(install_dir, "scry_web", "__init__.py")
        with open(web_init, "w") as f:
            f.write("# OLD-WEB\n")
        claude = os.path.join(self.tmp, "claude-cfg")
        skill_md = os.path.join(claude, "skills", "scry", "SKILL.md")
        os.makedirs(os.path.dirname(skill_md))
        with open(skill_md, "w") as f:
            f.write("OLD-SKILL\n")

        tarball = os.path.join(self.tmp, "aux.tar.gz")
        _make_aux_tarball(tarball, {
            "scry-deepseek": b"NEW-DEEPSEEK\n",
            "scry_web/__init__.py": b"# NEW-WEB\n",
            "scry_web/static/index.html": b"<html>new</html>\n",
            ".claude/skills/scry/SKILL.md": b"NEW-SKILL\n",
        })
        payload = self._bump("99.0.0")
        with h.FileServer(payload) as srv:
            r = _run_update(self.copy, srv.url, env_extra={
                "SCRY_WEB_TARBALL": f"file://{tarball}",
                "CLAUDE_CONFIG_DIR": claude,
            })
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("also refreshed", r.stdout)
        self.assertEqual(_read(deepseek), "NEW-DEEPSEEK\n")
        self.assertEqual(_read(web_init), "# NEW-WEB\n")
        self.assertTrue(os.path.exists(
            os.path.join(install_dir, "scry_web", "static", "index.html")))
        self.assertEqual(_read(skill_md), "NEW-SKILL\n")

    def test_update_does_not_add_absent_web_package(self):
        # Refresh-if-present: a pure-CLI install (no scry_web next to the binary) must
        # NOT suddenly gain the web package on update — only existing pieces refresh.
        install_dir = os.path.dirname(str(self.copy))
        deepseek = os.path.join(install_dir, "scry-deepseek")
        with open(deepseek, "w") as f:
            f.write("OLD\n")
        tarball = os.path.join(self.tmp, "aux.tar.gz")
        _make_aux_tarball(tarball, {
            "scry-deepseek": b"NEW\n",
            "scry_web/__init__.py": b"# web\n",
        })
        payload = self._bump("99.0.0")
        with h.FileServer(payload) as srv:
            r = _run_update(self.copy, srv.url, env_extra={
                "SCRY_WEB_TARBALL": f"file://{tarball}",
                "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude-empty"),
            })
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(_read(deepseek), "NEW\n")          # present → refreshed
        self.assertFalse(os.path.exists(os.path.join(install_dir, "scry_web")))  # absent → stays absent

    def test_already_up_to_date_still_refreshes_aux(self):
        # A scry_web-only change doesn't bump the binary's VERSION, so the no-op
        # "already up to date" path must still refresh the aux artifacts.
        install_dir = os.path.dirname(str(self.copy))
        os.makedirs(os.path.join(install_dir, "scry_web"))
        web_init = os.path.join(install_dir, "scry_web", "__init__.py")
        with open(web_init, "w") as f:
            f.write("# OLD-WEB\n")
        tarball = os.path.join(self.tmp, "aux.tar.gz")
        _make_aux_tarball(tarball, {"scry_web/__init__.py": b"# NEW-WEB\n"})
        payload = self.copy.read_bytes()  # byte-identical → "already up to date"
        with h.FileServer(payload) as srv:
            r = _run_update(self.copy, srv.url, env_extra={
                "SCRY_WEB_TARBALL": f"file://{tarball}",
                "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude-empty"),
            })
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("already up to date", r.stdout)
        self.assertEqual(_read(web_init), "# NEW-WEB\n")
        self.assertEqual(self.copy.read_bytes(), self.orig_bytes)  # binary untouched


if __name__ == "__main__":
    unittest.main()
