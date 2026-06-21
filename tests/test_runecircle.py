"""Tests for the `scry init` welcome sigil: RuneCircle, _welcome_lines,
_compose_welcome, show_init_welcome (scry lines ~1134-1300).

Mirrors and extends the smoke.sh RuneCircle block. No model CLI is ever invoked;
the only "side effect" is writing a static splash to a fake stderr stream.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402

# Strip ANSI SGR sequences (the same regex the smoke test uses).
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class TestRuneCircle(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.RC = self.scry.RuneCircle
        self.circle = self.RC()

    # -- render() shape + well-formed ANSI across build + idle ---------------- #
    def test_render_shape_and_ansi_all_frames(self):
        RC = self.RC
        for f in range(RC.BUILD + 30):
            plain = self.circle.render(f, color=False)
            colored = self.circle.render(f, color=True)
            # exactly ROWS lines
            self.assertEqual(len(plain), RC.ROWS, (f, len(plain)))
            self.assertEqual(len(colored), RC.ROWS, (f, len(colored)))
            # each plain line has the fixed visible width COLS
            for ln in plain:
                self.assertEqual(len(ln), RC.COLS, (f, repr(ln)))
            # colored lines, with ANSI stripped, equal the plain lines
            # (well-formed ANSI, no escape leaks / no stray bytes)
            stripped = [_ANSI.sub("", ln) for ln in colored]
            self.assertEqual(stripped, plain, f)

    def test_render_default_color_is_true(self):
        # render() defaults color=True -> should contain ANSI on a built frame.
        out = self.circle.render(self.RC.BUILD)
        self.assertTrue(any("\x1b[" in ln for ln in out))

    # -- the eye opens on the settled frame ---------------------------------- #
    def test_eye_open_on_build_frame(self):
        joined = "".join(self.circle.render(self.RC.BUILD, color=False))
        self.assertIn("◉", joined)  # "◉"

    def test_eye_closed_glyph_before_eye_phase(self):
        # On frame 0 the eye is the dim placeholder "·", not the open "◉".
        joined = "".join(self.circle.render(0, color=False))
        self.assertNotIn("◉", joined)
        self.assertIn("·", joined)  # "·"

    # -- _col(h,s,v) truecolor SGR format ------------------------------------ #
    def test_col_format(self):
        s = self.RC._col(292, 0.62, 1.0)
        self.assertTrue(s.startswith("\x1b[38;2;"))
        self.assertTrue(s.endswith("m"))
        m = re.fullmatch(r"\x1b\[38;2;(\d+);(\d+);(\d+)m", s)
        self.assertIsNotNone(m, s)
        r, g, b = (int(x) for x in m.groups())
        for v in (r, g, b):
            self.assertGreaterEqual(v, 0)
            self.assertLessEqual(v, 255)

    def test_col_spans_hue_range(self):
        # _col handles the full hue circle (the idle drift uses 258 +/- 18) and
        # the wrapping index used inside the renderer (e.g. h=258, 45, 188).
        for hue in (0, 45, 188, 240, 258, 276, 292, 359):
            s = self.RC._col(hue, 0.55, 0.5)
            m = re.fullmatch(r"\x1b\[38;2;(\d+);(\d+);(\d+)m", s)
            self.assertIsNotNone(m, (hue, s))
            for v in m.groups():
                self.assertTrue(0 <= int(v) <= 255, (hue, v))


class TestWelcomeLines(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_animate_vs_static_last_line_differs(self):
        anim = self.scry._welcome_lines(on=True, animate=True)
        static = self.scry._welcome_lines(on=True, animate=False)
        self.assertIsInstance(anim, list)
        self.assertIsInstance(static, list)
        # same number of lines; only the final call-to-action differs
        self.assertEqual(len(anim), len(static))
        self.assertNotEqual(anim[-1], static[-1])
        # the actual copy (strip ANSI since on=True)
        self.assertIn("press Enter to choose your models", _ANSI.sub("", anim[-1]))
        self.assertIn("let's choose your models", _ANSI.sub("", static[-1]))
        # every other line is identical between the two
        self.assertEqual(anim[:-1], static[:-1])

    def test_on_false_has_no_ansi(self):
        for animate in (True, False):
            lines = self.scry._welcome_lines(on=False, animate=animate)
            self.assertIsInstance(lines, list)
            for ln in lines:
                self.assertNotIn("\x1b", ln, (animate, repr(ln)))

    def test_on_true_has_ansi(self):
        lines = self.scry._welcome_lines(on=True, animate=True)
        self.assertTrue(any("\x1b[" in ln for ln in lines))
        # the headline "Welcome to scry" text survives stripping
        plain = [_ANSI.sub("", ln) for ln in lines]
        self.assertIn("Welcome to scry", plain)


class TestComposeWelcome(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.RC = self.scry.RuneCircle

    def test_compose_basic(self):
        art = self.RC().render(self.RC.BUILD, color=False)
        text = self.scry._welcome_lines(on=False, animate=False)
        rows = self.scry._compose_welcome(art, text)
        self.assertIsInstance(rows, list)
        for row in rows:
            self.assertIsInstance(row, str)
        # at least as many rows as the taller of (padded art, text)
        self.assertGreaterEqual(len(rows), max(len(art), len(text)))

    def test_compose_row_count_matches_text_when_text_taller(self):
        # text (12 lines) is taller than art (ROWS=5), so rows == len(text).
        art = self.RC().render(self.RC.BUILD, color=False)
        text = self.scry._welcome_lines(on=False, animate=True)
        self.assertGreater(len(text), len(art))
        rows = self.scry._compose_welcome(art, text)
        self.assertEqual(len(rows), len(text))
        # each composed row carries the 2-space gutter + the text column
        for row, t in zip(rows, text):
            self.assertTrue(row.startswith("  "))
            if t:
                self.assertTrue(row.endswith(t), (repr(row), repr(t)))

    def test_compose_art_taller_than_text(self):
        # When art is taller than text, rows == len(art) (or padded full).
        art = ["AAAAAAA"] * 20
        text = ["one", "two"]
        rows = self.scry._compose_welcome(art, text)
        self.assertEqual(len(rows), len(art))
        # art content appears in the composed rows
        self.assertTrue(any("AAAAAAA" in r for r in rows))


class TestShowInitWelcome(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_static_path_writes_splash_non_tty(self):
        fake = h.FakeTTY(tty=False)
        orig = sys.stderr
        sys.stderr = fake
        self.addCleanup(setattr, sys, "stderr", orig)
        # Ensure FORCE_COLOR doesn't force the color/animate branch; clear
        # SCRY_NO_ANIM so we exercise the no_anim=True argument path itself.
        with h.env_vars(FORCE_COLOR=None, SCRY_NO_ANIM=None):
            ret = self.scry.show_init_welcome(no_anim=True)
        self.assertIsNone(ret)
        out = fake.getvalue()
        self.assertTrue(out)  # non-empty splash written
        # static path -> the static call-to-action, never the "press Enter" one,
        # and never the cursor-hide sequence the animated path emits.
        plain = _ANSI.sub("", out)
        self.assertIn("Welcome to scry", plain)
        self.assertIn("let's choose your models", plain)
        self.assertNotIn("press Enter to choose your models", plain)
        self.assertNotIn("\x1b[?25l", out)  # cursor not hidden (no animation)

    def test_no_anim_false_but_non_tty_still_static(self):
        # Even with no_anim=False, a non-TTY stderr (isatty False) forces the
        # static path: it must not touch stdin and must return None.
        fake = h.FakeTTY(tty=False)
        orig = sys.stderr
        sys.stderr = fake
        self.addCleanup(setattr, sys, "stderr", orig)
        with h.env_vars(FORCE_COLOR=None, SCRY_NO_ANIM=None):
            ret = self.scry.show_init_welcome(no_anim=False)
        self.assertIsNone(ret)
        self.assertTrue(fake.getvalue())
        self.assertNotIn("\x1b[?25l", fake.getvalue())


if __name__ == "__main__":
    unittest.main()
