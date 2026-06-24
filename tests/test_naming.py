"""Artifact naming — meaningful filenames derived from the prompt.

Covers scry's pure slug helpers, the cheap LLM-title call (`topic_slug`) and its
fallback chain (driven by stub provider binaries, never a real model), and the web
artifact writers' title/collision behavior. Stdlib + the scry test harness only.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import _harness as h  # noqa: E402

scry = h.load_scry()


# --------------------------------------------------------------------------- #
# Pure slug helpers
# --------------------------------------------------------------------------- #
class SlugifyTest(unittest.TestCase):
    def test_basic_lowercases_and_dashes(self):
        self.assertEqual(scry.slugify_title("Dark Mode Toggle"), "dark-mode-toggle")

    def test_punctuation_and_runs_collapse_to_single_dash(self):
        self.assertEqual(scry.slugify_title("  Add: a 'dark-mode'  toggle!! "),
                         "add-a-dark-mode-toggle")

    def test_only_first_line_is_used(self):
        self.assertEqual(scry.slugify_title("Email Validation\nblah blah blah"),
                         "email-validation")

    def test_unicode_dropped_to_dashes(self):
        # Non-ASCII alphanumerics aren't kept (filesystem-safe ASCII slug).
        self.assertEqual(scry.slugify_title("café ☕ report"), "caf-report")

    def test_long_title_capped_without_trailing_dash(self):
        s = scry.slugify_title("word " * 40, cap=20)
        self.assertLessEqual(len(s), 20)
        self.assertFalse(s.endswith("-"))

    def test_empty_and_garbage_return_empty(self):
        self.assertEqual(scry.slugify_title(""), "")
        self.assertEqual(scry.slugify_title("   "), "")
        self.assertEqual(scry.slugify_title("!!!"), "")

    def test_first_words_slug_takes_n_words(self):
        self.assertEqual(scry._first_words_slug("make the artifact files meaningful now", n=4),
                         "make-the-artifact-files")


# --------------------------------------------------------------------------- #
# Collision-suffix helper
# --------------------------------------------------------------------------- #
class UniqueStemTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="scry-stem-"))
        self.addCleanup(__import__("shutil").rmtree, self.d, ignore_errors=True)

    def test_free_stem_returned_as_is(self):
        self.assertEqual(scry._unique_stem(self.d, "scry-plan-x", (".md",)), "scry-plan-x")

    def test_collision_increments(self):
        (self.d / "scry-plan-x.md").write_text("a")
        self.assertEqual(scry._unique_stem(self.d, "scry-plan-x", (".md",)), "scry-plan-x-2")
        (self.d / "scry-plan-x-2.md").write_text("b")
        self.assertEqual(scry._unique_stem(self.d, "scry-plan-x", (".md",)), "scry-plan-x-3")

    def test_sibling_extensions_block_a_stem(self):
        # The diagnostics sibling alone is enough to push to the next stem, so the
        # plan and its .diagnostics.md never split across different bases.
        (self.d / "scry-plan-x.diagnostics.md").write_text("d")
        self.assertEqual(
            scry._unique_stem(self.d, "scry-plan-x", (".md", ".diagnostics.md")),
            "scry-plan-x-2")


# --------------------------------------------------------------------------- #
# topic_slug — the cheap LLM title call + fallback chain (stub-driven)
# --------------------------------------------------------------------------- #
class TopicSlugTest(unittest.TestCase):
    def _cfg(self):
        cfg = scry.load_config(None)
        cfg["judge"] = {"provider": "claude", "model": ""}
        return cfg

    def test_uses_llm_title_when_available(self):
        with h.StubBins({"claude": h.claude_json("Dark Mode Toggle")}):
            slug = scry.topic_slug(self._cfg(), "please add a dark mode toggle")
        self.assertEqual(slug, "dark-mode-toggle")

    def test_falls_back_to_first_words_when_model_fails(self):
        with h.StubBins({"claude": h.fail()}):
            slug = scry.topic_slug(self._cfg(), "Make the artifact files meaningful please")
        self.assertEqual(slug, "make-the-artifact-files-meaningful-please")

    def test_falls_back_when_no_judge_provider(self):
        cfg = scry.load_config(None)
        cfg["judge"] = {}
        slug = scry.topic_slug(cfg, "Rename the output files")
        self.assertEqual(slug, "rename-the-output-files")

    def test_blank_title_falls_back_to_first_words(self):
        with h.StubBins({"claude": h.claude_json("   ")}):
            slug = scry.topic_slug(self._cfg(), "Tidy up the logging")
        self.assertEqual(slug, "tidy-up-the-logging")

    def test_title_override_routes_to_configured_model(self):
        # A `title` override (string form) wins over the judge: the deepseek stub is
        # called, the claude judge is not.
        cfg = self._cfg()
        cfg["title"] = "deepseek"
        with h.StubBins({"claude": h.claude_json("From Judge"),
                         "scry-deepseek": h.deepseek_text("From DeepSeek")}):
            slug = scry.topic_slug(cfg, "whatever")
        self.assertEqual(slug, "from-deepseek")

    def test_empty_title_override_falls_back_to_judge(self):
        cfg = self._cfg()
        cfg["title"] = {}                                # configured but empty -> judge
        with h.StubBins({"claude": h.claude_json("Judge Title")}):
            slug = scry.topic_slug(cfg, "whatever")
        self.assertEqual(slug, "judge-title")


class TitleMemberResolutionTest(unittest.TestCase):
    def test_title_string_is_parsed(self):
        self.assertEqual(
            scry._title_member({"title": "deepseek:chat", "judge": {"provider": "claude"}}),
            {"provider": "deepseek", "model": "chat"})

    def test_title_dict_used_as_is(self):
        self.assertEqual(
            scry._title_member({"title": {"provider": "glm", "model": ""}}),
            {"provider": "glm", "model": ""})

    def test_defaults_to_judge_when_no_title(self):
        self.assertEqual(scry._title_member({"judge": {"provider": "claude"}}),
                         {"provider": "claude"})

    def test_empty_title_defaults_to_judge(self):
        self.assertEqual(scry._title_member({"title": {}, "judge": {"provider": "claude"}}),
                         {"provider": "claude"})

    def test_nothing_configured_returns_empty(self):
        self.assertEqual(scry._title_member({}), {})


class TitleSettingsRenderTest(unittest.TestCase):
    """The title call must render the CHEAPEST reasoning effort + web OFF + a tiny
    output cap — assert it through scry's own render_call on the claude provider."""

    def test_render_uses_low_effort_and_web_off(self):
        cfg = scry.load_config(None)
        p = cfg["providers"]["claude"]
        settings = {"web_tools": False, "max_tool_calls": 0, "effort": "low",
                    "max_output_tokens": 32, "timeout": 30}
        argv, _ = scry.render_call(p, "", scry._TITLE_SYSTEM, False, settings, "")
        # Cheapest reasoning effort.
        self.assertIn("low", argv)
        self.assertIn(scry._TITLE_SYSTEM, argv)
        # Web OFF: the web-on argv (search enabled) must differ from this one.
        web_argv, _ = scry.render_call(p, "", scry._TITLE_SYSTEM, True, settings, "")
        self.assertNotEqual(argv, web_argv)


