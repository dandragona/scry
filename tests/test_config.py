"""Tests for scry.load_config / scry.parse_member / scry.parse_panel.

Covers config loading (defaults, explicit-path merge, settings backfill, bad-JSON
tolerance) and resolution precedence (--config > project-local ./scry.config.json >
global ~/.config/scry/config.json > built-in defaults, with a stray ./config.json
deliberately ignored), plus the panel/member spec parsers. Stdlib-only; never
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


@contextlib.contextmanager
def _isolated_config_env():
    """Run load_config(None) against an empty HOME + cwd so it ignores any real
    machine config (~/.config/scry/config.json or ./scry.config.json). Without this,
    a developer box that has run `scry init` fails the default-equality assertions
    below (e.g. a customized panel length) — these tests assert the built-in defaults."""
    home = tempfile.mkdtemp(prefix="scry-home-")
    cwd = tempfile.mkdtemp(prefix="scry-cwd-")
    saved = os.getcwd()
    try:
        with h.env_vars(HOME=home):
            os.chdir(cwd)
            yield
    finally:
        os.chdir(saved)
        _rmtree(home)
        _rmtree(cwd)


class TestLoadConfigDefaults(unittest.TestCase):
    def setUp(self):
        # load_config(None) reads the machine's global/local config, so isolate to an
        # empty HOME + cwd; otherwise a configured dev box fails these default checks.
        iso = _isolated_config_env()
        iso.__enter__()
        self.addCleanup(iso.__exit__, None, None, None)

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


class TestLoadConfigResolution(unittest.TestCase):
    """Precedence: --config > ./scry.config.json > ~/.config/scry/config.json >
    defaults. A stray ./config.json (a very common filename) is deliberately NOT
    read — it would otherwise be silently merged into scry's config."""

    def setUp(self):
        self.scry = h.load_scry()
        self.cwd = tempfile.mkdtemp(prefix="scry-cwd-")
        self.addCleanup(_rmtree, self.cwd)
        self.home = tempfile.mkdtemp(prefix="scry-home-")
        self.addCleanup(_rmtree, self.home)
        self.global_path = os.path.join(self.home, ".config", "scry", "config.json")

    def _write_local(self, obj):
        with open(os.path.join(self.cwd, self.scry.LOCAL_CONFIG_NAME), "w") as f:
            f.write(json.dumps(obj))

    def _write_generic(self, obj):
        with open(os.path.join(self.cwd, "config.json"), "w") as f:
            f.write(json.dumps(obj))

    def _write_global(self, obj):
        os.makedirs(os.path.dirname(self.global_path), exist_ok=True)
        with open(self.global_path, "w") as f:
            f.write(json.dumps(obj))

    def _load(self, path=None):
        """Load with cwd=self.cwd and HOME=self.home active."""
        saved_cwd = os.getcwd()
        with h.env_vars(HOME=self.home):
            try:
                os.chdir(self.cwd)
                return self.scry.load_config(path)
            finally:
                os.chdir(saved_cwd)

    def test_global_config_path_honors_home(self):
        with h.env_vars(HOME=self.home):
            self.assertEqual(str(self.scry.global_config_path()), self.global_path)
        self.assertEqual(self.scry.LOCAL_CONFIG_NAME, "scry.config.json")

    def test_reads_global_config_when_no_local(self):
        self._write_global({"mode": "GLOBAL_MODE"})
        cfg = self._load()
        self.assertEqual(cfg["mode"], "GLOBAL_MODE")
        # Settings still fully backfilled.
        for k in self.scry.DEFAULT_SETTINGS:
            self.assertIn(k, cfg["settings"])

    def test_ignores_generic_cwd_config_json(self):
        # A generic ./config.json belongs to some OTHER tool — must NOT be loaded.
        self._write_generic({"mode": "GENERIC_SHOULD_BE_IGNORED"})
        self._write_global({"mode": "GLOBAL_MODE"})
        cfg = self._load()
        self.assertEqual(cfg["mode"], "GLOBAL_MODE",
                         "a stray ./config.json must not shadow the global config")

    def test_local_scry_config_is_read(self):
        self._write_local({"mode": "LOCAL_MODE"})
        cfg = self._load()
        self.assertEqual(cfg["mode"], "LOCAL_MODE")

    def test_local_overrides_global(self):
        self._write_global({"mode": "GLOBAL_MODE", "settings": {"effort": "low"}})
        self._write_local({"mode": "LOCAL_MODE"})
        cfg = self._load()
        self.assertEqual(cfg["mode"], "LOCAL_MODE")
        # First hit wins entirely — the global file is not consulted at all, so its
        # settings.effort does not leak in (only DEFAULT_SETTINGS backfills).
        self.assertEqual(cfg["settings"]["effort"], self.scry.DEFAULT_SETTINGS["effort"])

    def test_partial_local_override_keeps_default_providers(self):
        self._write_local({"mode": "fusion", "judge": {"provider": "codex", "model": ""}})
        cfg = self._load()
        self.assertEqual(cfg["judge"]["provider"], "codex")
        self.assertIn("claude", cfg["providers"])  # providers survive a partial override

    def test_explicit_config_beats_local_and_global(self):
        self._write_global({"mode": "GLOBAL_MODE"})
        self._write_local({"mode": "LOCAL_MODE"})
        explicit = os.path.join(self.cwd, "explicit.json")
        with open(explicit, "w") as f:
            f.write(json.dumps({"mode": "EXPLICIT_MODE"}))
        cfg = self._load(explicit)
        self.assertEqual(cfg["mode"], "EXPLICIT_MODE")

    def test_no_config_anywhere_yields_defaults(self):
        cfg = self._load()
        self.assertEqual(cfg["mode"], self.scry.DEFAULT_CONFIG["mode"])
        self.assertEqual(cfg["settings"], dict(self.scry.DEFAULT_SETTINGS))


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


