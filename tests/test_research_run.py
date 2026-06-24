"""Deep Research mode — orchestrator (research_run) loop behavior, driven by stub
CLIs so nothing here ever calls a real model.

The claude_research stub plays brief/proposer/referee/synthesis by branching on each
prompt's anchor; the other providers act as plain panel proposers. Run hermetically:
  python3 -m unittest discover -s tests
"""
from __future__ import annotations

import asyncio
import copy
import unittest

from _harness import (StubBins, load_scry, claude_research, flaky_text,
                      codex_outfile, agy_text, kimi_text, deepseek_text, fail)

S = load_scry()


def _noop_log(*_a, **_k):
    return None


# An explicit 5-provider panel (one no-web: deepseek) that exactly matches the stubs
# in _full_panel_stubs. Pinned so these tests are deterministic regardless of how many
# providers ship in the default panel (e.g. glm was added as a 6th) — and hermetic: an
# unstubbed default member would otherwise resolve to its real adapter.
_CLASSIC_PANEL = [
    {"provider": "claude", "label": "claude-opus"},
    {"provider": "codex", "label": "codex-gpt"},
    {"provider": "agy", "label": "gemini-pro"},
    {"provider": "deepseek", "label": "deepseek"},
    {"provider": "kimi", "label": "kimi"},
]


def _research_cfg(max_rounds=1, hard_cap=2, panel=None, early_exit=True):
    cfg = copy.deepcopy(S.DEFAULT_CONFIG)
    cfg["research"]["max_rounds"] = max_rounds
    cfg["research"]["hard_cap"] = hard_cap
    cfg["research"]["early_exit"] = early_exit
    cfg["research"]["clarify"] = False
    cfg["panel"] = panel if panel is not None else [dict(m) for m in _CLASSIC_PANEL]
    return cfg


def _full_panel_stubs(**research_kwargs):
    return {
        "claude": claude_research(**research_kwargs),
        "codex": codex_outfile("CODEX FINDINGS"),
        "agy": agy_text("GEMINI FINDINGS"),
        "kimi-cli": kimi_text("KIMI FINDINGS"),
        "scry-deepseek": deepseek_text("DEEPSEEK FINDINGS"),
    }


def _run(cfg):
    settings = dict(cfg["settings"])
    return asyncio.run(S.research_run(cfg, "What is X?", settings, _noop_log))


def _provs(round_responses):
    # responses carry the panel label (claude-opus, codex-gpt, gemini-pro, deepseek, kimi)
    return [r["model"] for r in round_responses if r["ok"]]


class TestResearchRunShape(unittest.TestCase):
    def test_two_round_run_has_brief_rounds_and_final(self):
        cfg = _research_cfg(max_rounds=1, hard_cap=2)
        with StubBins(_full_panel_stubs(gaps=True, needs_web=True)):
            result = _run(cfg)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "research")
        self.assertEqual(result["brief"]["sub_questions"], ["sub-q-1", "sub-q-2", "sub-q-3"])
        self.assertEqual(len(result["rounds"]), 2)
        self.assertEqual(result["final"], "RESEARCH ANSWER")
        # brief + 2x(panel+reflect) + synth all metered
        self.assertTrue(result["cost"]["calls"] >= 6)

    def test_brief_intent_is_carried(self):
        cfg = _research_cfg(max_rounds=1, hard_cap=1)
        with StubBins(_full_panel_stubs(gaps=False)):
            result = _run(cfg)
        self.assertEqual(result["brief"]["intent"], "INTENT")


class TestResearchLoopStop(unittest.TestCase):
    def test_no_gaps_stops_after_first_round(self):
        # Judge reports no open questions -> nothing to chase -> a single round even
        # though the target depth is higher.
        cfg = _research_cfg(max_rounds=3, hard_cap=5)
        with StubBins(_full_panel_stubs(gaps=False)):
            result = _run(cfg)
        self.assertEqual(len(result["rounds"]), 1)

    def test_persistent_gaps_run_to_hard_cap(self):
        cfg = _research_cfg(max_rounds=1, hard_cap=3)
        with StubBins(_full_panel_stubs(gaps=True, needs_web=True)):
            result = _run(cfg)
        self.assertEqual(len(result["rounds"]), 3)


class TestResearchRouting(unittest.TestCase):
    def test_round_two_excludes_no_web_provider_for_web_gaps(self):
        cfg = _research_cfg(max_rounds=1, hard_cap=2)
        with StubBins(_full_panel_stubs(gaps=True, needs_web=True)):
            result = _run(cfg)
        round1 = _provs(result["rounds"][0]["responses"])
        round2 = _provs(result["rounds"][1]["responses"])
        self.assertIn("deepseek", round1)         # round 1 = the full brief to everyone
        self.assertNotIn("deepseek", round2)       # round 2 web gap skips the no-web model
        self.assertIn("claude-opus", round2)

    def test_evidence_accumulates_across_rounds(self):
        cfg = _research_cfg(max_rounds=1, hard_cap=2)
        with StubBins(_full_panel_stubs(gaps=True, needs_web=True)):
            result = _run(cfg)
        # round 1: 5 proposers; round 2: 4 (deepseek dropped) -> 9 accumulated responses
        self.assertEqual(len(result["responses"]), 9)


class TestResearchRetry(unittest.TestCase):
    def setUp(self):
        self._backoff = S._RESEARCH_RETRY_BACKOFF
        S._RESEARCH_RETRY_BACKOFF = 0  # don't sleep in tests

    def tearDown(self):
        S._RESEARCH_RETRY_BACKOFF = self._backoff

    def test_transient_panel_failure_is_retried_once_then_proceeds(self):
        import os
        import tempfile
        marker = os.path.join(tempfile.mkdtemp(), "kimi-attempt")
        panel = [{"provider": "kimi", "label": "kimi"}]
        cfg = _research_cfg(max_rounds=1, hard_cap=1, panel=panel)
        stubs = {"claude": claude_research(gaps=False),
                 "kimi-cli": flaky_text(marker, "RECOVERED")}
        with StubBins(stubs):
            result = _run(cfg)
        self.assertTrue(os.path.exists(marker))    # first (failed) attempt happened
        ok = [r for r in result["responses"] if r["ok"]]
        self.assertEqual(len(ok), 1)
        self.assertIn("RECOVERED", ok[0]["content"])


class TestResearchSeedMeters(unittest.TestCase):
    def test_clarify_meters_fold_into_cost(self):
        cfg = _research_cfg(max_rounds=1, hard_cap=1)
        seed = [{"stage": "interview", "label": "claude-opus",
                 "provider": "claude", "ok": True},
                {"stage": "dedup", "label": "dedup", "provider": "claude", "ok": True}]
        settings = dict(cfg["settings"])
        with StubBins(_full_panel_stubs(gaps=False)):
            result = asyncio.run(S.research_run(cfg, "What is X?", settings, _noop_log,
                                                seed_meters=seed))
        # brief(1) + round1 panel(5) + reflect(1) + synth(1) = 8, plus the 2 seeded = 10
        self.assertEqual(result["cost"]["calls"], 10)


class TestResearchAllFail(unittest.TestCase):
    def test_first_round_all_fail_raises(self):
        panel = [{"provider": "kimi", "label": "kimi"}]
        cfg = _research_cfg(max_rounds=1, hard_cap=1, panel=panel)
        stubs = {"claude": claude_research(gaps=False),
                 "kimi-cli": fail(code=1, stderr="boom")}
        with StubBins(stubs):
            with self.assertRaises(S.AllPanelsFailed):
                _run(cfg)


if __name__ == "__main__":
    unittest.main()
