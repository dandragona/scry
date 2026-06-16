"""Unit tests for small pure helpers in `scry`:
  * color_enabled(stream) — FORCE_COLOR / NO_COLOR / TERM / isatty precedence.
  * _ver_tuple(v)        — loose numeric version key.
  * _uniq_label(base, used) — de-duplicating label allocator.

These are stdlib-only, never touch a model CLI, and gate purely on env + args.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class TestColorEnabled(unittest.TestCase):
    """color_enabled (scry lines 1101-1113). Precedence (top wins):
       FORCE_COLOR truthy -> True; else NO_COLOR present -> False;
       else TERM in ('', 'dumb') -> False; else stream.isatty()."""

    def setUp(self):
        self.scry = h.load_scry()

    def test_force_color_wins_even_with_no_color(self):
        # FORCE_COLOR is checked FIRST: it returns True even if NO_COLOR is also
        # set and even on a non-tty stream.
        with h.env_vars(FORCE_COLOR="1", NO_COLOR="1", TERM="dumb"):
            self.assertTrue(self.scry.color_enabled(h.FakeTTY(False)))

    def test_force_color_any_truthy_value(self):
        # os.environ.get("FORCE_COLOR") truthiness: any non-empty string is truthy.
        with h.env_vars(FORCE_COLOR="anything", NO_COLOR=None, TERM=None):
            self.assertTrue(self.scry.color_enabled(h.FakeTTY(False)))

    def test_force_color_empty_string_is_falsey(self):
        # FORCE_COLOR="" is falsey, so it does NOT short-circuit; with NO_COLOR
        # also absent and a tty stream we fall through to isatty()==True.
        with h.env_vars(FORCE_COLOR="", NO_COLOR=None, TERM="xterm"):
            self.assertTrue(self.scry.color_enabled(h.FakeTTY(True)))

    def test_no_color_disables_when_force_absent(self):
        # NO_COLOR present (any value, even empty) -> False when FORCE_COLOR absent.
        with h.env_vars(FORCE_COLOR=None, NO_COLOR="", TERM="xterm"):
            self.assertFalse(self.scry.color_enabled(h.FakeTTY(True)))

    def test_no_color_disables_with_value(self):
        with h.env_vars(FORCE_COLOR=None, NO_COLOR="1", TERM="xterm"):
            self.assertFalse(self.scry.color_enabled(h.FakeTTY(True)))

    def test_term_dumb_disables(self):
        # No FORCE_COLOR, no NO_COLOR, TERM=dumb -> False (even on a tty).
        with h.env_vars(FORCE_COLOR=None, NO_COLOR=None, TERM="dumb"):
            self.assertFalse(self.scry.color_enabled(h.FakeTTY(True)))

    def test_term_empty_disables(self):
        # TERM="" is treated like dumb -> False.
        with h.env_vars(FORCE_COLOR=None, NO_COLOR=None, TERM=""):
            self.assertFalse(self.scry.color_enabled(h.FakeTTY(True)))

    def test_falls_back_to_isatty_true(self):
        # All three env vars clear (TERM a real terminal) -> stream.isatty().
        with h.env_vars(FORCE_COLOR=None, NO_COLOR=None, TERM="xterm-256color"):
            self.assertTrue(self.scry.color_enabled(h.FakeTTY(True)))

    def test_falls_back_to_isatty_false(self):
        with h.env_vars(FORCE_COLOR=None, NO_COLOR=None, TERM="xterm-256color"):
            self.assertFalse(self.scry.color_enabled(h.FakeTTY(False)))

    def test_isatty_raising_returns_false(self):
        # A stream whose isatty() raises -> caught -> returns False.
        class Boom:
            def isatty(self):
                raise RuntimeError("no isatty here")

        with h.env_vars(FORCE_COLOR=None, NO_COLOR=None, TERM="xterm-256color"):
            self.assertFalse(self.scry.color_enabled(Boom()))


class TestVerTuple(unittest.TestCase):
    """_ver_tuple (scry lines 1214-1220): the run of integers in a version string."""

    def setUp(self):
        self.scry = h.load_scry()

    def test_basic(self):
        self.assertEqual(self.scry._ver_tuple("0.3.0"), (0, 3, 0))

    def test_multidigit_components(self):
        self.assertEqual(self.scry._ver_tuple("1.10.2"), (1, 10, 2))

    def test_ordering_compare(self):
        self.assertGreater(self.scry._ver_tuple("0.4.0"),
                           self.scry._ver_tuple("0.3.9"))

    def test_minor_bump_beats_patch(self):
        # tuple comparison: (1, 10, 2) > (1, 9, 99)
        self.assertGreater(self.scry._ver_tuple("1.10.2"),
                           self.scry._ver_tuple("1.9.99"))

    def test_non_numeric_chars_ignored(self):
        # re.findall(r"\d+") strips a leading 'v' and any non-digit separators.
        self.assertEqual(self.scry._ver_tuple("v2.1"), (2, 1))


class TestUniqLabel(unittest.TestCase):
    """_uniq_label (scry lines 822-828): a label not already in `used`."""

    def setUp(self):
        self.scry = h.load_scry()

    def test_first_use_returns_base_and_records(self):
        used = set()
        self.assertEqual(self.scry._uniq_label("claude", used), "claude")
        self.assertIn("claude", used)

    def test_repeats_get_numeric_suffixes(self):
        used = set()
        self.assertEqual(self.scry._uniq_label("claude", used), "claude")
        self.assertEqual(self.scry._uniq_label("claude", used), "claude-2")
        self.assertEqual(self.scry._uniq_label("claude", used), "claude-3")

    def test_used_set_grows_each_call(self):
        used = set()
        self.scry._uniq_label("m", used)
        self.assertEqual(used, {"m"})
        self.scry._uniq_label("m", used)
        self.assertEqual(used, {"m", "m-2"})
        self.scry._uniq_label("m", used)
        self.assertEqual(used, {"m", "m-2", "m-3"})

    def test_empty_base_defaults_to_member(self):
        used = set()
        self.assertEqual(self.scry._uniq_label("", used), "member")
        self.assertIn("member", used)

    def test_empty_base_collision_behavior(self):
        # The "member" default now applies to the suffixed labels too, so repeated
        # empty-base calls yield "member", "member-2", "member-3" (not "-2"/"-3").
        used = set()
        self.assertEqual(self.scry._uniq_label("", used), "member")
        self.assertEqual(self.scry._uniq_label("", used), "member-2")
        self.assertEqual(self.scry._uniq_label("", used), "member-3")
        self.assertEqual(used, {"member", "member-2", "member-3"})

    def test_pre_seeded_used_set_skips_taken_label(self):
        used = {"agy"}
        self.assertEqual(self.scry._uniq_label("agy", used), "agy-2")
        self.assertEqual(used, {"agy", "agy-2"})


if __name__ == "__main__":
    unittest.main()
