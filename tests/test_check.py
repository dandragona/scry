"""Tests for scry.do_check(cfg, settings) — the `scry --check` doctor that verifies
every model CLI is installed/logged in before a (paid) run. No real model CLI is ever
invoked: every probe resolves to a stub binary on PATH via h.StubBins.

do_check returns 0 (ready) / 1 (not ready) and PRINTS a per-provider report plus a
trailing summary; we capture stdout and assert on both the return code and text.

NO_COLOR is forced on for every test so the report is plain ASCII (do_check colors via
color_enabled(sys.stderr), which we disable for deterministic string matching)."""
import contextlib
import copy
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


def _run_check(cfg, settings, with_keys=True):
    """Call do_check with NO_COLOR forced (plain output) and stdout captured.
    Returns (rc, stdout_text).

    The default panel includes the API-key providers deepseek + glm, whose pre-flight
    checks for their key env vars; with_keys=True sets dummy keys so tests that exercise
    *other* behavior still reach the ready path, while with_keys=False unsets them."""
    scry = h.load_scry()
    buf = io.StringIO()
    keys = ({"DEEPSEEK_API_KEY": "sk-test", "GLM_API_KEY": "glm-test"} if with_keys
            else {"DEEPSEEK_API_KEY": None, "GLM_API_KEY": None})
    with h.env_vars(NO_COLOR="1", FORCE_COLOR=None, **keys):
        with contextlib.redirect_stdout(buf):
            rc = scry.do_check(cfg, settings)
    return rc, buf.getvalue()


def _all_stubs():
    return {
        "claude": h.version_stub("claude x"),
        "codex": h.version_stub("Logged in as a@b"),
        "agy": h.version_stub("agy x"),
        "kimi-cli": h.version_stub("kimi x"),
        "scry-deepseek": h.version_stub("scry-deepseek x"),
        "scry-glm": h.version_stub("scry-glm x"),
    }


