"""Unit tests for scry._stream_extract(obj, fmt) -> (delta|None, final|None).

_stream_extract is a pure parser: given one already-parsed stream-event object and
a format string, it classifies the object into (incremental delta text, final text).
For fmt="claude" it understands four shapes — stream_event/content_block_delta
(delta), result (final), assistant message (final), and everything else (none).
For any other fmt it returns (None, None) unconditionally. No subprocess / no model
CLI is involved, so these tests just call the function directly.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class TestStreamExtractClaude(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.fn = self.scry._stream_extract

    # ----- content_block_delta -> delta text ----------------------------- #
    def test_content_block_delta_returns_delta_text(self):
        obj = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hi"},
            },
        }
        self.assertEqual(self.fn(obj, "claude"), ("hi", None))

    def test_content_block_delta_empty_string_text(self):
        # An empty string is still a str -> returned as-is (not coerced to None here).
        obj = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": ""},
            },
        }
        self.assertEqual(self.fn(obj, "claude"), ("", None))

    def test_stream_event_non_delta_type(self):
        # event.type is not content_block_delta -> (None, None)
        obj = {
            "type": "stream_event",
            "event": {"type": "message_start"},
        }
        self.assertEqual(self.fn(obj, "claude"), (None, None))

    def test_stream_event_delta_missing_text_key(self):
        # content_block_delta but the delta dict lacks "text" -> .get('text') is None
        obj = {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta"}},
        }
        self.assertEqual(self.fn(obj, "claude"), (None, None))

    def test_stream_event_delta_is_none(self):
        # delta is explicitly None -> (obj.get('delta') or {}) guards the .get
        obj = {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": None},
        }
        self.assertEqual(self.fn(obj, "claude"), (None, None))

    def test_stream_event_no_event_key_does_not_raise(self):
        # Missing nested "event" must be tolerated (obj.get('event') or {}).
        obj = {"type": "stream_event"}
        self.assertEqual(self.fn(obj, "claude"), (None, None))

    def test_stream_event_event_is_none_does_not_raise(self):
        obj = {"type": "stream_event", "event": None}
        self.assertEqual(self.fn(obj, "claude"), (None, None))

    # ----- result -> final text ------------------------------------------ #
    def test_result_str_returns_final(self):
        self.assertEqual(self.fn({"type": "result", "result": "done"}, "claude"),
                         (None, "done"))

    def test_result_non_str_returns_none(self):
        # result present but not a str -> (None, None)
        self.assertEqual(self.fn({"type": "result", "result": 123}, "claude"),
                         (None, None))

    def test_result_dict_value_returns_none(self):
        self.assertEqual(self.fn({"type": "result", "result": {"x": 1}}, "claude"),
                         (None, None))

    def test_result_missing_key_returns_none(self):
        # type=result but no "result" key -> obj.get('result') is None -> (None, None)
        self.assertEqual(self.fn({"type": "result"}, "claude"), (None, None))

    def test_result_empty_string_returns_empty_string(self):
        # An empty string is still a str, so it is returned as the final (not None).
        self.assertEqual(self.fn({"type": "result", "result": ""}, "claude"),
                         (None, ""))

    # ----- assistant message -> joined text parts ------------------------ #
    def test_assistant_joins_text_parts(self):
        obj = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "a"},
                    {"type": "text", "text": "b"},
                ]
            },
        }
        self.assertEqual(self.fn(obj, "claude"), (None, "ab"))

    def test_assistant_skips_non_text_parts(self):
        obj = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "a"},
                    {"type": "tool_use", "name": "foo"},
                    {"type": "text", "text": "c"},
                ]
            },
        }
        self.assertEqual(self.fn(obj, "claude"), (None, "ac"))

    def test_assistant_no_text_parts_returns_none(self):
        # Only non-text parts -> joined "" -> (txt or None) -> None
        obj = {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "foo"}]},
        }
        self.assertEqual(self.fn(obj, "claude"), (None, None))

    def test_assistant_empty_content_returns_none(self):
        obj = {"type": "assistant", "message": {"content": []}}
        self.assertEqual(self.fn(obj, "claude"), (None, None))

    def test_assistant_missing_message_does_not_raise(self):
        # Missing "message" -> (obj.get('message') or {}) guards .get('content')
        obj = {"type": "assistant"}
        self.assertEqual(self.fn(obj, "claude"), (None, None))

    def test_assistant_message_none_does_not_raise(self):
        obj = {"type": "assistant", "message": None}
        self.assertEqual(self.fn(obj, "claude"), (None, None))

    def test_assistant_content_none_does_not_raise(self):
        # message.content is None -> (... or []) guards the comprehension
        obj = {"type": "assistant", "message": {"content": None}}
        self.assertEqual(self.fn(obj, "claude"), (None, None))

    def test_assistant_content_with_non_dict_part_does_not_raise(self):
        # A part that isn't a dict is skipped by the isinstance(p, dict) guard.
        obj = {
            "type": "assistant",
            "message": {"content": ["not-a-dict", {"type": "text", "text": "z"}]},
        }
        self.assertEqual(self.fn(obj, "claude"), (None, "z"))

    def test_assistant_text_part_missing_text_key_treated_as_empty(self):
        # type=text but no "text" key -> p.get('text','') -> '' -> overall None
        obj = {
            "type": "assistant",
            "message": {"content": [{"type": "text"}]},
        }
        self.assertEqual(self.fn(obj, "claude"), (None, None))

    # ----- unknown / missing type ---------------------------------------- #
    def test_something_else_type_returns_none(self):
        self.assertEqual(self.fn({"type": "something_else"}, "claude"), (None, None))

    def test_no_type_key_returns_none(self):
        # No "type" key at all -> t is None -> falls through -> (None, None)
        self.assertEqual(self.fn({}, "claude"), (None, None))


class TestStreamExtractOtherFormats(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.fn = self.scry._stream_extract

    def test_other_fmt_always_none_even_for_delta_shape(self):
        # A perfectly valid claude delta shape, but fmt != "claude" -> (None, None).
        obj = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hi"},
            },
        }
        self.assertEqual(self.fn(obj, "other"), (None, None))

    def test_other_fmt_result_shape_returns_none(self):
        self.assertEqual(self.fn({"type": "result", "result": "done"}, "other"),
                         (None, None))

    def test_other_fmt_assistant_shape_returns_none(self):
        obj = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "a"}]},
        }
        self.assertEqual(self.fn(obj, "other"), (None, None))

    def test_empty_string_fmt_returns_none(self):
        self.assertEqual(self.fn({"type": "result", "result": "done"}, ""),
                         (None, None))

    def test_none_fmt_returns_none(self):
        # fmt is None (no stream format configured) -> not == "claude" -> (None, None)
        self.assertEqual(self.fn({"type": "result", "result": "done"}, None),
                         (None, None))

    def test_empty_obj_other_fmt_returns_none(self):
        self.assertEqual(self.fn({}, "openai"), (None, None))


if __name__ == "__main__":
    unittest.main()