# --------------------------------------------------------------------------- #
# Web artifact writers — title-based names + collision suffix
# --------------------------------------------------------------------------- #
class WebArtifactNamingTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="scry-web-home-")
        self._old = os.environ.get("SCRY_WEB_HOME")
        os.environ["SCRY_WEB_HOME"] = self.home
        from scry_web import artifacts
        self.artifacts = artifacts

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SCRY_WEB_HOME", None)
        else:
            os.environ["SCRY_WEB_HOME"] = self._old

    def test_chat_uses_title_slug(self):
        ap = self.artifacts.write_chat({}, "conv1", "r1", "q", "a",
                                       title="email-validation")
        self.assertEqual(Path(ap).name, "scry-chat-email-validation.md")

    def test_research_uses_title_slug(self):
        ap = self.artifacts.write_research({}, "conv1", "r1", "q", "a",
                                           title="llm-eval-benchmarks")
        self.assertEqual(Path(ap).name, "scry-research-llm-eval-benchmarks.md")

    def test_collision_appends_suffix(self):
        a1 = self.artifacts.write_chat({}, "conv1", "r1", "q", "a", title="same-topic")
        a2 = self.artifacts.write_chat({}, "conv1", "r2", "q", "a", title="same-topic")
        self.assertEqual(Path(a1).name, "scry-chat-same-topic.md")
        self.assertEqual(Path(a2).name, "scry-chat-same-topic-2.md")

    def test_missing_title_falls_back_to_run_id(self):
        ap = self.artifacts.write_chat({}, "conv1", "r9", "q", "a")
        self.assertEqual(Path(ap).name, "scry-chat-r9.md")


if __name__ == "__main__":
    unittest.main()
