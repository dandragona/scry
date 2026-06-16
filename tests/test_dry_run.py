"""Tests for scry.do_dry_run(cfg, mode, settings).

do_dry_run prints a human-readable preview of the exact provider command lines
Fusion would run, without invoking any model. We capture stdout and assert on the
PROPOSER / JUDGE / AGGREGATOR lines and the header. No subprocesses, no money.

Source under test: scry lines 763-790 (do_dry_run), 750-760 (_dry_prompt/_show).
"""
import contextlib
import copy
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


def _dry(cfg, mode, settings):
    """Run do_dry_run and return its captured stdout."""
    scry = h.load_scry()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        scry.do_dry_run(cfg, mode, settings)
    return buf.getvalue()


def _lines(out, prefix):
    """All output lines whose role column starts with `prefix`."""
    return [ln for ln in out.splitlines() if ln.startswith(prefix)]


class TestDryRun(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        # Fresh config per test; deepcopy so mutation can't leak between tests.
        self.cfg = self.scry.load_config(str(h.CONFIG_JSON))

    # ---- fusion mode, default panel ------------------------------------- #
    def test_fusion_default_panel_structure(self):
        out = _dry(self.cfg, "fusion", self.cfg["settings"])
        # 3 proposers (claude/codex/agy), exactly one judge, one aggregator.
        self.assertEqual(len(_lines(out, "PROPOSER")), 3, out)
        self.assertEqual(len(_lines(out, "JUDGE")), 1, out)
        self.assertEqual(len(_lines(out, "AGGREGATOR")), 1, out)

    def test_fusion_header_line(self):
        out = _dry(self.cfg, "fusion", self.cfg["settings"])
        header = out.splitlines()[0]
        self.assertIn("[dry-run]", header)
        self.assertIn("mode=fusion", header)
        self.assertIn("web_tools=True", header)
        self.assertIn("max_tool_calls=8", header)

    def test_claude_proposer_json_output_format(self):
        out = _dry(self.cfg, "fusion", self.cfg["settings"])
        claude_proposer = next(
            ln for ln in _lines(out, "PROPOSER") if " claude " in ln
        )
        self.assertIn("--output-format json", claude_proposer)
        self.assertIn("<prompt on stdin>", claude_proposer)

    def test_agy_proposer_prompt_as_arg(self):
        out = _dry(self.cfg, "fusion", self.cfg["settings"])
        agy_proposer = next(
            ln for ln in _lines(out, "PROPOSER") if " agy " in ln
        )
        # agy takes the prompt as a CLI arg (prompt:"arg", prompt_flag "-p").
        self.assertIn("<prompt as arg>", agy_proposer)
        self.assertIn("-p", agy_proposer.split())
        # The prompt placeholder is rendered as {PROMPT} (upper of "prompt").
        self.assertIn("{PROMPT}", agy_proposer)

    def test_claude_proposer_prompt_on_stdin(self):
        out = _dry(self.cfg, "fusion", self.cfg["settings"])
        claude_proposer = next(
            ln for ln in _lines(out, "PROPOSER") if " claude " in ln
        )
        self.assertIn("<prompt on stdin>", claude_proposer)
        self.assertNotIn("<prompt as arg>", claude_proposer)

    # ---- synthesize mode (no judge) ------------------------------------- #
    def test_synthesize_mode_omits_judge(self):
        out = _dry(self.cfg, "synthesize", self.cfg["settings"])
        self.assertEqual(len(_lines(out, "JUDGE")), 0, out)
        self.assertEqual(len(_lines(out, "PROPOSER")), 3, out)
        self.assertEqual(len(_lines(out, "AGGREGATOR")), 1, out)
        self.assertIn("mode=synthesize", out.splitlines()[0])

    # ---- unknown provider in the panel ---------------------------------- #
    def test_unknown_provider_panel_line(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["panel"] = [{"provider": "ghost", "model": "", "label": "x"}]
        out = _dry(cfg, "fusion", cfg["settings"])
        proposers = _lines(out, "PROPOSER")
        self.assertEqual(len(proposers), 1, out)
        self.assertIn("PROPOSER   (unknown provider 'ghost')", out)

    # ---- kimi in the panel ---------------------------------------------- #
    def test_kimi_proposer_agent_file(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["panel"].append(
            {"provider": "kimi", "model": "kimi-k2.6", "label": "kimi"}
        )
        out = _dry(cfg, "fusion", cfg["settings"])
        kimi_line = next(ln for ln in _lines(out, "PROPOSER") if "kimi" in ln)
        self.assertIn("kimi --quiet", kimi_line)
        self.assertIn("--agent-file", kimi_line)
        # The generated agent-file path is shown as the {AGENTFILE} placeholder.
        self.assertIn("{AGENTFILE}", kimi_line)

    # ---- web tools off -------------------------------------------------- #
    def test_web_off_header_and_claude_disallow(self):
        cfg = copy.deepcopy(self.cfg)
        settings = copy.deepcopy(cfg["settings"])
        settings["web_tools"] = False
        out = _dry(cfg, "fusion", settings)
        self.assertIn("web_tools=False", out.splitlines()[0])
        claude_proposer = next(
            ln for ln in _lines(out, "PROPOSER") if " claude " in ln
        )
        # web off => the web_off --disallowedTools form, never --allowedTools.
        self.assertIn("--disallowedTools", claude_proposer)
        self.assertNotIn("--allowedTools", claude_proposer)

    # ---- aggregator always runs with web off ---------------------------- #
    def test_aggregator_always_web_off(self):
        # Even in fusion + web_tools=True, the AGGREGATOR (final synthesis) is
        # rendered with web off: no --allowedTools on its claude command line.
        out = _dry(self.cfg, "fusion", self.cfg["settings"])
        agg = _lines(out, "AGGREGATOR")[0]
        self.assertNotIn("--allowedTools", agg)
        self.assertIn("--disallowedTools", agg)
        # Sanity: a panel claude line in the SAME run does carry --allowedTools,
        # proving the aggregator's web-off is not just a config-wide default.
        claude_proposer = next(
            ln for ln in _lines(out, "PROPOSER") if " claude " in ln
        )
        self.assertIn("--allowedTools", claude_proposer)


if __name__ == "__main__":
    unittest.main()
