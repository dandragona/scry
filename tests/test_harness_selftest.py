"""Self-test for tests/_harness.py — validates the harness before the rest of the
suite relies on it. Exercises: module loading, an in-process stubbed call_cli,
do_check over stubs, a real ./scry subprocess, and the update FileServer."""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class TestHarness(unittest.TestCase):
    def test_load_scry(self):
        scry = h.load_scry()
        self.assertTrue(hasattr(scry, "render_call"))
        self.assertTrue(callable(scry.load_config))
        # cached -> same object
        self.assertIs(scry, h.load_scry())

    def test_load_scry_eval(self):
        ev = h.load_scry_eval()
        self.assertTrue(callable(ev.draco_aggregate))

    def test_render_call_smoke(self):
        scry = h.load_scry()
        cfg = scry.load_config(str(h.CONFIG_JSON))
        p = cfg["providers"]["claude"]
        argv, env = scry.render_call(p, "opus", None, True, cfg["settings"], "")
        self.assertIn("--output-format", argv)
        self.assertIn("opus", argv)

    def test_run_scry_version(self):
        r = h.run_scry(["--version"])
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("scry "))

    def test_do_check_passes_with_stubs(self):
        scry = h.load_scry()
        cfg = scry.load_config(str(h.CONFIG_JSON))
        import contextlib
        import io
        with h.StubBins({
            "claude": h.version_stub("claude 0.0.0"),
            "codex": h.version_stub("Logged in as ci@example.com"),
            "agy": h.version_stub("agy 0.0.0"),
            "kimi": h.version_stub("kimi 0.0.0"),
            "scry-deepseek": h.version_stub("scry-deepseek 0.0.0"),
        }):
            with contextlib.redirect_stdout(io.StringIO()):
                rc = scry.do_check(cfg, "fusion", cfg["settings"])
        self.assertEqual(rc, 0)

    def test_call_cli_through_stub(self):
        scry = h.load_scry()
        cfg = scry.load_config(str(h.CONFIG_JSON))
        with h.StubBins({"claude": h.claude_json("HELLO FROM STUB")}):
            cwd = tempfile.mkdtemp()
            out = asyncio.run(scry.call_cli(cfg, "claude", "opus", None, "hi",
                                            cwd, 0, True, cfg["settings"]))
        self.assertEqual(out, "HELLO FROM STUB")

    def test_update_via_fileserver(self):
        import subprocess
        scry = h.load_scry()
        # Build a payload that looks like a newer scry (bump VERSION).
        src = (h.SCRY).read_text()
        newer = src.replace(f'VERSION = "{scry.VERSION}"', 'VERSION = "99.0.0"', 1)
        payload = newer.encode()
        tmp = tempfile.mkdtemp()
        copy = h.make_scry_copy(tmp)  # update mutates the COPY, never the repo's scry
        with h.FileServer(payload) as srv:
            env = dict(os.environ, SCRY_UPDATE_URL=srv.url)
            r = subprocess.run([str(copy), "update"], env=env, cwd=tmp,
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("99.0.0", copy.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
