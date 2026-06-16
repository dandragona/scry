"""Tests for scry.do_check(cfg, mode, settings) — the `scry --check` doctor that
verifies every model CLI is installed/logged in before a (paid) run. No real model
CLI is ever invoked: every probe resolves to a stub binary on PATH via h.StubBins.

do_check returns 0 (ready) / 1 (not ready) and PRINTS a per-provider report plus a
trailing summary; we capture stdout and assert on both the return code and text.

NO_COLOR is forced on for every test so the report is plain ASCII (do_check colors
via color_enabled(sys.stderr), which we disable for deterministic string matching)."""
import contextlib
import copy
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


def _run_check(cfg, mode, settings):
    """Call do_check with NO_COLOR forced (plain output) and stdout captured.
    Returns (rc, stdout_text)."""
    scry = h.load_scry()
    buf = io.StringIO()
    with h.env_vars(NO_COLOR="1", FORCE_COLOR=None):
        with contextlib.redirect_stdout(buf):
            rc = scry.do_check(cfg, mode, settings)
    return rc, buf.getvalue()


class TestDoCheck(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        # Fresh config per test so mutations don't leak between tests.
        self.cfg = copy.deepcopy(self.scry.load_config(str(h.CONFIG_JSON)))
        self.settings = self.cfg["settings"]

    # -- happy path: every provider present & probes pass -------------------- #
    def test_all_present_ready(self):
        stubs = {
            "claude": h.version_stub("claude x"),
            # codex probe is `codex login status`, verifies_auth=True; exit 0
            # with this line -> "logged in".
            "codex": h.version_stub("Logged in as a@b"),
            "agy": h.version_stub("agy x"),
        }
        with h.StubBins(stubs):
            rc, out = _run_check(self.cfg, "fusion", self.settings)
        self.assertEqual(rc, 0, out)
        self.assertIn("ready", out)
        # No failure markers in a healthy report.
        self.assertNotIn("not ready", out)
        self.assertNotIn("not installed", out)
        self.assertNotIn("probe failed", out)
        self.assertNotIn("unknown provider", out)

    # -- a binary is not on PATH -------------------------------------------- #
    def test_missing_binary_not_installed(self):
        # Point agy at a binary that cannot exist on PATH (the test host may have
        # a REAL `agy` installed, so omitting the stub alone wouldn't guarantee a
        # miss). claude + codex are stubbed and present.
        missing_bin = "scry-nonexistent-binary-xyz"
        self.cfg["providers"]["agy"]["cmd"][0] = missing_bin
        # agy's probe also invokes the binary by name; rewrite it to match so the
        # not-installed branch (which keys off cmd[0]) is what fires.
        self.cfg["providers"]["agy"]["check"]["probe"][0] = missing_bin
        stubs = {
            "claude": h.version_stub("claude x"),
            "codex": h.version_stub("Logged in as a@b"),
        }
        with h.StubBins(stubs):
            rc, out = _run_check(self.cfg, "fusion", self.settings)
        self.assertEqual(rc, 1, out)
        self.assertIn("not installed", out)
        # The agy install hint is surfaced under the failing line.
        agy_install = self.cfg["providers"]["agy"]["check"]["install"]
        self.assertIn(agy_install, out)
        # Still emits the not-ready footer.
        self.assertIn("not ready", out)

    # -- a provider referenced by the panel isn't in providers{} ------------ #
    def test_unknown_provider_in_panel(self):
        # Panel references a provider that has no providers{} entry. Point
        # judge + aggregator at claude (which IS stubbed) so the ONLY failure is
        # the unknown 'ghost' provider.
        self.cfg["panel"] = [{"provider": "ghost", "model": "", "label": "g"}]
        self.cfg["judge"] = {"provider": "claude", "model": "opus"}
        self.cfg["aggregator"] = {"provider": "claude", "model": "opus"}
        with h.StubBins({"claude": h.version_stub("claude x")}):
            rc, out = _run_check(self.cfg, "fusion", self.settings)
        self.assertEqual(rc, 1, out)
        self.assertIn("unknown provider", out)
        # The name of the missing provider appears on the failing line.
        self.assertIn("ghost", out)

    # -- a probe runs but exits non-zero ------------------------------------ #
    def test_probe_failure(self):
        # codex probe exits non-zero -> "probe failed" + the codex hint.
        stubs = {
            "claude": h.version_stub("claude x"),
            "codex": h.fail(1),
            "agy": h.version_stub("agy x"),
        }
        with h.StubBins(stubs):
            rc, out = _run_check(self.cfg, "fusion", self.settings)
        self.assertEqual(rc, 1, out)
        self.assertIn("probe failed", out)
        # codex hint mentions logging in via `codex login`.
        codex_hint = self.cfg["providers"]["codex"]["check"]["hint"]
        self.assertIn(codex_hint, out)
        self.assertIn("codex login", out)

    # -- codex probe succeeds & verifies auth -> "logged in" ---------------- #
    def test_codex_verifies_auth_logged_in(self):
        stubs = {
            "claude": h.version_stub("claude x"),
            "codex": h.version_stub("Logged in as a@b"),
            "agy": h.version_stub("agy x"),
        }
        with h.StubBins(stubs):
            rc, out = _run_check(self.cfg, "fusion", self.settings)
        self.assertEqual(rc, 0, out)
        self.assertIn("logged in", out)
        # The probe's first non-empty line is echoed in parens as detail.
        self.assertIn("Logged in as a@b", out)

    # -- claude verifies_auth=False -> prints its /login note --------------- #
    def test_claude_note_printed(self):
        stubs = {
            "claude": h.version_stub("claude x"),
            "codex": h.version_stub("Logged in as a@b"),
            "agy": h.version_stub("agy x"),
        }
        with h.StubBins(stubs):
            rc, out = _run_check(self.cfg, "fusion", self.settings)
        self.assertEqual(rc, 0, out)
        claude_note = self.cfg["providers"]["claude"]["check"]["note"]
        # The full note is printed under the claude line, prefixed with "↳ ".
        self.assertIn(claude_note, out)
        self.assertIn("↳ " + claude_note, out)
        self.assertIn("/login", out)

    # -- summary reflects fusion call count: panel + 2 ---------------------- #
    def test_summary_fusion_call_count(self):
        stubs = {
            "claude": h.version_stub("claude x"),
            "codex": h.version_stub("Logged in as a@b"),
            "agy": h.version_stub("agy x"),
        }
        with h.StubBins(stubs):
            rc, out = _run_check(self.cfg, "fusion", self.settings)
        self.assertEqual(rc, 0, out)
        n_panel = len(self.cfg["panel"])
        self.assertIn(f"~{n_panel + 2} model calls", out)
        self.assertIn("mode: fusion", out)
        # fusion summary names judge in the breakdown.
        self.assertIn("panel+judge+synthesis", out)

    # -- summary reflects synthesize call count: panel + 1 ------------------ #
    def test_summary_synthesize_call_count(self):
        # In 'synthesize' mode the judge provider is NOT added to the probe set,
        # so it isn't separately probed (beyond dedup with panel/aggregator),
        # and the call count is panel + 1 (no judge call).
        stubs = {
            "claude": h.version_stub("claude x"),
            "codex": h.version_stub("Logged in as a@b"),
            "agy": h.version_stub("agy x"),
        }
        with h.StubBins(stubs):
            rc, out = _run_check(self.cfg, "synthesize", self.settings)
        self.assertEqual(rc, 0, out)
        n_panel = len(self.cfg["panel"])
        self.assertIn(f"~{n_panel + 1} model calls", out)
        self.assertIn("mode: synthesize", out)
        # synthesize breakdown omits the judge.
        self.assertIn("panel+synthesis", out)
        self.assertNotIn("panel+judge", out)

    # -- synthesize doesn't separately probe a judge-only provider ---------- #
    def test_synthesize_skips_judge_only_provider(self):
        # Make the judge a provider NOT otherwise used by panel/aggregator.
        # In fusion it would be probed (and fail, as it's unknown); in synthesize
        # it must be ignored entirely.
        self.cfg["judge"] = {"provider": "phantom", "model": "x"}
        stubs = {
            "claude": h.version_stub("claude x"),
            "codex": h.version_stub("Logged in as a@b"),
            "agy": h.version_stub("agy x"),
        }
        with h.StubBins(stubs):
            rc, out = _run_check(self.cfg, "synthesize", self.settings)
        # phantom is never reached -> ready, no "unknown provider".
        self.assertEqual(rc, 0, out)
        self.assertNotIn("phantom", out)
        self.assertNotIn("unknown provider", out)
        # Sanity: in fusion the same judge IS probed and fails.
        with h.StubBins(stubs):
            rc2, out2 = _run_check(self.cfg, "fusion", self.settings)
        self.assertEqual(rc2, 1, out2)
        self.assertIn("phantom", out2)
        self.assertIn("unknown provider", out2)

    # -- dedup: claude is panel+judge+aggregator -> probed exactly once ------ #
    def test_dedup_claude_probed_once(self):
        # Default config: claude is a panel member AND the judge AND the
        # aggregator. It must be deduped to a single probe line.
        self.assertEqual(self.cfg["judge"]["provider"], "claude")
        self.assertEqual(self.cfg["aggregator"]["provider"], "claude")
        self.assertTrue(
            any(m["provider"] == "claude" for m in self.cfg["panel"]),
            "expected claude in default panel",
        )
        stubs = {
            "claude": h.version_stub("claude x"),
            "codex": h.version_stub("Logged in as a@b"),
            "agy": h.version_stub("agy x"),
        }
        with h.StubBins(stubs):
            rc, out = _run_check(self.cfg, "fusion", self.settings)
        self.assertEqual(rc, 0, out)
        # Exactly one successful claude report line (dedup of 3 references).
        # Match the padded provider field "claude   " to avoid matching the note.
        n_claude_lines = sum(
            1 for ln in out.splitlines()
            if ln.lstrip().startswith("✓") and "claude" in ln
        )
        self.assertEqual(n_claude_lines, 1, out)

    # -- a provider with no probe configured -> "installed (binary)" -------- #
    def test_provider_without_probe(self):
        # Strip the probe from agy so it hits the "no probe" branch.
        del self.cfg["providers"]["agy"]["check"]["probe"]
        stubs = {
            "claude": h.version_stub("claude x"),
            "codex": h.version_stub("Logged in as a@b"),
            "agy": h.version_stub("agy x"),
        }
        with h.StubBins(stubs):
            rc, out = _run_check(self.cfg, "fusion", self.settings)
        self.assertEqual(rc, 0, out)
        # agy line reports plain install (binary echoed), no probe attempted.
        agy_lines = [ln for ln in out.splitlines() if "agy" in ln and "✓" in ln]
        self.assertTrue(agy_lines, out)
        self.assertTrue(any("installed (agy)" in ln for ln in agy_lines), out)

    # -- web_tools=True surfaces the web-search billing note ---------------- #
    def test_web_billing_note(self):
        stubs = {
            "claude": h.version_stub("claude x"),
            "codex": h.version_stub("Logged in as a@b"),
            "agy": h.version_stub("agy x"),
        }
        settings_web = copy.deepcopy(self.settings)
        settings_web["web_tools"] = True
        with h.StubBins(stubs):
            rc, out = _run_check(self.cfg, "fusion", settings_web)
        self.assertEqual(rc, 0, out)
        self.assertIn("web-search billing", out)
        # And off -> no billing note.
        settings_web["web_tools"] = False
        with h.StubBins(stubs):
            rc2, out2 = _run_check(self.cfg, "fusion", settings_web)
        self.assertEqual(rc2, 0, out2)
        self.assertNotIn("web-search billing", out2)


if __name__ == "__main__":
    unittest.main()
