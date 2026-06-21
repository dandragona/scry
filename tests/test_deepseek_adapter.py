"""Unit tests for the scry-deepseek adapter's max-tokens resolution.

DeepSeek's chat API silently caps output at 4096 tokens when max_tokens is
omitted, truncating long answers (e.g. a `scry plan` draft). resolve_max_tokens
requests the model's documented maximum instead. Pure function — no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class ResolveMaxTokensTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = h.load_scry_deepseek()

    def test_v4_pro_uses_full_max(self):
        self.assertEqual(
            self.ds.resolve_max_tokens(None, "deepseek-v4-pro"), 384000)

    def test_legacy_aliases_cap_at_8192(self):
        self.assertEqual(
            self.ds.resolve_max_tokens(None, "deepseek-chat"), 8192)
        self.assertEqual(
            self.ds.resolve_max_tokens(None, "deepseek-reasoner"), 8192)

    def test_unknown_model_falls_back(self):
        self.assertEqual(
            self.ds.resolve_max_tokens(None, "something-new"), 8192)

    def test_explicit_value_wins(self):
        self.assertEqual(
            self.ds.resolve_max_tokens(1000, "deepseek-v4-pro"), 1000)


if __name__ == "__main__":
    unittest.main()
