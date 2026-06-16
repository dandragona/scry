"""Unit tests for scry-eval pure grading / util functions.

Covers: find_answer_line, token_match, grade_objective, parse_verdict,
judges_for, parse_grader, _mark, is_transient, task_complete.

All functions under test are pure (no subprocess / no model CLI), so these are
plain in-process assertions. ev = h.load_scry_eval().
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402

ev = h.load_scry_eval()


class TestFindAnswerLine(unittest.TestCase):
    def test_basic_colon(self):
        self.assertEqual(ev.find_answer_line("blah\nANSWER: 42"), "42")

    def test_multiple_answers_takes_last(self):
        text = "ANSWER: 1\nsome reasoning\nANSWER: 99"
        self.assertEqual(ev.find_answer_line(text), "99")

    def test_equals_sign_and_lowercase(self):
        self.assertEqual(ev.find_answer_line("answer = 9.9"), "9.9")

    def test_mixed_case_keyword(self):
        self.assertEqual(ev.find_answer_line("Answer: hello"), "hello")

    def test_no_answer_line_returns_none(self):
        self.assertIsNone(ev.find_answer_line("just some text\nwith no marker"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(ev.find_answer_line(""))

    def test_none_input_returns_none(self):
        self.assertIsNone(ev.find_answer_line(None))

    def test_leading_whitespace_allowed(self):
        # regex anchors with ^\s* so an indented ANSWER line still matches.
        self.assertEqual(ev.find_answer_line("   ANSWER:   trimmed   "), "trimmed")


class TestTokenMatch(unittest.TestCase):
    def test_phrase_with_trailing_punctuation(self):
        self.assertTrue(ev.token_match("the cost is 5 cents.", "5 cents"))

    def test_left_digit_boundary(self):
        # '20' must NOT match inside '120'.
        self.assertFalse(ev.token_match("120", "20"))

    def test_decimal_boundary(self):
        # '9.9' must NOT match inside '19.9' (leading-dot/word boundary).
        self.assertFalse(ev.token_match("19.9", "9.9"))

    def test_case_insensitive(self):
        self.assertTrue(ev.token_match("The Answer Is PARIS", "paris"))

    def test_plain_token_present(self):
        self.assertTrue(ev.token_match("the answer is 42 indeed", "42"))

    def test_token_with_surrounding_whitespace_stripped(self):
        # token.strip() is applied before matching.
        self.assertTrue(ev.token_match("value is 7", "  7  "))


class TestGradeObjective(unittest.TestCase):
    def test_answer_line_exact_match(self):
        correct, evidence, tok = ev.grade_objective("ANSWER: 9.9", ["9.9"])
        self.assertTrue(correct)
        self.assertEqual(evidence, "9.9")
        self.assertEqual(tok, "9.9")

    def test_fallback_to_full_text_when_no_answer_line(self):
        # No ANSWER line -> hay is the full text, evidence is the sentinel.
        correct, evidence, tok = ev.grade_objective("the result is 42 ok", ["42"])
        self.assertTrue(correct)
        self.assertEqual(evidence, "(full text)")
        self.assertEqual(tok, "42")

    def test_no_match_with_answer_line(self):
        correct, evidence, tok = ev.grade_objective("ANSWER: 7", ["42"])
        self.assertFalse(correct)
        self.assertEqual(evidence, "7")
        self.assertIsNone(tok)

    def test_no_match_no_answer_line(self):
        correct, evidence, tok = ev.grade_objective("nothing relevant here", ["42"])
        self.assertFalse(correct)
        self.assertEqual(evidence, "(full text)")
        self.assertIsNone(tok)

    def test_multiple_expected_any_match(self):
        correct, evidence, tok = ev.grade_objective("ANSWER: blue", ["red", "blue"])
        self.assertTrue(correct)
        self.assertEqual(evidence, "blue")
        self.assertEqual(tok, "blue")

    def test_multiple_expected_first_match_wins(self):
        # 'red' is checked before 'crimson'; both present -> returns first.
        correct, evidence, tok = ev.grade_objective("ANSWER: red crimson",
                                                     ["red", "crimson"])
        self.assertTrue(correct)
        self.assertEqual(tok, "red")

    def test_boundary_respected_in_grading(self):
        # expecting '20' against an answer of '120' -> no match (boundary).
        correct, evidence, tok = ev.grade_objective("ANSWER: 120", ["20"])
        self.assertFalse(correct)
        self.assertEqual(evidence, "120")
        self.assertIsNone(tok)

    def test_empty_text(self):
        correct, evidence, tok = ev.grade_objective("", ["42"])
        self.assertFalse(correct)
        self.assertEqual(evidence, "(full text)")
        self.assertIsNone(tok)


class TestParseVerdict(unittest.TestCase):
    def test_verdict_a(self):
        self.assertEqual(ev.parse_verdict("VERDICT: A"), "A")

    def test_verdict_dash_b_lowercase(self):
        self.assertEqual(ev.parse_verdict("verdict - b"), "B")

    def test_tie(self):
        self.assertEqual(ev.parse_verdict("VERDICT: TIE"), "TIE")

    def test_tie_with_connector_words(self):
        # Natural phrasings the judge may slip in are now tolerated (connector
        # words like "is"/"it's"/"a" between 'verdict' and the choice).
        self.assertEqual(ev.parse_verdict("My verdict is TIE here"), "TIE")
        self.assertEqual(ev.parse_verdict("My verdict is A"), "A")
        self.assertEqual(ev.parse_verdict("verdict: it's a tie"), "TIE")
        self.assertEqual(ev.parse_verdict("The verdict is B."), "B")

    def test_keyword_still_required_no_false_positive(self):
        # Still anchored to the 'verdict' keyword, so unrelated prose with a stray
        # letter doesn't false-positive.
        self.assertIsNone(ev.parse_verdict("the verdict is alarming"))

    def test_last_verdict_wins(self):
        self.assertEqual(ev.parse_verdict("VERDICT: A\nactually VERDICT: B"), "B")

    def test_no_verdict_returns_none(self):
        self.assertIsNone(ev.parse_verdict("there is no decision here"))

    def test_empty_returns_none(self):
        self.assertIsNone(ev.parse_verdict(""))

    def test_none_returns_none(self):
        self.assertIsNone(ev.parse_verdict(None))

    def test_bare_tie_without_keyword(self):
        # A bare 'TIE' with no 'verdict' keyword does NOT match (avoids reading a
        # stray choice letter out of unrelated prose).
        self.assertIsNone(ev.parse_verdict("It is a TIE between them"))


class TestJudgesFor(unittest.TestCase):
    def test_excludes_fused_and_solo(self):
        judges, fams = ev.judges_for("anthropic", "openai")
        self.assertEqual(fams, ["google", "moonshot"])
        self.assertEqual(judges, [ev.FAMILY_JUDGE["google"],
                                  ev.FAMILY_JUDGE["moonshot"]])

    def test_same_family_excluded_once_leaves_three(self):
        judges, fams = ev.judges_for("anthropic", "anthropic")
        self.assertEqual(fams, ["openai", "google", "moonshot"])
        self.assertEqual(judges, [ev.FAMILY_JUDGE["openai"],
                                  ev.FAMILY_JUDGE["google"],
                                  ev.FAMILY_JUDGE["moonshot"]])

    def test_order_follows_family_judge_iteration(self):
        # fused=google, solo=moonshot -> remaining anthropic, openai in dict order.
        judges, fams = ev.judges_for("google", "moonshot")
        self.assertEqual(fams, ["anthropic", "openai"])
        self.assertEqual(judges, [ev.FAMILY_JUDGE["anthropic"],
                                  ev.FAMILY_JUDGE["openai"]])

    def test_returned_dicts_are_judge_configs(self):
        judges, _ = ev.judges_for("anthropic", "openai")
        for j in judges:
            self.assertIn("provider", j)
            self.assertIn("model", j)


class TestParseGrader(unittest.TestCase):
    def test_provider_and_model(self):
        self.assertEqual(ev.parse_grader("claude:opus"),
                         {"provider": "claude", "model": "opus"})

    def test_provider_only_empty_model(self):
        self.assertEqual(ev.parse_grader("codex"),
                         {"provider": "codex", "model": ""})

    def test_whitespace_is_stripped(self):
        self.assertEqual(ev.parse_grader("  claude  :  opus  "),
                         {"provider": "claude", "model": "opus"})

    def test_model_with_extra_colons_keeps_remainder(self):
        # partition splits on the FIRST colon only.
        self.assertEqual(ev.parse_grader("agy:Gemini 3.1 Pro: High"),
                         {"provider": "agy", "model": "Gemini 3.1 Pro: High"})


class TestMark(unittest.TestCase):
    def test_true(self):
        self.assertEqual(ev._mark(True), "✓")  # ✓

    def test_false(self):
        self.assertEqual(ev._mark(False), "✗")  # ✗

    def test_none(self):
        self.assertEqual(ev._mark(None), "—")  # —


class TestIsTransient(unittest.TestCase):
    def test_rate_limited(self):
        self.assertTrue(ev.is_transient("rate limited"))

    def test_429(self):
        self.assertTrue(ev.is_transient("HTTP 429 Too Many Requests"))

    def test_overloaded(self):
        self.assertTrue(ev.is_transient("server overloaded"))

    def test_timeout(self):
        self.assertTrue(ev.is_transient("connection timeout"))

    def test_503(self):
        self.assertTrue(ev.is_transient("503 Service Unavailable"))

    def test_quota(self):
        self.assertTrue(ev.is_transient("quota exceeded"))

    def test_nonsense_error_false(self):
        self.assertFalse(ev.is_transient("nonsense error"))

    def test_empty_false(self):
        self.assertFalse(ev.is_transient(""))

    def test_none_false(self):
        self.assertFalse(ev.is_transient(None))

    def test_case_insensitive(self):
        self.assertTrue(ev.is_transient("RATE LIMITED"))


class TestTaskComplete(unittest.TestCase):
    def test_complete(self):
        self.assertTrue(ev.task_complete({"sut_ok": True,
                                          "fused": {"ungraded": 0}}))

    def test_ungraded_positive_not_complete(self):
        self.assertFalse(ev.task_complete({"sut_ok": True,
                                           "fused": {"ungraded": 3}}))

    def test_sut_not_ok_not_complete(self):
        self.assertFalse(ev.task_complete({"sut_ok": False,
                                           "fused": {"ungraded": 0}}))

    def test_missing_fused_not_complete(self):
        # .get("fused", {}).get("ungraded", 1) -> 1 -> not complete.
        self.assertFalse(ev.task_complete({"sut_ok": True}))

    def test_missing_sut_ok_not_complete(self):
        self.assertFalse(ev.task_complete({"fused": {"ungraded": 0}}))

    def test_empty_dict_not_complete(self):
        self.assertFalse(ev.task_complete({}))

    def test_fused_missing_ungraded_key_not_complete(self):
        # fused present but ungraded missing -> default 1 -> not complete.
        self.assertFalse(ev.task_complete({"sut_ok": True, "fused": {}}))


if __name__ == "__main__":
    unittest.main()
