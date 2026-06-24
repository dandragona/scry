"""Unit tests for the scry-glm adapter's pure request-shaping helpers.

scry-glm calls Zhipu/Z.ai's OpenAI-compatible chat API. Three pure functions shape
the request body, all testable with no network:

  * resolve_max_tokens — GLM's API silently caps output low when max_tokens is
    omitted; we request the model's documented maximum (glm-5.2: 128K) instead.
  * thinking_payload   — top-level reasoning_effort + thinking:{type:enabled} for
    thinking-capable models (GLM-5.2 always thinks when an effort is requested).
  * web_search_payload — GLM has a BUILT-IN web_search tool (unlike DeepSeek);
    when web is on we inject it into the request's `tools` array, else omit it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class ResolveMaxTokensTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.glm = h.load_scry_glm()

    def test_glm_5_2_uses_full_max(self):
        # glm-5.2 documents ~128K (131072) max output.
        self.assertEqual(
            self.glm.resolve_max_tokens(None, "glm-5.2"), 131072)

    def test_unknown_model_falls_back_to_default(self):
        # Unknown ids fall back to the safe documented ceiling (131072), never the
        # API's silent low default.
        self.assertEqual(
            self.glm.resolve_max_tokens(None, "something-new"), 131072)

    def test_explicit_value_wins(self):
        self.assertEqual(
            self.glm.resolve_max_tokens(2000, "glm-5.2"), 2000)


class ThinkingPayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.glm = h.load_scry_glm()

    def test_glm_5_2_max_enables_thinking(self):
        self.assertEqual(
            self.glm.thinking_payload("glm-5.2", "max"),
            {"reasoning_effort": "max", "thinking": {"type": "enabled"}})

    def test_arbitrary_effort_forwarded(self):
        # The adapter forwards the effort string as-is (GLM accepts high/max/etc).
        self.assertEqual(
            self.glm.thinking_payload("glm-5.2", "high"),
            {"reasoning_effort": "high", "thinking": {"type": "enabled"}})

    def test_no_effort_returns_empty(self):
        self.assertEqual(self.glm.thinking_payload("glm-5.2", None), {})

    def test_empty_string_effort_returns_empty(self):
        self.assertEqual(self.glm.thinking_payload("glm-5.2", ""), {})


class WebSearchPayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.glm = h.load_scry_glm()

    def test_web_off_returns_empty(self):
        self.assertEqual(self.glm.web_search_payload(False), {})

    def test_web_on_injects_web_search_tool(self):
        payload = self.glm.web_search_payload(True)
        # The tool rides in the request's `tools` array.
        self.assertIn("tools", payload)
        tools = payload["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["type"], "web_search")
        # Built-in search is explicitly enabled (GLM defaults it off otherwise).
        self.assertIs(tools[0]["web_search"]["enable"], True)


if __name__ == "__main__":
    unittest.main()
