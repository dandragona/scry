"""Unit tests for scry.render_call and scry.append_prompt_arg.

render_call(p, model, system, web, settings, outfile, stream=False, agentfile="")
returns (argv, env_overrides). It applies Fusion knobs (web on/off tool policy,
tool-call cap, effort, max_output_tokens, streaming) per-provider from the
provider/caps records. These tests drive it directly against the shipped
config.json (loaded via scry.load_config) and assert the exact argv/env wiring
for each provider, with NO subprocess and NO model CLI ever invoked.
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


# --------------------------------------------------------------------------- #
# Local helpers (kept inside this file per the "no shared helpers" rule).
# --------------------------------------------------------------------------- #
def _adjacent(argv, flag, value):
    """True iff `flag` appears immediately followed by `value` somewhere in argv."""
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv) and argv[i + 1] == value:
            return True
    return False


def _contains_seq(argv, seq):
    """True iff the list `seq` appears as a contiguous sublist of argv."""
    n = len(seq)
    if n == 0:
        return True
    for i in range(len(argv) - n + 1):
        if argv[i:i + n] == seq:
            return True
    return False


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scry = h.load_scry()
        cls.cfg = cls.scry.load_config(str(h.CONFIG_JSON))

    def prov(self, name):
        # deepcopy so a test can't mutate the cached config's provider record.
        return copy.deepcopy(self.cfg["providers"][name])

    def settings(self, **overrides):
        s = copy.deepcopy(self.cfg["settings"])
        s.update(overrides)
        return s


# --------------------------------------------------------------------------- #
# claude
# --------------------------------------------------------------------------- #
class TestClaude(_Base):
    def test_web_on_full_wiring(self):
        p = self.prov("claude")
        argv, env = self.scry.render_call(
            p, "opus", "SYS", True, self.settings(), "/tmp/out")
        # model flag pair
        self.assertTrue(_adjacent(argv, "--model", "opus"))
        # buffered json output (not streaming)
        self.assertTrue(_contains_seq(argv, ["--output-format", "json"]))
        # web ON allowed tools + mutator disallow
        self.assertTrue(_contains_seq(
            argv, ["--allowedTools", "WebSearch,WebFetch,Read,Grep,Glob"]))
        self.assertTrue(_contains_seq(
            argv, ["--disallowedTools", "Bash Edit Write NotebookEdit"]))
        # tool cap from settings.max_tool_calls=8 (config default)
        self.assertTrue(_contains_seq(argv, ["--max-turns", "8"]))
        # system flag pair (system="SYS")
        self.assertTrue(_adjacent(argv, "--append-system-prompt", "SYS"))
        # no env overrides by default (max_output_tokens is null)
        self.assertEqual(env, {})

    def test_system_omitted_when_none(self):
        p = self.prov("claude")
        argv, _ = self.scry.render_call(
            p, "opus", None, True, self.settings(), "/tmp/out")
        self.assertNotIn("--append-system-prompt", argv)

    def test_web_off(self):
        p = self.prov("claude")
        argv, _ = self.scry.render_call(
            p, "opus", "SYS", False, self.settings(), "/tmp/out")
        # web OFF disallow list present
        self.assertTrue(_contains_seq(
            argv,
            ["--disallowedTools",
             "Bash Edit Write NotebookEdit WebFetch WebSearch"]))
        # web_on allowedTools and mutator_disallow ABSENT when web off
        self.assertNotIn("--allowedTools", argv)
        self.assertFalse(_contains_seq(
            argv, ["--disallowedTools", "Bash Edit Write NotebookEdit"]))
        # tool cap only applies when web is on
        self.assertNotIn("--max-turns", argv)

    def test_effort_present_when_set(self):
        p = self.prov("claude")
        argv, _ = self.scry.render_call(
            p, "opus", None, True, self.settings(effort="high"), "/tmp/out")
        self.assertTrue(_contains_seq(argv, ["--effort", "high"]))

    def test_effort_absent_by_default(self):
        p = self.prov("claude")
        argv, _ = self.scry.render_call(
            p, "opus", None, True, self.settings(), "/tmp/out")
        self.assertNotIn("--effort", argv)

    def test_max_output_tokens_env_override(self):
        p = self.prov("claude")
        argv, env = self.scry.render_call(
            p, "opus", None, True, self.settings(max_output_tokens=2048),
            "/tmp/out")
        self.assertEqual(env.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"), "2048")

    def test_max_output_tokens_env_empty_by_default(self):
        p = self.prov("claude")
        _, env = self.scry.render_call(
            p, "opus", None, True, self.settings(), "/tmp/out")
        self.assertEqual(env, {})

    def test_stream_replaces_json(self):
        p = self.prov("claude")
        argv, _ = self.scry.render_call(
            p, "opus", None, False, self.settings(), "/tmp/out", stream=True)
        # streaming args present
        self.assertTrue(_contains_seq(
            argv,
            ["--output-format", "stream-json",
             "--include-partial-messages", "--verbose"]))
        # buffered json output flag ABSENT (stream replaces it)
        self.assertFalse(_contains_seq(argv, ["--output-format", "json"]))

    def test_empty_model_no_model_flag(self):
        p = self.prov("claude")
        argv, _ = self.scry.render_call(
            p, "", None, True, self.settings(), "/tmp/out")
        self.assertNotIn("--model", argv)


# --------------------------------------------------------------------------- #
# codex
# --------------------------------------------------------------------------- #
class TestCodex(_Base):
    def test_web_on_off_config(self):
        p = self.prov("codex")
        on, _ = self.scry.render_call(
            p, "", None, True, self.settings(), "/tmp/out.txt")
        self.assertTrue(_contains_seq(on, ["-c", "web_search=live"]))
        off, _ = self.scry.render_call(
            p, "", None, False, self.settings(), "/tmp/out.txt")
        self.assertTrue(_contains_seq(off, ["-c", "web_search=disabled"]))
        self.assertFalse(_contains_seq(off, ["-c", "web_search=live"]))

    def test_outfile_and_stdin_arg_appended_last(self):
        p = self.prov("codex")
        argv, _ = self.scry.render_call(
            p, "", None, True, self.settings(), "/tmp/out.txt")
        # outfile flag+path present
        self.assertTrue(_adjacent(argv, "-o", "/tmp/out.txt"))
        # stdin_arg "-" is the very last token
        self.assertEqual(argv[-1], "-")
        # and the outfile pair sits just before the trailing "-"
        self.assertEqual(argv[-3:], ["-o", "/tmp/out.txt", "-"])

    def test_no_tool_cap(self):
        p = self.prov("codex")
        argv, _ = self.scry.render_call(
            p, "", None, True, self.settings(), "/tmp/out.txt")
        self.assertNotIn("--max-turns", argv)

    def test_effort(self):
        p = self.prov("codex")
        argv, _ = self.scry.render_call(
            p, "", None, True, self.settings(effort="high"), "/tmp/out.txt")
        self.assertTrue(_contains_seq(
            argv, ["-c", "model_reasoning_effort=high"]))

    def test_system_flag_null_no_system_added(self):
        # codex system_flag is null -> render_call must NOT add a system arg
        # even when a system string is passed.
        p = self.prov("codex")
        argv, _ = self.scry.render_call(
            p, "", "SYSTEM PROMPT", True, self.settings(), "/tmp/out.txt")
        self.assertNotIn("SYSTEM PROMPT", argv)
        # no --append-system-prompt style flag leaked from another provider
        self.assertNotIn("--append-system-prompt", argv)

    def test_no_env_override(self):
        p = self.prov("codex")
        _, env = self.scry.render_call(
            p, "", None, True, self.settings(max_output_tokens=2048),
            "/tmp/out.txt")
        # codex has no max_tokens_env cap -> nothing rendered into env
        self.assertEqual(env, {})


# --------------------------------------------------------------------------- #
# agy (Google / Antigravity)
# --------------------------------------------------------------------------- #
class TestAgy(_Base):
    def test_model_flag_no_web_no_outfile(self):
        p = self.prov("agy")
        argv, env = self.scry.render_call(
            p, "Gemini 3.1 Pro (High)", None, True, self.settings(), "/tmp/out")
        # model flag pair
        self.assertTrue(_adjacent(argv, "--model", "Gemini 3.1 Pro (High)"))
        # caps empty -> no web flags rendered
        self.assertNotIn("--allowedTools", argv)
        self.assertNotIn("--disallowedTools", argv)
        self.assertNotIn("-c", argv)
        # not an outfile capture provider, no stdin_arg
        self.assertNotIn("-o", argv)
        self.assertNotIn("-", argv)
        self.assertEqual(env, {})

    def test_web_off_same_as_on_no_caps(self):
        # caps empty -> web on/off produce identical argv (grounding can't toggle)
        p = self.prov("agy")
        on, _ = self.scry.render_call(
            p, "m", None, True, self.settings(), "/tmp/out")
        off, _ = self.scry.render_call(
            p, "m", None, False, self.settings(), "/tmp/out")
        self.assertEqual(on, off)

    def test_prompt_not_in_argv(self):
        # render_call does NOT add the prompt; that's append_prompt_arg's job.
        p = self.prov("agy")
        argv, _ = self.scry.render_call(
            p, "m", None, True, self.settings(), "/tmp/out")
        self.assertNotIn("-p", argv)


# --------------------------------------------------------------------------- #
# kimi (Moonshot) — agent_file rendering
# --------------------------------------------------------------------------- #
class TestKimi(_Base):
    def test_agentfile_appended(self):
        p = self.prov("kimi")
        argv, _ = self.scry.render_call(
            p, "k2", None, True, self.settings(), "/tmp/out",
            agentfile="/tmp/x.yaml")
        self.assertEqual(argv[-2:], ["--agent-file", "/tmp/x.yaml"])

    def test_no_agentfile_when_empty(self):
        p = self.prov("kimi")
        argv, _ = self.scry.render_call(
            p, "k2", None, True, self.settings(), "/tmp/out", agentfile="")
        self.assertNotIn("--agent-file", argv)

    def test_caps_empty_no_web_flags(self):
        p = self.prov("kimi")
        argv, _ = self.scry.render_call(
            p, "k2", None, True, self.settings(), "/tmp/out", agentfile="")
        self.assertNotIn("--allowedTools", argv)
        self.assertNotIn("--disallowedTools", argv)
        self.assertNotIn("--max-turns", argv)


# --------------------------------------------------------------------------- #
# append_prompt_arg
# --------------------------------------------------------------------------- #
class TestAppendPromptArg(_Base):
    def test_with_prompt_flag(self):
        p = self.prov("agy")  # prompt_flag "-p"
        out = self.scry.append_prompt_arg(["agy"], p, "HELLO")
        self.assertEqual(out, ["agy", "-p", "HELLO"])

    def test_bare_positional_when_no_prompt_flag(self):
        # a provider dict with no prompt_flag -> append bare positional
        p = {"cmd": ["x"]}
        out = self.scry.append_prompt_arg(["x"], p, "HELLO")
        self.assertEqual(out, ["x", "HELLO"])

    def test_does_not_mutate_input_list(self):
        base = ["agy"]
        self.scry.append_prompt_arg(base, self.prov("agy"), "HELLO")
        self.assertEqual(base, ["agy"])


if __name__ == "__main__":
    unittest.main()
