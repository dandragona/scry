"""Tests for scry.render_agent_file(af, web) — the hand-written YAML agent file
that scry renders for the Kimi provider to express the read-only invariant and the
Fusion web on/off setting (kimi has no argv flags for either). See scry lines
386-399 (render_agent_file) and the kimi `agent_file` record (config.json 102-115 /
scry 208-221).

We assert structure by splitting the output on newlines (no YAML parser dependency)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402

# The four mutator tools that are excluded regardless of web on/off.
EXCLUDE_ALWAYS = [
    "kimi_cli.tools.shell:Shell",
    "kimi_cli.tools.file:WriteFile",
    "kimi_cli.tools.file:StrReplaceFile",
    "kimi_cli.tools.agent:Agent",
]
# The two web tools additionally excluded when web is OFF.
EXCLUDE_WEB_OFF = [
    "kimi_cli.tools.web:SearchWeb",
    "kimi_cli.tools.web:FetchURL",
]


class TestRenderAgentFile(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.cfg = self.scry.load_config(str(h.CONFIG_JSON))
        self.af = self.cfg["providers"]["kimi"]["agent_file"]

    # ------------------------------------------------------------------ #
    # Sanity: the shipped config's af has exactly the entries we expect.
    # ------------------------------------------------------------------ #
    def test_shipped_config_af_entries(self):
        self.assertEqual(list(self.af["exclude_always"]), EXCLUDE_ALWAYS)
        self.assertEqual(list(self.af["exclude_web_off"]), EXCLUDE_WEB_OFF)

    # ------------------------------------------------------------------ #
    # web=True
    # ------------------------------------------------------------------ #
    def test_web_on_header(self):
        out = self.scry.render_agent_file(self.af, True)
        self.assertTrue(out.startswith("version: 1\nagent:\n  extend: default"))

    def test_web_on_exclude_tools_exactly_four(self):
        out = self.scry.render_agent_file(self.af, True)
        lines = out.split("\n")
        # The exclude_tools header is present.
        self.assertIn("  exclude_tools:", lines)
        # Exactly the 4 exclude_always entries appear, each as '    - "<entry>"'.
        excl_lines = [ln for ln in lines if ln.startswith('    - "')]
        self.assertEqual(excl_lines, [f'    - "{t}"' for t in EXCLUDE_ALWAYS])
        self.assertEqual(len(excl_lines), 4)

    def test_web_on_no_web_tools(self):
        out = self.scry.render_agent_file(self.af, True)
        # The 2 web tools must NOT be present when web is on.
        for t in EXCLUDE_WEB_OFF:
            self.assertNotIn(t, out)

    # ------------------------------------------------------------------ #
    # web=False
    # ------------------------------------------------------------------ #
    def test_web_off_six_entries(self):
        out = self.scry.render_agent_file(self.af, False)
        lines = out.split("\n")
        excl_lines = [ln for ln in lines if ln.startswith('    - "')]
        expected = [f'    - "{t}"' for t in (EXCLUDE_ALWAYS + EXCLUDE_WEB_OFF)]
        self.assertEqual(excl_lines, expected)
        self.assertEqual(len(excl_lines), 6)

    def test_web_off_web_tools_present(self):
        out = self.scry.render_agent_file(self.af, False)
        for t in EXCLUDE_WEB_OFF:
            self.assertIn(f'    - "{t}"', out)

    def test_web_off_header_unchanged(self):
        out = self.scry.render_agent_file(self.af, False)
        self.assertTrue(out.startswith("version: 1\nagent:\n  extend: default"))

    # ------------------------------------------------------------------ #
    # web on vs off differ by exactly the 2 web entries.
    # ------------------------------------------------------------------ #
    def test_web_off_extends_web_on(self):
        on = self.scry.render_agent_file(self.af, True)
        off = self.scry.render_agent_file(self.af, False)
        on_excl = [ln for ln in on.split("\n") if ln.startswith('    - "')]
        off_excl = [ln for ln in off.split("\n") if ln.startswith('    - "')]
        self.assertEqual(off_excl[:4], on_excl)
        self.assertEqual(off_excl[4:], [f'    - "{t}"' for t in EXCLUDE_WEB_OFF])

    # ------------------------------------------------------------------ #
    # Trailing newline.
    # ------------------------------------------------------------------ #
    def test_trailing_newline_web_on(self):
        out = self.scry.render_agent_file(self.af, True)
        self.assertTrue(out.endswith("\n"))
        # Exactly one trailing newline (single "\n".join + "\n").
        self.assertFalse(out.endswith("\n\n"))

    def test_trailing_newline_web_off(self):
        out = self.scry.render_agent_file(self.af, False)
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    # ------------------------------------------------------------------ #
    # Empty exclude lists => no "exclude_tools:" line (the `if excl:` branch).
    # ------------------------------------------------------------------ #
    def test_empty_excludes_no_exclude_tools_line(self):
        minimal = {"extend": "default", "exclude_always": [], "exclude_web_off": []}
        out = self.scry.render_agent_file(minimal, True)
        self.assertNotIn("exclude_tools", out)
        # Output is just the 3 header lines + trailing newline.
        self.assertEqual(out, "version: 1\nagent:\n  extend: default\n")

    def test_empty_excludes_web_off_still_no_exclude_tools(self):
        minimal = {"extend": "default", "exclude_always": [], "exclude_web_off": []}
        out = self.scry.render_agent_file(minimal, False)
        self.assertNotIn("exclude_tools", out)
        self.assertEqual(out, "version: 1\nagent:\n  extend: default\n")

    # ------------------------------------------------------------------ #
    # `extend` defaults to "default" when absent (af.get('extend', 'default')).
    # ------------------------------------------------------------------ #
    def test_extend_defaults_when_absent(self):
        af = {"exclude_always": [], "exclude_web_off": []}
        out = self.scry.render_agent_file(af, True)
        self.assertEqual(out, "version: 1\nagent:\n  extend: default\n")

    def test_extend_custom_value_used(self):
        af = {"extend": "custom-agent", "exclude_always": [], "exclude_web_off": []}
        out = self.scry.render_agent_file(af, True)
        self.assertIn("  extend: custom-agent", out)
        self.assertTrue(out.startswith("version: 1\nagent:\n  extend: custom-agent"))

    # ------------------------------------------------------------------ #
    # Missing keys: af.get(...) defaults to [] so no crash, no exclude_tools.
    # ------------------------------------------------------------------ #
    def test_missing_exclude_keys_no_crash(self):
        out = self.scry.render_agent_file({}, False)
        self.assertEqual(out, "version: 1\nagent:\n  extend: default\n")

    def test_exclude_always_only_when_web_on(self):
        # exclude_web_off missing -> only the always entries, even with web off.
        af = {"extend": "default", "exclude_always": ["only:One"]}
        out = self.scry.render_agent_file(af, False)
        lines = out.split("\n")
        excl_lines = [ln for ln in lines if ln.startswith('    - "')]
        self.assertEqual(excl_lines, ['    - "only:One"'])


if __name__ == "__main__":
    unittest.main()
