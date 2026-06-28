"""Tests for scry's cross-vendor API-key scrubbing.

scry copies its environment to every child CLI. A user who `export`s one vendor's
key (e.g. DEEPSEEK_API_KEY) must NOT have it handed to every other vendor's
subprocess (claude/codex/agy/kimi/glm). _keys_to_scrub computes which credential
vars to strip from each provider's child — every managed provider key EXCEPT the
one that provider legitimately needs — and call_cli applies it.
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class TestKeysToScrub(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.cfg = self.scry.load_config(str(h.CONFIG_JSON))
        self.providers = self.cfg["providers"]

    def test_deepseek_keeps_own_key_scrubs_others(self):
        scrub = self.scry._keys_to_scrub(self.providers["deepseek"], self.cfg)
        self.assertNotIn("DEEPSEEK_API_KEY", scrub)      # it needs this one
        self.assertIn("GLM_API_KEY", scrub)
        self.assertIn("ANTHROPIC_API_KEY", scrub)
        self.assertIn("KIMI_API_KEY", scrub)

    def test_glm_keeps_own_key_scrubs_others(self):
        scrub = self.scry._keys_to_scrub(self.providers["glm"], self.cfg)
        self.assertNotIn("GLM_API_KEY", scrub)
        self.assertIn("DEEPSEEK_API_KEY", scrub)
        self.assertIn("ANTHROPIC_API_KEY", scrub)

    def test_subscription_provider_scrubs_all_keys(self):
        # claude needs no API key (subscription) -> every managed provider key is
        # scrubbed, including its own ANTHROPIC_API_KEY (force subscription auth).
        scrub = self.scry._keys_to_scrub(self.providers["claude"], self.cfg)
        for k in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GLM_API_KEY", "KIMI_API_KEY"):
            self.assertIn(k, scrub)

    def test_kimi_still_scrubs_its_own_key(self):
        # kimi declared env_unset KIMI_API_KEY (force OAuth) -> still scrubbed,
        # plus the cross-vendor keys.
        scrub = self.scry._keys_to_scrub(self.providers["kimi"], self.cfg)
        self.assertIn("KIMI_API_KEY", scrub)
        self.assertIn("DEEPSEEK_API_KEY", scrub)


class TestCallCliScrubsEnv(unittest.TestCase):
    def test_other_vendor_key_not_passed_to_child(self):
        scry = h.load_scry()
        cfg = scry.load_config(str(h.CONFIG_JSON))
        # A scry-deepseek stub that reports which keys it can see in its env.
        report = (
            "import os,sys\n"
            "sys.stdin.read()\n"
            "sys.stdout.write('DS=%s GLM=%s ANTH=%s' % (\n"
            "  'Y' if os.environ.get('DEEPSEEK_API_KEY') else 'N',\n"
            "  'Y' if os.environ.get('GLM_API_KEY') else 'N',\n"
            "  'Y' if os.environ.get('ANTHROPIC_API_KEY') else 'N'))\n"
        )
        cwd = tempfile.mkdtemp(prefix="scry-test-scrub-")
        with h.env_vars(DEEPSEEK_API_KEY="ds-key", GLM_API_KEY="glm-key",
                        ANTHROPIC_API_KEY="anth-key"):
            with h.StubBins({"scry-deepseek": h._py(report)}):
                out = asyncio.run(scry.call_cli(
                    cfg, "deepseek", cfg["providers"]["deepseek"].get("model", ""),
                    None, "hi", cwd, 0, False, cfg["settings"]))
        # deepseek keeps its own key but the cross-vendor keys are stripped.
        self.assertIn("DS=Y", out)
        self.assertIn("GLM=N", out)
        self.assertIn("ANTH=N", out)


if __name__ == "__main__":
    unittest.main()
