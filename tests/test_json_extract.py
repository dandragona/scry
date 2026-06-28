"""Unit tests for scry's JSON extraction/parsing helpers:
scry.extract_json_result, scry.tolerant_json, scry._slice_braces.

These are pure functions (no subprocesses, no model CLIs), so we just load the
scry module and call them directly. No StubBins needed.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class TestExtractJsonResult(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_result_key_present_returns_value_stripped(self):
        # {"result":"hi"} with result_path="result" -> "hi"
        out = self.scry.extract_json_result('{"result": "hi"}', "result")
        self.assertEqual(out, "hi")

    def test_result_value_is_stripped(self):
        # the returned str value is .strip()'d
        out = self.scry.extract_json_result('{"result": "  spaced  "}', "result")
        self.assertEqual(out, "spaced")

    def test_result_path_can_be_a_nonstandard_key(self):
        # result_path is consulted FIRST, even if it's not one of the fallbacks.
        out = self.scry.extract_json_result('{"answer": "from-answer"}', "answer")
        self.assertEqual(out, "from-answer")

    def test_missing_result_falls_back_to_response(self):
        # result missing, but a fallback key (response) is present -> "yo"
        out = self.scry.extract_json_result('{"response": "yo"}', "result")
        self.assertEqual(out, "yo")

    def test_fallback_key_order_result_wins(self):
        # The fallback loop tries result,response,text,content,output_text in order.
        # result_path="missing" (absent), so val is None and the loop runs; "result"
        # is checked before "response".
        out = self.scry.extract_json_result(
            '{"response": "second", "result": "first"}', "missing")
        self.assertEqual(out, "first")

    def test_fallback_to_text_content_output_text(self):
        self.assertEqual(
            self.scry.extract_json_result('{"text": "t"}', "result"), "t")
        self.assertEqual(
            self.scry.extract_json_result('{"content": "c"}', "result"), "c")
        self.assertEqual(
            self.scry.extract_json_result('{"output_text": "o"}', "result"), "o")

    def test_result_not_a_str_falls_through_to_fallback(self):
        # {"result":123, "response":"r"} -> result is not str, falls to response.
        out = self.scry.extract_json_result(
            '{"result": 123, "response": "r"}', "result")
        self.assertEqual(out, "r")

    def test_result_not_a_str_and_no_fallback_returns_raw_text(self):
        # {"result":123} with no fallback string keys -> returns the raw (stripped)
        # JSON text unchanged.
        raw = '{"result": 123}'
        out = self.scry.extract_json_result(raw, "result")
        self.assertEqual(out, raw)

    def test_non_json_text_returned_stripped(self):
        out = self.scry.extract_json_result("  just some text  ", "result")
        self.assertEqual(out, "just some text")

    def test_non_json_text_already_stripped_unchanged(self):
        out = self.scry.extract_json_result("plain answer", "result")
        self.assertEqual(out, "plain answer")

    def test_json_that_is_not_a_dict_returns_raw_text(self):
        # "[1,2]" parses as a list (not a dict) -> returns the raw text.
        out = self.scry.extract_json_result("[1,2]", "result")
        self.assertEqual(out, "[1,2]")

    def test_json_scalar_returns_raw_text(self):
        # A bare JSON string/number parses fine but is not a dict -> raw text back.
        # json.loads('"hello"') -> 'hello' (a str, not dict) -> returns text as-is,
        # i.e. the literal source including the quotes.
        out = self.scry.extract_json_result('"hello"', "result")
        self.assertEqual(out, '"hello"')


class TestTolerantJson(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_plain_object(self):
        self.assertEqual(self.scry.tolerant_json('{"a": 1}'), {"a": 1})

    def test_code_fenced_with_language(self):
        self.assertEqual(
            self.scry.tolerant_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_code_fenced_bare(self):
        # bare ``` fence (no language) is stripped too.
        self.assertEqual(
            self.scry.tolerant_json('```\n{"a": 1}\n```'), {"a": 1})

    def test_prose_around_object_uses_brace_slice(self):
        self.assertEqual(
            self.scry.tolerant_json('noise {"a": 1} trailing'), {"a": 1})

    def test_empty_string_returns_none(self):
        self.assertIsNone(self.scry.tolerant_json(""))

    def test_none_returns_none(self):
        self.assertIsNone(self.scry.tolerant_json(None))

    def test_not_json_returns_none(self):
        self.assertIsNone(self.scry.tolerant_json("not json at all"))

    def test_valid_json_but_not_a_dict_returns_none(self):
        # "[1,2]" is valid JSON but a list, not a dict -> None.
        self.assertIsNone(self.scry.tolerant_json("[1,2]"))

    def test_recovers_object_despite_trailing_stray_brace(self):
        # A stray closing brace in trailing commentary must NOT defeat recovery:
        # the balanced-object scan finds the real object and ignores the lone '}'.
        self.assertEqual(
            self.scry.tolerant_json('{"a": 1} then garbage }'), {"a": 1})

    def test_brace_slice_recovers_when_junk_has_no_trailing_brace(self):
        # Here the last '}' closes the object, so the slice is exactly the object.
        self.assertEqual(
            self.scry.tolerant_json('{"a": 1} then garbage'), {"a": 1})

    def test_prose_with_its_own_braces_before_the_object(self):
        # A chatty judge that writes set notation ({x, y}) before the real JSON: the
        # greedy outermost slice would span both and fail; the balanced scan tries each
        # {...} and returns the one that parses.
        self.assertEqual(
            self.scry.tolerant_json('I think the set {x, y} matters. {"consensus": ["a"]}'),
            {"consensus": ["a"]})

    def test_two_objects_returns_the_larger_real_one(self):
        # When the judge emits a tiny preamble object then the real analysis, prefer
        # the larger valid object (the real 5-field analysis dwarfs an incidental one).
        s = '{"first": 1}\nhere it is:\n{"consensus": ["a"], "contradictions": []}'
        self.assertEqual(
            self.scry.tolerant_json(s),
            {"consensus": ["a"], "contradictions": []})

    def test_trailing_comma_is_tolerated(self):
        # Models frequently emit a trailing comma; strip it before parsing.
        self.assertEqual(
            self.scry.tolerant_json('{"a": 1, "b": 2,}'), {"a": 1, "b": 2})

    def test_trailing_comma_inside_prose(self):
        self.assertEqual(
            self.scry.tolerant_json('here: {"items": ["x", "y",],}'),
            {"items": ["x", "y"]})

    def test_brace_inside_string_value_not_treated_as_structure(self):
        # A '}' inside a string value must not prematurely close the object.
        self.assertEqual(
            self.scry.tolerant_json('prose {"note": "use } carefully"} end'),
            {"note": "use } carefully"})


class TestSliceBraces(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_simple_slice(self):
        self.assertEqual(self.scry._slice_braces("x{y}z"), "{y}")

    def test_no_braces_returns_none(self):
        self.assertIsNone(self.scry._slice_braces("no braces"))

    def test_closing_before_opening_returns_none(self):
        # '}{' : find('{')=1, rfind('}')=0 -> b < a -> None.
        self.assertIsNone(self.scry._slice_braces("}{"))

    def test_outermost_braces_kept(self):
        # find = first '{', rfind = last '}': spans the whole outer object.
        self.assertEqual(
            self.scry._slice_braces('pre {"a": {"b": 1}} post'),
            '{"a": {"b": 1}}')

    def test_only_open_brace_returns_none(self):
        # '{' alone: rfind('}') = -1, so b == -1, not > a -> None.
        self.assertIsNone(self.scry._slice_braces("{"))


if __name__ == "__main__":
    unittest.main()
