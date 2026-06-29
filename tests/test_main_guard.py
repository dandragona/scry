"""Tests for main()-level robustness:

  * a failed aggregator (after a paid run) surfaces a clean 'scry: ...' line, NOT a
    raw Python traceback;
  * an unknown --aggregator/--panel provider name is rejected at parse time (exit 2),
    rather than running a paid panel and then crashing;
  * `scry plan` and `scry --check` stay discoverable from --help.

All paths are driven through the real ./scry subprocess with stub provider binaries
(no paid calls). Bare `scry` is the deep-research pipeline (the only query mode).
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


def _cfg(panel="claude", judge="claude", aggregator="claude"):
    obj = {"panel": [{"provider": panel, "label": panel}],
           "judge": {"provider": judge}, "aggregator": {"provider": aggregator},
           "research": {"max_rounds": 1, "hard_cap": 1, "clarify": False}}
    d = tempfile.mkdtemp(prefix="scry-guard-cfg-")
    p = os.path.join(d, "scry.config.json")
    with open(p, "w") as f:
        json.dump(obj, f)
    return p


class TestNoRawTraceback(unittest.TestCase):
    def test_aggregator_failure_is_clean_not_a_traceback(self):
        # Panel + brief + reflect (claude) succeed; the aggregator/synthesis (codex)
        # errors. The user must see a clean 'scry: ...' message and exit 1 — never a
        # raw Traceback. We never touch a real codex.
        cfg = _cfg(panel="claude", judge="claude", aggregator="codex")
        with h.StubBins({"claude": h.claude_research(), "codex": h.fail()}) as bins:
            r = h.run_scry(["--no-anim", "--config", cfg, "hello"], input="", env=bins.env)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("scry:", r.stderr)


class TestProviderNameValidation(unittest.TestCase):
    def test_unknown_aggregator_rejected_at_parse(self):
        r = h.run_scry(["--aggregator", "ghostzz", "hello"], input="")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("ghostzz", r.stderr)

    def test_unknown_panel_member_rejected_at_parse(self):
        r = h.run_scry(["--panel", "claude,ghostzz", "hello"], input="")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("ghostzz", r.stderr)


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


class TestProgressiveHelp(unittest.TestCase):
    def test_short_help_hides_advanced_and_verb_only_flags(self):
        short = h.run_scry(["--help"]).stdout
        self.assertIn("scry plan", short)        # the two verbs stay discoverable
        self.assertIn("--check", short)
        self.assertIn("--help-all", short)       # pointer to the full list
        self.assertNotIn("--step", short)        # internal protocol flag
        self.assertNotIn("--host", short)        # web-only flag

    def test_help_all_reveals_every_flag(self):
        full = h.run_scry(["--help-all"]).stdout
        for flag in ("--step", "--host", "--port", "--no-open", "--resume",
                     "--allow-downgrade", "--overwrite"):
            self.assertIn(flag, full, flag)


if __name__ == "__main__":
    unittest.main()
