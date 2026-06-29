"""Tests for scry.do_dry_run(cfg, settings, cli_overrides).

do_dry_run prints a human-readable preview of the exact provider command lines the
deep-research pipeline would run (the only query pipeline), without invoking any
model: PROPOSER lines (web-on panel), one REFLECT line (the per-round judge, web-off),
one AGGREGATOR line (synthesis, web-off), and a header. We capture stdout and assert.
No subprocesses, no money.
"""
import contextlib
import copy
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


def _dry(cfg, settings, cli_overrides=None):
    """Run do_dry_run and return its captured stdout."""
    scry = h.load_scry()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        scry.do_dry_run(cfg, settings, cli_overrides=cli_overrides)
    return buf.getvalue()


def _lines(out, prefix):
    """All output lines whose role column starts with `prefix`."""
    return [ln for ln in out.splitlines() if ln.startswith(prefix)]


class TestDryRun(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        # Fresh config per test; deepcopy so mutation can't leak between tests.
        self.cfg = self.scry.load_config(str(h.CONFIG_JSON))

    # ---- research pipeline, default panel ------------------------------- #
    def test_research_default_panel_structure(self):
        out = _dry(self.cfg, self.cfg["settings"])
        # 6 proposers (claude/codex/agy/deepseek/kimi/glm), one REFLECT, one aggregator.
        self.assertEqual(len(_lines(out, "PROPOSER")), 6, out)
        self.assertEqual(len(_lines(out, "REFLECT")), 1, out)
        self.assertEqual(len(_lines(out, "JUDGE")), 0, out)        # research uses REFLECT
        self.assertEqual(len(_lines(out, "AGGREGATOR")), 1, out)

    def test_research_header_line(self):
        out = _dry(self.cfg, self.cfg["settings"])
        header = out.splitlines()[0]
        self.assertIn("[dry-run]", header)
        self.assertIn("research", header)
        self.assertIn("web_tools=True", header)
        self.assertIn("max_tool_calls=uncapped", header)   # uncapped by default

    def test_research_loop_footer(self):
        out = _dry(self.cfg, self.cfg["settings"])
        self.assertIn("[research]", out)
        self.assertIn("gap-driven loop", out)

    def test_claude_proposer_json_output_format(self):
        out = _dry(self.cfg, self.cfg["settings"])
        claude_proposer = next(ln for ln in _lines(out, "PROPOSER") if " claude " in ln)
        self.assertIn("--output-format json", claude_proposer)
        self.assertIn("<prompt on stdin>", claude_proposer)

    def test_agy_proposer_prompt_as_arg(self):
        out = _dry(self.cfg, self.cfg["settings"])
        agy_proposer = next(ln for ln in _lines(out, "PROPOSER") if " agy " in ln)
        self.assertIn("<prompt as arg>", agy_proposer)
        self.assertIn("-p", agy_proposer.split())
        self.assertIn("{PROMPT}", agy_proposer)

    # ---- unknown provider in the panel ---------------------------------- #
    def test_unknown_provider_panel_line(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["panel"] = [{"provider": "ghost", "model": "", "label": "x"}]
        out = _dry(cfg, cfg["settings"])
        proposers = _lines(out, "PROPOSER")
        self.assertEqual(len(proposers), 1, out)
        self.assertIn("PROPOSER   (unknown provider 'ghost')", out)

    # ---- kimi in the panel ---------------------------------------------- #
    def test_kimi_proposer_agent_file(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["panel"].append({"provider": "kimi", "model": "kimi-k2.6", "label": "kimi"})
        out = _dry(cfg, cfg["settings"])
        kimi_line = next(ln for ln in _lines(out, "PROPOSER") if "kimi" in ln)
        self.assertIn("kimi-cli --quiet", kimi_line)
        self.assertIn("--agent-file", kimi_line)
        self.assertIn("{AGENTFILE}", kimi_line)

    # ---- web tools off (via --no-web, i.e. a CLI override) -------------- #
    def test_web_off_header_and_claude_disallow(self):
        # --no-web flows through as a CLI override, which wins over the research
        # phase's web_tools=True default — so the panel preview goes web-off.
        out = _dry(self.cfg, self.cfg["settings"], cli_overrides={"web_tools": False})
        self.assertIn("web_tools=False", out.splitlines()[0])
        claude_proposer = next(ln for ln in _lines(out, "PROPOSER") if " claude " in ln)
        self.assertIn("--disallowedTools", claude_proposer)
        self.assertNotIn("--allowedTools", claude_proposer)

    # ---- the REFLECT per-round judge previews web-off ------------------- #
    def test_reflect_preview_is_web_off(self):
        out = _dry(self.cfg, self.cfg["settings"])
        reflect = _lines(out, "REFLECT")
        self.assertEqual(len(reflect), 1, out)
        line = reflect[0]
        self.assertNotIn("--allowedTools", line)     # web off
        self.assertIn("--disallowedTools", line)

    # ---- aggregator always runs with web off --------------------------- #
    def test_aggregator_always_web_off(self):
        # The AGGREGATOR (final synthesis) is rendered web-off even though the panel
        # is web-on — proving the web-off is the synthesis phase, not a config default.
        out = _dry(self.cfg, self.cfg["settings"])
        agg = _lines(out, "AGGREGATOR")[0]
        self.assertNotIn("--allowedTools", agg)
        self.assertIn("--disallowedTools", agg)
        claude_proposer = next(ln for ln in _lines(out, "PROPOSER") if " claude " in ln)
        self.assertIn("--allowedTools", claude_proposer)


if __name__ == "__main__":
    unittest.main()
