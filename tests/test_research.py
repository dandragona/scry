"""Deep Research mode — unit tests for the pure pieces (prompts, config, helpers).

Loop/orchestration + CLI end-to-end tests live in test_research_run.py. These are
the fast, dependency-free units: prompt anchors, config defaults, and the four pure
helpers that drive the gap loop and capability routing.

Run hermetically:  python3 -m unittest discover -s tests
"""
from __future__ import annotations

import unittest

from _harness import load_scry

S = load_scry()


# --------------------------------------------------------------------------- #
# Prompts — each new research prompt must carry a distinct anchor phrase so the
# test stub router (which keys on a substring of the system prompt) can tell the
# stages apart. The anchors must not collide with the existing fusion/plan ones.
# --------------------------------------------------------------------------- #
class TestResearchPrompts(unittest.TestCase):
    def test_each_prompt_carries_its_anchor(self):
        self.assertIn("research brief", S.RESEARCH_BRIEF_SYSTEM)
        self.assertIn("deep research analyst", S.RESEARCH_PANEL_SYSTEM)
        self.assertIn("research referee", S.RESEARCH_JUDGE_SYSTEM)
        self.assertIn("research synthesis", S.RESEARCH_SYNTH_SYSTEM)

    def test_new_anchors_do_not_collide_with_existing_ones(self):
        # The stub router keys on these existing substrings; a new prompt must not
        # accidentally contain one (it would be misrouted to the wrong stage).
        existing = ["impartial judge", "scope a task", "deduplicating",
                    "plan drafts", "synthesize these responses"]
        for prompt in (S.RESEARCH_BRIEF_SYSTEM, S.RESEARCH_PANEL_SYSTEM,
                       S.RESEARCH_JUDGE_SYSTEM, S.RESEARCH_SYNTH_SYSTEM):
            for anchor in existing:
                self.assertNotIn(anchor, prompt)

    def test_anchors_are_mutually_exclusive(self):
        # Each new anchor identifies exactly one of the four new prompts.
        anchors = {
            "research brief": S.RESEARCH_BRIEF_SYSTEM,
            "deep research analyst": S.RESEARCH_PANEL_SYSTEM,
            "research referee": S.RESEARCH_JUDGE_SYSTEM,
            "research synthesis": S.RESEARCH_SYNTH_SYSTEM,
        }
        prompts = list(anchors.values())
        for anchor, owner in anchors.items():
            matches = [p for p in prompts if anchor in p]
            self.assertEqual(matches, [owner],
                             f"anchor {anchor!r} must match exactly one prompt")


# --------------------------------------------------------------------------- #
# Config — research becomes the default mode; the research block + new phases ship
# in DEFAULT_CONFIG and survive load_config's shallow merge.
# --------------------------------------------------------------------------- #
class TestResearchConfig(unittest.TestCase):
    def test_default_mode_is_research(self):
        self.assertEqual(S.DEFAULT_CONFIG["mode"], "research")

    def test_research_block_defaults(self):
        r = S.DEFAULT_CONFIG["research"]
        self.assertTrue(r["clarify"])
        self.assertEqual(r["max_rounds"], 3)
        self.assertEqual(r["hard_cap"], 5)
        self.assertTrue(r["early_exit"])
        self.assertEqual(r["sub_questions"], 6)
        self.assertEqual(r["repo_context"], "auto")

    def test_new_phases_present(self):
        for ph in ("brief", "research", "reflect"):
            self.assertIn(ph, S.DEFAULT_PHASES)
        self.assertTrue(S.DEFAULT_PHASES["research"]["web_tools"])
        self.assertFalse(S.DEFAULT_PHASES["brief"]["web_tools"])

    def test_reflect_phase_is_web_off(self):
        # The reflect judge must return strict JSON (the gaps). A live e2e showed that
        # web-on makes claude ramble unparseable prose and silently collapse the gap
        # loop to one round; web-off it returns clean JSON. Pin the decision.
        self.assertFalse(S.DEFAULT_PHASES["reflect"]["web_tools"])

    def test_load_config_backfills_research_block(self):
        # A config that predates research mode (no `research` key) still gets the
        # block backfilled, so research_run can read its keys.
        import json
        import tempfile
        import os
        d = tempfile.mkdtemp()
        path = os.path.join(d, "c.json")
        with open(path, "w") as f:
            json.dump({"mode": "research", "settings": {"effort": "high"}}, f)
        cfg = S.load_config(path)
        self.assertEqual(cfg["research"]["max_rounds"], 3)
        self.assertEqual(cfg["research"]["hard_cap"], 5)

    def test_load_config_partial_research_keeps_sibling_defaults(self):
        import json
        import tempfile
        import os
        d = tempfile.mkdtemp()
        path = os.path.join(d, "c.json")
        with open(path, "w") as f:
            json.dump({"research": {"max_rounds": 4}}, f)
        cfg = S.load_config(path)
        self.assertEqual(cfg["research"]["max_rounds"], 4)
        self.assertEqual(cfg["research"]["hard_cap"], 5)   # sibling default kept
        self.assertTrue(cfg["research"]["clarify"])


