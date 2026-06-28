"""Tests for main()-level robustness:

  * a failed aggregator (after a paid run) surfaces a clean 'scry: ...' line, NOT a
    raw Python traceback;
  * an unknown --aggregator/--panel provider name is rejected at parse time (exit 2),
    rather than running a paid panel and then crashing;
  * a partially-failed fusion panel prints a visible 'fused from N/M' degraded notice
    instead of silently presenting a one-model answer as 'fused'.

All paths are driven through the real ./scry subprocess with stub provider binaries
(no paid calls).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class TestNoRawTraceback(unittest.TestCase):
    def test_aggregator_failure_is_clean_not_a_traceback(self):
        # Panel (claude) succeeds; the aggregator (codex) errors at synthesis time
        # (stubbed to fail, so we never touch a real codex). The user must see a
        # clean 'scry: ...' message and exit 1 — never a raw Traceback.
        with h.StubBins({"claude": h.claude_json("A"), "codex": h.fail()}) as bins:
            r = h.run_scry(["--mode", "synthesize", "--panel", "claude",
                            "--aggregator", "codex", "hello"], env=bins.env)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("scry:", r.stderr)


class TestProviderNameValidation(unittest.TestCase):
    def test_unknown_aggregator_rejected_at_parse(self):
        r = h.run_scry(["--mode", "fusion", "--aggregator", "ghostzz", "hello"])
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("ghostzz", r.stderr)

    def test_unknown_panel_member_rejected_at_parse(self):
        r = h.run_scry(["--mode", "fusion", "--panel", "claude,ghostzz", "hello"])
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("ghostzz", r.stderr)


class TestDegradedFusionNotice(unittest.TestCase):
    def test_partial_panel_failure_prints_degraded_notice(self):
        # 2-member panel: claude ok, codex fails. The run still succeeds (>=1
        # proposer), but the user must be told the panel was degraded.
        stubs = {"claude": h.claude_json("ONLY ANSWER"), "codex": h.fail()}
        with h.StubBins(stubs) as bins:
            r = h.run_scry(["--mode", "synthesize", "--panel", "claude,codex",
                            "hello"], env=bins.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        # A visible 'fused from 1/2 proposers' style notice on stderr.
        self.assertIn("1/2", r.stderr)
        self.assertIn("failed", r.stderr.lower())

    def test_full_panel_success_has_no_degraded_notice(self):
        stubs = {"claude": h.claude_json("A"), "codex": h.codex_outfile("B")}
        with h.StubBins(stubs) as bins:
            r = h.run_scry(["--mode", "synthesize", "--panel", "claude,codex",
                            "hello"], env=bins.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("fused from", r.stderr)


class TestIgnoredFlagWarning(unittest.TestCase):
    def test_research_flag_in_fusion_mode_warns(self):
        # --no-clarify is research-only; using it with --mode fusion silently did
        # nothing. Now it warns (via --dry-run so no model is called).
        r = h.run_scry(["--mode", "fusion", "--no-clarify", "--dry-run", "hello"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--no-clarify", r.stderr)
        self.assertIn("research", r.stderr.lower())

    def test_no_warning_when_flag_matches_mode(self):
        r = h.run_scry(["--mode", "research", "--no-clarify", "--dry-run", "hello"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("only applies to research", r.stderr)


class TestHelpDiscoverability(unittest.TestCase):
    def test_help_lists_plan_command(self):
        # `scry plan` is a headline capability; it must be discoverable from --help,
        # not only buried inside individual flag descriptions.
        r = h.run_scry(["--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("scry plan", r.stdout)
        self.assertIn("planning", r.stdout.lower())

    def test_help_lists_check_command(self):
        r = h.run_scry(["--help"])
        self.assertIn("--check", r.stdout)


if __name__ == "__main__":
    unittest.main()