class TestLoadConfigPlanBlock(unittest.TestCase):
    """`scry plan` reads a top-level `plan` block; load_config backfills it from
    DEFAULT_CONFIG['plan'] like it does for settings, so old configs still work."""

    def setUp(self):
        # test_none_has_plan_defaults calls load_config(None); isolate HOME + cwd so a
        # configured dev box can't override the plan defaults this asserts. (Harmless
        # for the explicit-path tests here, which read absolute temp paths.)
        iso = _isolated_config_env()
        iso.__enter__()
        self.addCleanup(iso.__exit__, None, None, None)

    def _write(self, obj) -> str:
        d = tempfile.mkdtemp(prefix="scry-cfg-")
        self.addCleanup(_rmtree, d)
        p = os.path.join(d, "config.json")
        with open(p, "w") as f:
            f.write(json.dumps(obj))
        return p

    def test_none_has_plan_defaults(self):
        scry = h.load_scry()
        cfg = scry.load_config(None)
        self.assertEqual(cfg["plan"]["max_rounds"], 6)
        self.assertIs(cfg["plan"]["repo_context"], True)      # panel reads the repo

    def test_partial_plan_override_keeps_other_new_keys(self):
        scry = h.load_scry()
        p = self._write({"plan": {"repo_context": False}})
        cfg = scry.load_config(p)
        self.assertIs(cfg["plan"]["repo_context"], False)        # overridden
        self.assertEqual(cfg["plan"]["max_rounds"], 6)           # backfilled

    def test_config_without_plan_key_is_backfilled(self):
        scry = h.load_scry()
        p = self._write({"mode": "fusion"})  # no "plan" key at all
        cfg = scry.load_config(p)
        self.assertEqual(cfg["plan"]["max_rounds"], 6)
        self.assertIs(cfg["plan"]["repo_context"], True)

    def test_partial_plan_override_backfills_missing_keys(self):
        scry = h.load_scry()
        p = self._write({"plan": {"max_rounds": 3}})
        cfg = scry.load_config(p)
        self.assertEqual(cfg["plan"]["max_rounds"], 3)        # kept
        self.assertIs(cfg["plan"]["repo_context"], True)      # backfilled

    def test_plan_block_does_not_leak_into_settings_equality(self):
        # Adding the plan block must NOT disturb the settings == DEFAULT_SETTINGS
        # contract other tests rely on (plan lives at top level, not in settings).
        scry = h.load_scry()
        p = self._write({"plan": {"max_rounds": 9}})
        cfg = scry.load_config(p)
        self.assertEqual(cfg["settings"], dict(scry.DEFAULT_SETTINGS))


class TestLoadConfigPhases(unittest.TestCase):
    """load_config backfills the top-level `phases` block from DEFAULT_PHASES, merging a
    partial user phase on top of its default and ignoring a non-dict (e.g. a `_note`)."""

    def setUp(self):
        iso = _isolated_config_env()
        iso.__enter__()
        self.addCleanup(iso.__exit__, None, None, None)

    def _write(self, obj) -> str:
        d = tempfile.mkdtemp(prefix="scry-cfg-")
        self.addCleanup(_rmtree, d)
        p = os.path.join(d, "config.json")
        with open(p, "w") as f:
            f.write(json.dumps(obj))
        return p

    def test_none_has_phase_defaults(self):
        scry = h.load_scry()
        ph = scry.load_config(None)["phases"]
        self.assertEqual(set(ph), set(scry.DEFAULT_PHASES))
        self.assertIs(ph["synthesis"]["web_tools"], False)
        self.assertIs(ph["interview"]["web_tools"], False)
        self.assertEqual(ph["final"]["max_tool_calls"], 24)
        self.assertEqual(ph["final"]["timeout"], 2100)

    def test_partial_phase_override_keeps_sibling_defaults(self):
        scry = h.load_scry()
        p = self._write({"phases": {"judge": {"web_tools": False}}})
        ph = scry.load_config(p)["phases"]
        self.assertIs(ph["judge"]["web_tools"], False)          # overridden
        self.assertEqual(ph["final"]["max_tool_calls"], 24)     # sibling default kept
        self.assertEqual(ph["synthesis"], {"web_tools": False})

    def test_non_dict_phase_value_ignored(self):
        scry = h.load_scry()
        p = self._write({"phases": {"_note": "explain", "judge": {"max_tool_calls": 4}}})
        ph = scry.load_config(p)["phases"]
        self.assertNotIn("_note", ph)
        self.assertEqual(ph["judge"]["max_tool_calls"], 4)

    def test_unknown_phase_preserved(self):
        scry = h.load_scry()
        p = self._write({"phases": {"custom": {"timeout": 99}}})
        ph = scry.load_config(p)["phases"]
        self.assertEqual(ph["custom"], {"timeout": 99})


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