class TestDoCheck(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        # Fresh config per test so mutations don't leak between tests.
        self.cfg = copy.deepcopy(self.scry.load_config(str(h.CONFIG_JSON)))
        self.settings = self.cfg["settings"]

    # -- happy path: every provider present & probes pass -------------------- #
    def test_all_present_ready(self):
        with h.StubBins(_all_stubs()):
            rc, out = _run_check(self.cfg, self.settings)
        self.assertEqual(rc, 0, out)
        self.assertIn("ready", out)
        self.assertNotIn("not ready", out)
        self.assertNotIn("not installed", out)
        self.assertNotIn("probe failed", out)
        self.assertNotIn("unknown provider", out)

    # -- a binary is not on PATH -------------------------------------------- #
    def test_missing_binary_not_installed(self):
        missing_bin = "scry-nonexistent-binary-xyz"
        self.cfg["providers"]["agy"]["cmd"][0] = missing_bin
        self.cfg["providers"]["agy"]["check"]["probe"][0] = missing_bin
        with h.StubBins({"claude": h.version_stub("claude x"),
                         "codex": h.version_stub("Logged in as a@b")}):
            rc, out = _run_check(self.cfg, self.settings)
        self.assertEqual(rc, 1, out)
        self.assertIn("not installed", out)
        self.assertIn(self.cfg["providers"]["agy"]["check"]["install"], out)
        self.assertIn("not ready", out)

    # -- a provider referenced by the panel isn't in providers{} ------------ #
    def test_unknown_provider_in_panel(self):
        self.cfg["panel"] = [{"provider": "ghost", "model": "", "label": "g"}]
        self.cfg["judge"] = {"provider": "claude", "model": "opus"}
        self.cfg["aggregator"] = {"provider": "claude", "model": "opus"}
        with h.StubBins({"claude": h.version_stub("claude x")}):
            rc, out = _run_check(self.cfg, self.settings)
        self.assertEqual(rc, 1, out)
        self.assertIn("unknown provider", out)
        self.assertIn("ghost", out)

    # -- a probe runs but exits non-zero ------------------------------------ #
    def test_probe_failure(self):
        stubs = _all_stubs()
        stubs["codex"] = h.fail(1)
        with h.StubBins(stubs):
            rc, out = _run_check(self.cfg, self.settings)
        self.assertEqual(rc, 1, out)
        self.assertIn("probe failed", out)
        self.assertIn(self.cfg["providers"]["codex"]["check"]["hint"], out)
        self.assertIn("codex login", out)

    # -- codex probe succeeds & verifies auth -> "logged in" ---------------- #
    def test_codex_verifies_auth_logged_in(self):
        with h.StubBins(_all_stubs()):
            rc, out = _run_check(self.cfg, self.settings)
        self.assertEqual(rc, 0, out)
        self.assertIn("logged in", out)
        self.assertIn("Logged in as a@b", out)

    # -- claude verifies_auth=False -> prints its /login note --------------- #
    def test_claude_note_printed(self):
        with h.StubBins(_all_stubs()):
            rc, out = _run_check(self.cfg, self.settings)
        self.assertEqual(rc, 0, out)
        claude_note = self.cfg["providers"]["claude"]["check"]["note"]
        self.assertIn(claude_note, out)
        self.assertIn("↳ " + claude_note, out)
        self.assertIn("/login", out)

    # -- summary describes the research pipeline ---------------------------- #
    def test_summary_research_pipeline(self):
        with h.StubBins(_all_stubs()):
            rc, out = _run_check(self.cfg, self.settings)
        self.assertEqual(rc, 0, out)
        self.assertIn("research:", out)
        self.assertIn("rounds", out)
        self.assertIn("model calls/run", out)
        # breakdown names the research stages, not a fusion judge.
        self.assertIn("brief", out)
        self.assertIn("synthesis", out)
        self.assertNotIn("mode: fusion", out)
        self.assertNotIn("panel+judge+synthesis", out)

    # -- research always probes the judge (the reflect stage) --------------- #
    def test_unknown_judge_is_probed_and_fails(self):
        # research runs a judge every round (REFLECT), so an unknown judge provider
        # is probed and fails the pre-flight (it is never silently skipped).
        self.cfg["judge"] = {"provider": "phantom", "model": "x"}
        with h.StubBins(_all_stubs()):
            rc, out = _run_check(self.cfg, self.settings)
        self.assertEqual(rc, 1, out)
        self.assertIn("phantom", out)
        self.assertIn("unknown provider", out)

    # -- dedup: claude is panel+judge+aggregator -> probed exactly once ------ #
    def test_dedup_claude_probed_once(self):
        self.assertEqual(self.cfg["judge"]["provider"], "claude")
        self.assertEqual(self.cfg["aggregator"]["provider"], "claude")
        self.assertTrue(any(m["provider"] == "claude" for m in self.cfg["panel"]),
                        "expected claude in default panel")
        with h.StubBins(_all_stubs()):
            rc, out = _run_check(self.cfg, self.settings)
        self.assertEqual(rc, 0, out)
        n_claude_lines = sum(1 for ln in out.splitlines()
                             if ln.lstrip().startswith("✓") and "claude" in ln)
        self.assertEqual(n_claude_lines, 1, out)

    # -- a provider with no probe configured -> "installed (binary)" -------- #
    def test_provider_without_probe(self):
        del self.cfg["providers"]["agy"]["check"]["probe"]
        with h.StubBins(_all_stubs()):
            rc, out = _run_check(self.cfg, self.settings)
        self.assertEqual(rc, 0, out)
        agy_lines = [ln for ln in out.splitlines() if "agy" in ln and "✓" in ln]
        self.assertTrue(agy_lines, out)
        self.assertTrue(any("installed (agy)" in ln for ln in agy_lines), out)

    # -- API-key provider with no key set -> not ready (honest pre-flight) --- #
    def test_api_key_provider_without_key_not_ready(self):
        with h.StubBins(_all_stubs()):
            rc, out = _run_check(self.cfg, self.settings, with_keys=False)
        self.assertEqual(rc, 1, out)
        self.assertIn("DEEPSEEK_API_KEY", out)
        self.assertIn("GLM_API_KEY", out)
        self.assertIn("not set", out)
        self.assertIn("not ready", out)

    # -- API-key provider WITH key set -> ready, and its note is shown ------- #
    def test_api_key_provider_with_key_ready_and_note(self):
        with h.StubBins(_all_stubs()):
            rc, out = _run_check(self.cfg, self.settings, with_keys=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("ready", out)
        ds_note = self.cfg["providers"]["deepseek"]["check"]["note"]
        self.assertIn(ds_note, out)

    # -- web_tools=True surfaces the web-search billing note ---------------- #
    def test_web_billing_note(self):
        settings_web = copy.deepcopy(self.settings)
        settings_web["web_tools"] = True
        with h.StubBins(_all_stubs()):
            rc, out = _run_check(self.cfg, settings_web)
        self.assertEqual(rc, 0, out)
        self.assertIn("web-search billing", out)
        settings_web["web_tools"] = False
        with h.StubBins(_all_stubs()):
            rc2, out2 = _run_check(self.cfg, settings_web)
        self.assertEqual(rc2, 0, out2)
        self.assertNotIn("web-search billing", out2)


if __name__ == "__main__":
    unittest.main()