# --------------------------------------------------------------------------- #
# _has_open_gaps — does the judge's analysis still name something to chase?
# --------------------------------------------------------------------------- #
class TestHasOpenGaps(unittest.TestCase):
    def test_empty_or_none_is_no_gaps(self):
        self.assertFalse(S._has_open_gaps(None))
        self.assertFalse(S._has_open_gaps({}))

    def test_all_gap_lists_empty_is_no_gaps(self):
        self.assertFalse(S._has_open_gaps({
            "consensus": ["c"], "unique_insights": ["u"],
            "contradictions": [], "partial_coverage": [],
            "blind_spots": [], "open_questions": []}))

    def test_any_gap_list_nonempty_is_a_gap(self):
        for k in ("contradictions", "partial_coverage", "blind_spots", "open_questions"):
            self.assertTrue(S._has_open_gaps({k: ["something"]}), k)

    def test_consensus_and_unique_insights_are_not_gaps(self):
        self.assertFalse(S._has_open_gaps(
            {"consensus": ["a", "b"], "unique_insights": ["c"]}))


# --------------------------------------------------------------------------- #
# _research_should_continue — False at the hard cap; otherwise True iff gaps remain.
# --------------------------------------------------------------------------- #
class TestShouldContinue(unittest.TestCase):
    GAPS = {"open_questions": [{"question": "q", "needs_web": True}]}
    NONE = {"consensus": ["x"]}

    def test_stops_at_hard_cap_even_with_gaps(self):
        self.assertFalse(S._research_should_continue(self.GAPS, 4, 3, 5))

    def test_continues_below_cap_with_gaps(self):
        self.assertTrue(S._research_should_continue(self.GAPS, 0, 3, 5))
        self.assertTrue(S._research_should_continue(self.GAPS, 2, 3, 5))

    def test_stops_below_cap_without_gaps(self):
        self.assertFalse(S._research_should_continue(self.NONE, 1, 3, 5))


# --------------------------------------------------------------------------- #
# _route_gaps — re-fan open gaps to capable models only. Web gaps skip no-web
# providers (deepseek); reasoning gaps go to everyone.
# --------------------------------------------------------------------------- #
class TestRouteGaps(unittest.TestCase):
    def setUp(self):
        self.panel = S.DEFAULT_CONFIG["panel"]
        self.providers = S.DEFAULT_CONFIG["providers"]

    def _provs(self, tasks):
        return {m["provider"] for m, _ in tasks}

    def test_web_gap_excludes_no_web_provider(self):
        oq = [{"question": "What shipped in 2026?", "needs_web": True}]
        tasks = S._route_gaps(oq, self.panel, self.providers)
        provs = self._provs(tasks)
        self.assertNotIn("deepseek", provs)
        self.assertIn("claude", provs)
        self.assertIn("agy", provs)     # Gemini's grounding is always on
        for _, prompt in tasks:
            self.assertIn("What shipped in 2026?", prompt)

    def test_reasoning_gap_includes_every_provider(self):
        oq = [{"question": "Derive the bound.", "needs_web": False}]
        tasks = S._route_gaps(oq, self.panel, self.providers)
        self.assertEqual(self._provs(tasks),
                         {m["provider"] for m in self.panel})

    def test_mixed_gaps_route_each_kind_correctly(self):
        oq = [{"question": "WEBQ", "needs_web": True},
              {"question": "REASONQ", "needs_web": False}]
        tasks = S._route_gaps(oq, self.panel, self.providers)
        by_prov = {m["provider"]: p for m, p in tasks}
        # deepseek (no web) gets only the reasoning gap
        self.assertIn("REASONQ", by_prov["deepseek"])
        self.assertNotIn("WEBQ", by_prov["deepseek"])
        # a web-capable model gets both
        self.assertIn("WEBQ", by_prov["claude"])
        self.assertIn("REASONQ", by_prov["claude"])

    def test_no_open_questions_yields_no_tasks(self):
        self.assertEqual(S._route_gaps([], self.panel, self.providers), [])


# --------------------------------------------------------------------------- #
# _provider_web_capability — read from config (caps / agent_file / no_web marker),
# never from the panel label.
# --------------------------------------------------------------------------- #
class TestProviderWebCapability(unittest.TestCase):
    def setUp(self):
        self.providers = S.DEFAULT_CONFIG["providers"]

    def test_deepseek_is_none(self):
        self.assertEqual(
            S._provider_web_capability("deepseek", self.providers), "none")

    def test_claude_and_codex_toggle(self):
        self.assertEqual(
            S._provider_web_capability("claude", self.providers), "toggle")
        self.assertEqual(
            S._provider_web_capability("codex", self.providers), "toggle")

    def test_kimi_toggles_via_agent_file(self):
        self.assertEqual(
            S._provider_web_capability("kimi", self.providers), "toggle")

    def test_agy_grounding_is_always_on(self):
        self.assertEqual(
            S._provider_web_capability("agy", self.providers), "always")


class TestIsTransientError(unittest.TestCase):
    def test_timeout_and_empty_are_transient(self):
        self.assertTrue(S._is_transient_error(Exception("timeout after 420s")))
        self.assertTrue(S._is_transient_error(Exception("exit 1: empty output")))
        self.assertTrue(S._is_transient_error(Exception("empty stream output")))

    def test_model_and_other_errors_are_not_transient(self):
        self.assertFalse(S._is_transient_error(Exception("model error: bad request")))
        self.assertFalse(S._is_transient_error(Exception("exit 1: boom")))
        self.assertFalse(S._is_transient_error(Exception("command not found: claude")))


if __name__ == "__main__":
    unittest.main()
