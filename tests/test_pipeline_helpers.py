"""Unit tests for scry's small pipeline/dry-run/entry helpers:
numbered_responses, _failure_reason, _trunc, _dry_prompt, read_prompt.

These are pure (or nearly pure) functions — no subprocess, no model CLI is ever
invoked. read_prompt is exercised by monkeypatching sys.stdin with a local fake.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class _FakeStdin:
    """A minimal stdin stand-in: controllable isatty() and a canned read()."""

    def __init__(self, tty: bool, data: str = ""):
        self._tty = tty
        self._data = data

    def isatty(self) -> bool:
        return self._tty

    def read(self) -> str:
        return self._data


class TestNumberedResponses(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_two_responses(self):
        out = self.scry.numbered_responses([{"content": "a"}, {"content": "b"}])
        self.assertEqual(out, "1. a\n2. b")

    def test_single_response(self):
        out = self.scry.numbered_responses([{"content": "only"}])
        self.assertEqual(out, "1. only")

    def test_empty_list(self):
        self.assertEqual(self.scry.numbered_responses([]), "")

    def test_preserves_order_and_multiline_content(self):
        out = self.scry.numbered_responses(
            [{"content": "first"}, {"content": "line\nbreak"}, {"content": "third"}]
        )
        self.assertEqual(out, "1. first\n2. line\nbreak\n3. third")


class TestFailureReason(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_rate_keyword(self):
        self.assertEqual(
            self.scry._failure_reason([{"error": "rate limit hit"}]), "rate_limited"
        )

    def test_429_keyword(self):
        self.assertEqual(
            self.scry._failure_reason([{"error": "HTTP 429 Too Many Requests"}]),
            "rate_limited",
        )

    def test_overloaded_keyword(self):
        self.assertEqual(
            self.scry._failure_reason([{"error": "server overloaded, retry"}]),
            "rate_limited",
        )

    def test_credit_keyword(self):
        self.assertEqual(
            self.scry._failure_reason([{"error": "not enough credit"}]),
            "insufficient_credits",
        )

    def test_insufficient_keyword(self):
        self.assertEqual(
            self.scry._failure_reason([{"error": "insufficient funds"}]),
            "insufficient_credits",
        )

    def test_quota_keyword(self):
        self.assertEqual(
            self.scry._failure_reason([{"error": "quota exceeded"}]),
            "insufficient_credits",
        )

    def test_balance_keyword(self):
        self.assertEqual(
            self.scry._failure_reason([{"error": "account balance too low"}]),
            "insufficient_credits",
        )

    def test_timeout_falls_through(self):
        self.assertEqual(
            self.scry._failure_reason([{"error": "timeout waiting"}]),
            "all_panels_failed",
        )

    def test_boom_falls_through(self):
        self.assertEqual(
            self.scry._failure_reason([{"error": "boom"}]), "all_panels_failed"
        )

    def test_rate_takes_precedence_over_credit(self):
        # blob contains both 'rate' and 'credit' — rate is checked first.
        self.assertEqual(
            self.scry._failure_reason([{"error": "rate limit; out of credit"}]),
            "rate_limited",
        )

    def test_none_error_is_tolerated(self):
        # Some entries may carry error=None (filtered via `or ""`); only the real
        # error string drives the classification.
        self.assertEqual(
            self.scry._failure_reason(
                [{"error": None}, {"error": "429 backoff"}]
            ),
            "rate_limited",
        )

    def test_missing_error_key_defaults_to_all_failed(self):
        # .get('error') is None -> empty blob -> the catch-all reason.
        self.assertEqual(self.scry._failure_reason([{}]), "all_panels_failed")

    def test_empty_list_is_all_failed(self):
        self.assertEqual(self.scry._failure_reason([]), "all_panels_failed")

    def test_case_insensitive_matching(self):
        # blob is lowercased before matching, so upper-case keywords still hit.
        self.assertEqual(
            self.scry._failure_reason([{"error": "RATE LIMITED"}]), "rate_limited"
        )


class TestTrunc(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_short_unchanged(self):
        self.assertEqual(self.scry._trunc("short"), "short")

    def test_exactly_at_limit_unchanged(self):
        s = "x" * 64
        self.assertEqual(self.scry._trunc(s, 64), s)

    def test_over_limit_truncated_to_n_with_ellipsis(self):
        out = self.scry._trunc("x" * 100, 64)
        self.assertEqual(len(out), 64)
        self.assertTrue(out.endswith("…"))
        # 63 original chars + the ellipsis.
        self.assertEqual(out, "x" * 63 + "…")

    def test_default_n_is_64(self):
        out = self.scry._trunc("y" * 200)
        self.assertEqual(len(out), 64)
        self.assertTrue(out.endswith("…"))

    def test_empty_string(self):
        self.assertEqual(self.scry._trunc(""), "")


class TestDryPrompt(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.cfg = self.scry.load_config(str(h.CONFIG_JSON))

    def test_arg_provider_appends_prompt_flag(self):
        # agy: prompt='arg', prompt_flag='-p'.
        p = self.cfg["providers"]["agy"]
        base = ["agy", "--print-timeout", "400s"]
        argv, note = self.scry._dry_prompt(p, list(base), what="prompt")
        self.assertEqual(argv, base + ["-p", "{PROMPT}"])
        self.assertEqual(note, "prompt as arg")

    def test_stdin_provider_unchanged(self):
        # claude: prompt='stdin' -> argv passes through untouched.
        p = self.cfg["providers"]["claude"]
        base = ["claude", "-p"]
        argv, note = self.scry._dry_prompt(p, list(base), what="prompt")
        self.assertEqual(argv, base)
        self.assertEqual(note, "prompt on stdin")

    def test_what_token_is_uppercased(self):
        p = self.cfg["providers"]["agy"]
        argv, note = self.scry._dry_prompt(p, ["agy"], what="judge")
        self.assertEqual(argv, ["agy", "-p", "{JUDGE}"])
        self.assertEqual(note, "judge as arg")

    def test_arg_provider_without_prompt_flag_uses_bare_positional(self):
        # If an 'arg' provider has no prompt_flag, append_prompt_arg appends a bare
        # positional token (no flag).
        p = {"prompt": "arg"}
        argv, note = self.scry._dry_prompt(p, ["tool"], what="prompt")
        self.assertEqual(argv, ["tool", "{PROMPT}"])
        self.assertEqual(note, "prompt as arg")


class TestReadPrompt(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self._orig_stdin = self.scry.sys.stdin
        self.addCleanup(setattr, self.scry.sys, "stdin", self._orig_stdin)

    def test_positional_args_win(self):
        # Even with a non-tty stdin holding data, positional args take priority and
        # stdin is never read.
        self.scry.sys.stdin = _FakeStdin(tty=False, data="SHOULD_NOT_BE_READ\n")
        self.assertEqual(self.scry.read_prompt(["hello", "world"]), "hello world")

    def test_positional_single_arg(self):
        self.scry.sys.stdin = _FakeStdin(tty=True)
        self.assertEqual(self.scry.read_prompt(["solo"]), "solo")

    def test_positional_is_stripped(self):
        self.scry.sys.stdin = _FakeStdin(tty=True)
        self.assertEqual(self.scry.read_prompt(["  spaced  "]), "spaced")

    def test_piped_stdin_when_no_pos(self):
        self.scry.sys.stdin = _FakeStdin(tty=False, data="piped\n")
        self.assertEqual(self.scry.read_prompt([]), "piped")

    def test_tty_with_no_pos_returns_empty_without_blocking(self):
        # isatty()->True means an interactive terminal: do NOT read (would block),
        # just return "".
        called = {"read": False}

        class _TTYNoRead(_FakeStdin):
            def read(_self):
                called["read"] = True
                raise AssertionError("read() must not be called on a tty")

        self.scry.sys.stdin = _TTYNoRead(tty=True)
        self.assertEqual(self.scry.read_prompt([]), "")
        self.assertFalse(called["read"])

    def test_empty_pos_list_and_whitespace_pos_falls_back_to_stdin(self):
        # pos joins to empty/whitespace -> falls through to stdin path.
        self.scry.sys.stdin = _FakeStdin(tty=False, data="from_stdin\n")
        self.assertEqual(self.scry.read_prompt(["   "]), "from_stdin")

    def test_piped_stdin_is_stripped(self):
        self.scry.sys.stdin = _FakeStdin(tty=False, data="  trimmed me  \n\n")
        self.assertEqual(self.scry.read_prompt([]), "trimmed me")


if __name__ == "__main__":
    unittest.main()
