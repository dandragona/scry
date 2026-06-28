"""End-to-end CLI tests for the real ./scry executable, driven as a SUBPROCESS
(h.run_scry) with stub provider binaries injected on PATH via h.StubBins(...).env.

Bare `scry "<question>"` is the deep-research pipeline — the only query mode (there
is no more --mode / fusion / synthesize). We pass an explicit --config naming a
claude-only panel + claude judge + claude aggregator, so ONE h.claude_research()
stub plays every research role (brief / panel / reflect / synthesis) by branching on
--append-system-prompt. The config pins one research round (no gaps) so the runs are
fast and deterministic. run_scry pipes stdout/stderr, so the process sees a non-tty:
streaming + the orb animation stay off and the consensus map auto-print is suppressed
unless --map is passed.

Nothing here spends money — every model call resolves to a stub.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


# A research analysis with non-empty, multi-field content so render_consensus_map()
# produces visible output (lets us assert the map is printed with --map, absent without).
_ANALYSIS = {
    "consensus": ["the sky is blue"],
    "contradictions": ["disagree on shade"],
    "partial_coverage": [],
    "unique_insights": ["only one mentioned rayleigh scattering"],
    "blind_spots": [],
}


def _research_stub(**kw):
    return h.claude_research(findings="CLAUDE FINDINGS", fused="THE RESEARCH ANSWER", **kw)


def _cfg(research=None, extra=None):
    """A throwaway claude-only research config; one round, clarify off, by default."""
    obj = {"panel": [{"provider": "claude", "label": "claude-opus"}],
           "judge": {"provider": "claude"}, "aggregator": {"provider": "claude"},
           "research": research or {"max_rounds": 1, "hard_cap": 1, "clarify": False}}
    if extra:
        obj.update(extra)
    d = tempfile.mkdtemp(prefix="scry-e2e-cfg-")
    p = os.path.join(d, "scry.config.json")
    with open(p, "w") as f:
        json.dump(obj, f)
    return p


class TestCliMeta(unittest.TestCase):
    def test_version(self):
        r = h.run_scry(["--version"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("scry "),
                        f"--version stdout should start with 'scry ': {r.stdout!r}")

    def test_help(self):
        r = h.run_scry(["--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage", r.stdout.lower())

    def test_no_prompt_is_argparse_error(self):
        r = h.run_scry([], input="")
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout!r} stderr={r.stderr!r}")
        self.assertIn("no prompt", r.stderr.lower())


class TestModeRemoved(unittest.TestCase):
    def test_mode_flag_is_rejected(self):
        # --mode no longer exists; argparse rejects it (exit 2), no paid call.
        r = h.run_scry(["--mode", "fusion", "hi"], input="")
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout!r} stderr={r.stderr!r}")
        self.assertIn("unrecognized arguments", r.stderr.lower())

    def test_stale_config_mode_is_ignored_and_research_runs(self):
        # A leftover mode:fusion in a config must be ignored — bare scry still researches.
        cfg = _cfg(extra={"mode": "fusion"})
        with h.StubBins({"claude": _research_stub()}) as stub:
            r = h.run_scry(["--json", "--no-anim", "--config", cfg, "hi"], env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        data = json.loads(r.stdout)
        self.assertEqual(data["mode"], "research")   # research-shaped result, not fusion
        self.assertIn("rounds", data)
        self.assertEqual(data["final"], "THE RESEARCH ANSWER")


class TestCliResearch(unittest.TestCase):
    def test_happy_json_run(self):
        with h.StubBins({"claude": _research_stub()}) as stub:
            r = h.run_scry(["--json", "--no-anim", "--config", _cfg(), "hi"], env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "ok")
        self.assertIsInstance(data["responses"], list)
        self.assertEqual(data["final"], "THE RESEARCH ANSWER")
        self.assertNotIn("streamed", data)           # stripped under --json

    def test_stdin_prompt_json_run(self):
        with h.StubBins({"claude": _research_stub()}) as stub:
            r = h.run_scry(["--json", "--no-anim", "--config", _cfg()],
                           input="from stdin", env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data.get("prompt"), "from stdin")

    def test_show_proposers(self):
        with h.StubBins({"claude": _research_stub()}) as stub:
            r = h.run_scry(["--show-proposers", "--no-anim", "--config", _cfg(), "hi"],
                           env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        self.assertIn("THE RESEARCH ANSWER", r.stdout)         # final on stdout
        self.assertIn("researched answer", r.stderr)           # the proposer-dump separator
        self.assertIn("CLAUDE FINDINGS", r.stderr)             # a proposer's raw findings

    def test_all_panels_fail_json(self):
        with h.StubBins({"claude": h.fail(1, "rate limit")}) as stub:
            r = h.run_scry(["--json", "--no-anim", "--config", _cfg(), "hi"], env=stub.env)
        self.assertEqual(r.returncode, 1, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["failure_reason"], "rate_limited")


class TestConsensusMapGate(unittest.TestCase):
    def test_map_flag_prints_consensus_map(self):
        with h.StubBins({"claude": _research_stub(analysis_fields=_ANALYSIS)}) as stub:
            r = h.run_scry(["--map", "on", "--no-anim", "--config", _cfg(), "hi"], env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        self.assertIn("consensus map", r.stderr)
        self.assertIn("THE RESEARCH ANSWER", r.stdout)

    def test_no_map_default_on_nontty(self):
        with h.StubBins({"claude": _research_stub(analysis_fields=_ANALYSIS)}) as stub:
            r = h.run_scry(["--no-anim", "--config", _cfg(), "hi"], env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        self.assertNotIn("consensus map", r.stderr)
        self.assertIn("THE RESEARCH ANSWER", r.stdout)


class TestQuiet(unittest.TestCase):
    def test_quiet_suppresses_progress(self):
        with h.StubBins({"claude": _research_stub()}) as stub:
            r = h.run_scry(["--quiet", "--no-anim", "--config", _cfg(), "hi"], env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        self.assertNotIn("▸", r.stderr)               # no stage progress
        self.assertNotIn("research round", r.stderr)
        self.assertIn("THE RESEARCH ANSWER", r.stdout)


class TestFlagCleanup(unittest.TestCase):
    def test_removed_flags_error(self):
        # The old boolean/overloaded flags are gone with no aliases (a hard break).
        for flag in ("--no-map", "--no-repo", "--no-out", "--max-rounds", "--force"):
            r = h.run_scry([flag, "x"], input="")
            self.assertEqual(r.returncode, 2, f"{flag}: {r.stdout!r} {r.stderr!r}")
            self.assertIn("unrecognized arguments", r.stderr.lower())

    def test_map_tristate_parses(self):
        for v in ("auto", "on", "off"):
            r = h.run_scry(["--map", v, "--check", "--config", _cfg()], input="")
            self.assertNotEqual(r.returncode, 2, f"--map {v} should parse: {r.stderr!r}")

    def test_map_invalid_value_is_error(self):
        r = h.run_scry(["--map", "bogus", "x"], input="")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_new_verb_flags_parse(self):
        # --repo tri-state and --hard-cap (research) parse without an argparse error.
        r = h.run_scry(["--repo", "none", "--hard-cap", "2", "--check",
                        "--config", _cfg()], input="")
        self.assertNotEqual(r.returncode, 2, r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
