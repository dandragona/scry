"""Tests for scry.load_config / scry.parse_member / scry.parse_panel.

Covers config loading (defaults, explicit-path merge, settings backfill, bad-JSON
tolerance, cwd discovery) and the panel/member spec parsers. Stdlib-only; never
invokes a real model CLI (config parsing touches no subprocesses)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class TestLoadConfigDefaults(unittest.TestCase):
    def test_none_returns_deepcopy_of_default_config(self):
        scry = h.load_scry()
        cfg = scry.load_config(None)
        # Same content as DEFAULT_CONFIG...
        self.assertEqual(cfg["mode"], scry.DEFAULT_CONFIG["mode"])
        self.assertEqual(cfg["providers"].keys(), scry.DEFAULT_CONFIG["providers"].keys())
        # ...but a DISTINCT object (deepcopy), not the module global.
        self.assertIsNot(cfg, scry.DEFAULT_CONFIG)
        self.assertIsNot(cfg["providers"], scry.DEFAULT_CONFIG["providers"])
        self.assertIsNot(cfg["panel"], scry.DEFAULT_CONFIG["panel"])
        # Mutating the returned cfg must not bleed into the module default.
        cfg["mode"] = "MUTATED"
        cfg["panel"].append({"provider": "x"})
        self.assertEqual(scry.DEFAULT_CONFIG["mode"], "fusion")
        self.assertEqual(
            len(scry.DEFAULT_CONFIG["panel"]),
            len(scry.load_config(None)["panel"]),
        )

    def test_none_backfills_settings_from_default_settings(self):
        scry = h.load_scry()
        cfg = scry.load_config(None)
        # Every DEFAULT_SETTINGS key is present.
        for k, v in scry.DEFAULT_SETTINGS.items():
            self.assertIn(k, cfg["settings"])
            self.assertEqual(cfg["settings"][k], v)

    def test_none_settings_is_a_distinct_dict(self):
        scry = h.load_scry()
        cfg = scry.load_config(None)
        # settings is rebuilt ({**DEFAULT_SETTINGS, ...}) so it's not the global.
        self.assertIsNot(cfg["settings"], scry.DEFAULT_SETTINGS)
        cfg["settings"]["effort"] = "ultra"
        self.assertIsNone(scry.DEFAULT_SETTINGS["effort"])


class TestLoadConfigExplicitPath(unittest.TestCase):
    def _write(self, obj) -> str:
        d = tempfile.mkdtemp(prefix="scry-cfg-")
        self.addCleanup(_rmtree, d)
        p = os.path.join(d, "config.json")
        with open(p, "w") as f:
            f.write(json.dumps(obj))
        return p

    def _write_raw(self, text) -> str:
        d = tempfile.mkdtemp(prefix="scry-cfg-")
        self.addCleanup(_rmtree, d)
        p = os.path.join(d, "config.json")
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_explicit_path_merges_over_defaults(self):
        scry = h.load_scry()
        p = self._write({"mode": "synthesize", "settings": {"effort": "high"}})
        cfg = scry.load_config(p)
        # Top-level override applied.
        self.assertEqual(cfg["mode"], "synthesize")
        # Settings override applied.
        self.assertEqual(cfg["settings"]["effort"], "high")

    def test_explicit_path_partial_settings_backfilled(self):
        scry = h.load_scry()
        p = self._write({"settings": {"effort": "high"}})
        cfg = scry.load_config(p)
        # The overridden key.
        self.assertEqual(cfg["settings"]["effort"], "high")
        # Keys NOT in the file are still backfilled from DEFAULT_SETTINGS.
        self.assertEqual(
            cfg["settings"]["web_tools"], scry.DEFAULT_SETTINGS["web_tools"]
        )
        self.assertEqual(
            cfg["settings"]["max_tool_calls"], scry.DEFAULT_SETTINGS["max_tool_calls"]
        )
        self.assertIn("max_output_tokens", cfg["settings"])

    def test_explicit_path_toplevel_key_replaces_default(self):
        scry = h.load_scry()
        # cfg.update() does a shallow replace of the whole top-level value.
        p = self._write({"panel": [{"provider": "kimi", "model": "k2", "label": "z"}]})
        cfg = scry.load_config(p)
        self.assertEqual(len(cfg["panel"]), 1)
        self.assertEqual(cfg["panel"][0]["provider"], "kimi")
        # Untouched top-level keys keep their defaults.
        self.assertEqual(cfg["mode"], scry.DEFAULT_CONFIG["mode"])
        self.assertIn("claude", cfg["providers"])

    def test_explicit_path_no_settings_key_yields_full_defaults(self):
        scry = h.load_scry()
        p = self._write({"mode": "fusion"})
        cfg = scry.load_config(p)
        self.assertEqual(cfg["settings"], dict(scry.DEFAULT_SETTINGS))

    def test_bad_json_does_not_raise_and_returns_defaults(self):
        scry = h.load_scry()
        p = self._write_raw("{ this is : not valid json,,, ")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            cfg = scry.load_config(p)  # must NOT raise
        # Falls back to defaults.
        self.assertEqual(cfg["mode"], scry.DEFAULT_CONFIG["mode"])
        # Settings still complete.
        for k in scry.DEFAULT_SETTINGS:
            self.assertIn(k, cfg["settings"])
        # A warning was emitted to stderr.
        self.assertIn("warning", buf.getvalue().lower())

    def test_nonexistent_explicit_path_returns_defaults(self):
        scry = h.load_scry()
        d = tempfile.mkdtemp(prefix="scry-cfg-")
        self.addCleanup(_rmtree, d)
        missing = os.path.join(d, "does-not-exist.json")
        # c.exists() is False -> loop falls through, no error, defaults returned.
        cfg = scry.load_config(missing)
        self.assertEqual(cfg["mode"], scry.DEFAULT_CONFIG["mode"])
        self.assertEqual(cfg["settings"], dict(scry.DEFAULT_SETTINGS))


class TestLoadConfigCwdDiscovery(unittest.TestCase):
    def test_picks_up_cwd_config_json(self):
        scry = h.load_scry()
        cwd_dir = tempfile.mkdtemp(prefix="scry-cwd-")
        self.addCleanup(_rmtree, cwd_dir)
        home_dir = tempfile.mkdtemp(prefix="scry-home-")
        self.addCleanup(_rmtree, home_dir)
        # Sentinel config in the cwd directory.
        with open(os.path.join(cwd_dir, "config.json"), "w") as f:
            f.write(json.dumps({"mode": "SENTINEL_CWD_MODE"}))

        saved_cwd = os.getcwd()
        # HOME empty so the ~/.config/scry/config.json candidate doesn't exist.
        with h.env_vars(HOME=home_dir):
            try:
                os.chdir(cwd_dir)
                cfg = scry.load_config(None)
            finally:
                os.chdir(saved_cwd)
        self.assertEqual(cfg["mode"], "SENTINEL_CWD_MODE")
        # Settings still fully backfilled.
        for k in scry.DEFAULT_SETTINGS:
            self.assertIn(k, cfg["settings"])

    def test_no_cwd_or_home_config_yields_defaults(self):
        scry = h.load_scry()
        empty_cwd = tempfile.mkdtemp(prefix="scry-cwd-empty-")
        self.addCleanup(_rmtree, empty_cwd)
        home_dir = tempfile.mkdtemp(prefix="scry-home-empty-")
        self.addCleanup(_rmtree, home_dir)

        saved_cwd = os.getcwd()
        with h.env_vars(HOME=home_dir):
            try:
                os.chdir(empty_cwd)
                cfg = scry.load_config(None)
            finally:
                os.chdir(saved_cwd)
        self.assertEqual(cfg["mode"], scry.DEFAULT_CONFIG["mode"])


class TestParseMember(unittest.TestCase):
    def test_provider_and_model(self):
        scry = h.load_scry()
        self.assertEqual(
            scry.parse_member("claude:opus"),
            {"provider": "claude", "model": "opus"},
        )

    def test_provider_only_empty_model(self):
        scry = h.load_scry()
        self.assertEqual(
            scry.parse_member("codex"),
            {"provider": "codex", "model": ""},
        )

    def test_trims_surrounding_and_internal_whitespace(self):
        scry = h.load_scry()
        self.assertEqual(
            scry.parse_member("  claude : opus "),
            {"provider": "claude", "model": "opus"},
        )

    def test_partitions_on_first_colon(self):
        scry = h.load_scry()
        self.assertEqual(
            scry.parse_member("a:b:c"),
            {"provider": "a", "model": "b:c"},
        )

    def test_empty_string(self):
        scry = h.load_scry()
        self.assertEqual(
            scry.parse_member(""),
            {"provider": "", "model": ""},
        )

    def test_trailing_colon_yields_empty_model(self):
        scry = h.load_scry()
        self.assertEqual(
            scry.parse_member("claude:"),
            {"provider": "claude", "model": ""},
        )


class TestParsePanel(unittest.TestCase):
    def test_three_members_with_labels(self):
        scry = h.load_scry()
        panel = scry.parse_panel("claude:opus,codex,kimi:kimi-k2.6")
        self.assertEqual(len(panel), 3)
        self.assertEqual(
            panel[0],
            {"provider": "claude", "model": "opus", "label": "claude-opus"},
        )
        # Empty model => label omits the dash.
        self.assertEqual(
            panel[1],
            {"provider": "codex", "model": "", "label": "codex"},
        )
        self.assertEqual(
            panel[2],
            {
                "provider": "kimi",
                "model": "kimi-k2.6",
                "label": "kimi-kimi-k2.6",
            },
        )

    def test_blank_and_whitespace_items_skipped(self):
        scry = h.load_scry()
        panel = scry.parse_panel("claude:opus, ,  ,codex")
        self.assertEqual([m["provider"] for m in panel], ["claude", "codex"])

    def test_trailing_comma_ignored(self):
        scry = h.load_scry()
        panel = scry.parse_panel("claude:opus,codex,")
        self.assertEqual(len(panel), 2)
        self.assertEqual([m["provider"] for m in panel], ["claude", "codex"])

    def test_leading_comma_ignored(self):
        scry = h.load_scry()
        panel = scry.parse_panel(",claude:opus")
        self.assertEqual(len(panel), 1)
        self.assertEqual(panel[0]["provider"], "claude")

    def test_label_omits_dash_when_model_empty(self):
        scry = h.load_scry()
        panel = scry.parse_panel("codex")
        self.assertEqual(panel, [{"provider": "codex", "model": "", "label": "codex"}])

    def test_empty_spec_yields_empty_panel(self):
        scry = h.load_scry()
        self.assertEqual(scry.parse_panel(""), [])
        self.assertEqual(scry.parse_panel("   "), [])
        self.assertEqual(scry.parse_panel(",,,"), [])

    def test_single_item_whitespace_trimmed(self):
        scry = h.load_scry()
        panel = scry.parse_panel("  claude : opus  ")
        self.assertEqual(
            panel,
            [{"provider": "claude", "model": "opus", "label": "claude-opus"}],
        )


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
