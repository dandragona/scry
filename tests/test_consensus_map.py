"""Tests for scry.render_consensus_map (and its _MAP_FIELDS table).

Covers: non-dict input -> "", all-empty fields -> "", header/bullets, ANSI gating
on the `on` flag, field ordering per _MAP_FIELDS, non-list coercion, max_items
truncation with the "… (+N more)" line, and whitespace-only item filtering.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class TestRenderConsensusMap(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.render = self.scry.render_consensus_map

    # --- non-dict / empty inputs -> "" -------------------------------------- #
    def test_none_returns_empty(self):
        self.assertEqual(self.render(None, on=False), "")

    def test_string_returns_empty(self):
        self.assertEqual(self.render("x", on=False), "")

    def test_list_returns_empty(self):
        self.assertEqual(self.render([], on=False), "")

    def test_all_empty_fields_returns_empty(self):
        analysis = {
            "consensus": [],
            "contradictions": [],
            "unique_insights": [],
            "partial_coverage": [],
            "blind_spots": [],
        }
        # Even though it's a dict, no field has content -> any_field stays False.
        self.assertEqual(self.render(analysis, on=False), "")

    def test_unknown_keys_only_returns_empty(self):
        # Keys not in _MAP_FIELDS contribute nothing.
        self.assertEqual(self.render({"foo": ["bar"], "baz": ["qux"]}, on=False), "")

    def test_empty_dict_returns_empty(self):
        self.assertEqual(self.render({}, on=False), "")

    # --- header + bullets (color off) --------------------------------------- #
    def test_consensus_header_and_bullets_no_color(self):
        out = self.render({"consensus": ["a", "b"]}, on=False)
        self.assertIn("◉ consensus map", out)
        self.assertIn("consensus", out)
        self.assertIn("      • a", out)
        self.assertIn("      • b", out)
        # No ANSI escape sequences when color is off.
        self.assertNotIn("\x1b", out)

    def test_consensus_header_and_bullets_color_on(self):
        out = self.render({"consensus": ["a", "b"]}, on=True)
        # ANSI codes present when color is on.
        self.assertIn("\x1b", out)
        self.assertIn("◉ consensus map", out)
        self.assertIn("      • a", out)
        self.assertIn("      • b", out)
        # The header is bolded (code "1") and the consensus glyph uses green (32).
        self.assertIn("\x1b[1m  ◉ consensus map\x1b[0m", out)
        self.assertIn("\x1b[32m", out)

    def test_bullets_themselves_are_never_colored(self):
        # The item bullet lines are emitted raw even when on=True.
        out = self.render({"consensus": ["alpha"]}, on=True)
        self.assertIn("      • alpha", out)

    # --- field ordering per _MAP_FIELDS ------------------------------------- #
    def test_field_order_follows_map_fields(self):
        analysis = {
            "blind_spots": ["bs"],
            "consensus": ["co"],
            "partial_coverage": ["pc"],
            "contradictions": ["cn"],
            "unique_insights": ["ui"],
        }
        out = self.render(analysis, on=False)
        # Expected display labels in canonical order.
        labels = [
            "consensus",
            "contradictions",
            "unique insights",
            "partial coverage",
            "blind spots",
        ]
        positions = [out.index(lbl) for lbl in labels]
        self.assertEqual(positions, sorted(positions),
                         f"labels not in _MAP_FIELDS order: {positions}")

    def test_only_non_empty_fields_render(self):
        analysis = {
            "consensus": ["co"],
            "contradictions": [],          # empty -> skipped
            "unique_insights": ["ui"],
            "partial_coverage": [],        # empty -> skipped
            "blind_spots": [],             # empty -> skipped
        }
        out = self.render(analysis, on=False)
        self.assertIn("consensus", out)
        self.assertIn("unique insights", out)
        self.assertNotIn("contradictions", out)
        self.assertNotIn("partial coverage", out)
        self.assertNotIn("blind spots", out)

    def test_map_fields_table_shape(self):
        # Sanity-check the table the function iterates over.
        keys = [row[0] for row in self.scry._MAP_FIELDS]
        self.assertEqual(
            keys,
            ["consensus", "contradictions", "unique_insights",
             "partial_coverage", "blind_spots"],
        )

    # --- non-list coercion -------------------------------------------------- #
    def test_non_list_value_coerced_to_single_item(self):
        out = self.render({"consensus": "just one"}, on=False)
        self.assertIn("      • just one", out)
        # Only one bullet rendered.
        self.assertEqual(out.count("• "), 1)

    def test_non_string_scalar_coerced_via_str(self):
        out = self.render({"consensus": 42}, on=False)
        self.assertIn("      • 42", out)

    def test_dict_value_coerced_to_single_item(self):
        # A dict is not a list, so it's wrapped: [the_dict] -> str(dict) bullet.
        d = {"k": "v"}
        out = self.render({"consensus": d}, on=False)
        self.assertIn("      • " + str(d), out)

    # --- max_items truncation ----------------------------------------------- #
    def test_max_items_truncation_shows_more_line(self):
        items = ["i1", "i2", "i3", "i4", "i5", "i6"]
        out = self.render({"consensus": items}, on=False, max_items=4)
        # First four bullets present.
        for it in items[:4]:
            self.assertIn(f"      • {it}", out)
        # Fifth and sixth are NOT shown as bullets.
        self.assertNotIn("      • i5", out)
        self.assertNotIn("      • i6", out)
        # The truncation summary line shows the remaining count.
        self.assertIn("… (+2 more)", out)
        self.assertEqual(out.count("• "), 4)

    def test_truncation_line_colored_when_on(self):
        items = [f"x{i}" for i in range(6)]
        out = self.render({"consensus": items}, on=True, max_items=4)
        # The "… (+N more)" line is wrapped in the dim code "2".
        self.assertIn("\x1b[2m      … (+2 more)\x1b[0m", out)

    def test_no_more_line_when_exactly_max_items(self):
        items = ["a", "b", "c", "d"]
        out = self.render({"consensus": items}, on=False, max_items=4)
        self.assertNotIn("more)", out)
        self.assertEqual(out.count("• "), 4)

    def test_default_max_items_is_four(self):
        items = [f"n{i}" for i in range(7)]
        out = self.render({"consensus": items}, on=False)  # default max_items
        self.assertEqual(out.count("• "), 4)
        self.assertIn("… (+3 more)", out)

    # --- whitespace filtering ----------------------------------------------- #
    def test_whitespace_only_items_filtered(self):
        analysis = {"consensus": ["  ", "real", "\t\n", "", "  also  "]}
        out = self.render(analysis, on=False)
        # Surviving items are stripped.
        self.assertIn("      • real", out)
        self.assertIn("      • also", out)
        # Only the two non-blank items remain.
        self.assertEqual(out.count("• "), 2)

    def test_all_whitespace_field_treated_as_empty(self):
        # A field whose every item is blank renders nothing and, if it's the only
        # field, the whole map is "".
        self.assertEqual(self.render({"consensus": ["  ", "\t"]}, on=False), "")

    def test_items_are_stripped(self):
        out = self.render({"consensus": ["  padded  "]}, on=False)
        self.assertIn("      • padded", out)
        self.assertNotIn("      •   padded", out)


if __name__ == "__main__":
    unittest.main()
