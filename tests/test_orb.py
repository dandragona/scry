#!/usr/bin/env python3
"""Tests for scry.ScryingOrb — the 'scrying orb' progress animation.

The orb is pure eye-candy that paints to an interactive stderr on a background
thread. The contract we lock down here: its color/RGB helpers produce valid
truecolor escape codes, _render returns a stable-length list of strings that
includes the caption, and the full start/note/stop lifecycle on a fake TTY
emits output without ever raising (the run loop swallows exceptions by design).

No model CLI is ever touched here — this is all in-process against a FakeTTY.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class TestOrbColor(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_rgb_returns_int_triple_in_range(self):
        Orb = self.scry.ScryingOrb
        cases = [
            (0.0, 0.0, 0.0),     # black
            (0.0, 0.0, 1.0),     # white
            (0.0, 1.0, 1.0),     # pure red
            (120.0, 1.0, 1.0),   # pure green
            (240.0, 1.0, 1.0),   # pure blue
            (60.0, 0.5, 0.5),    # mid
            (300.0, 0.85, 0.78),
            (232.0, 0.55, 0.99),
            (359.999, 1.0, 1.0),
        ]
        for hh, s, v in cases:
            rgb = Orb._rgb(hh, s, v)
            self.assertIsInstance(rgb, tuple, f"{(hh, s, v)} -> {rgb!r}")
            self.assertEqual(len(rgb), 3)
            for comp in rgb:
                self.assertIsInstance(comp, int)
                self.assertGreaterEqual(comp, 0, f"{(hh, s, v)} -> {rgb!r}")
                self.assertLessEqual(comp, 255, f"{(hh, s, v)} -> {rgb!r}")

    def test_rgb_known_primaries(self):
        Orb = self.scry.ScryingOrb
        self.assertEqual(Orb._rgb(0.0, 0.0, 0.0), (0, 0, 0))
        self.assertEqual(Orb._rgb(0.0, 0.0, 1.0), (255, 255, 255))
        self.assertEqual(Orb._rgb(0.0, 1.0, 1.0), (255, 0, 0))
        self.assertEqual(Orb._rgb(120.0, 1.0, 1.0), (0, 255, 0))
        self.assertEqual(Orb._rgb(240.0, 1.0, 1.0), (0, 0, 255))

    def test_color_returns_truecolor_escape(self):
        orb = self.scry.ScryingOrb(h.FakeTTY(tty=True))
        for hh, s, v in [(0.0, 0.0, 0.0), (232.0, 0.55, 0.78),
                         (400.0, 0.85, 0.95), (-30.0, 0.6, 0.4)]:
            esc = orb._color(hh, s, v)
            self.assertIsInstance(esc, str)
            self.assertTrue(esc.startswith("\x1b[38;2;"),
                            f"{(hh, s, v)} -> {esc!r}")
            self.assertTrue(esc.endswith("m"), f"{(hh, s, v)} -> {esc!r}")
            # body is exactly three ints separated by semicolons
            body = esc[len("\x1b[38;2;"):-1]
            parts = body.split(";")
            self.assertEqual(len(parts), 3, f"{esc!r}")
            for p in parts:
                self.assertTrue(p.isdigit(), f"{esc!r}")
                self.assertTrue(0 <= int(p) <= 255, f"{esc!r}")


class TestOrbRender(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.orb = self.scry.ScryingOrb(h.FakeTTY(tty=True))

    def test_render_returns_list_with_caption(self):
        lines = self.orb._render(0, "gazing…", 1.0)
        self.assertIsInstance(lines, list)
        self.assertTrue(all(isinstance(ln, str) for ln in lines))
        self.assertIn("gazing…", "\n".join(lines))

    def test_render_includes_elapsed_and_caption_second_frame(self):
        lines = self.orb._render(50, "synthesis…", 12.0)
        self.assertIsInstance(lines, list)
        self.assertTrue(all(isinstance(ln, str) for ln in lines))
        joined = "\n".join(lines)
        self.assertIn("synthesis…", joined)
        # elapsed is rendered as a right-justified integer-seconds field "  12s"
        self.assertIn("12s", joined)

    def test_render_line_count_stable_across_frames(self):
        # The orb repaints in place by moving the cursor up self._lines rows, so
        # every frame MUST yield the same number of lines regardless of the frame
        # index, caption, or elapsed — otherwise the in-place repaint corrupts.
        counts = set()
        for frame, cap, elapsed in [
            (0, "gazing…", 0.0),
            (1, "gazing…", 1.0),
            (50, "synthesis…", 12.0),
            (137, "a much longer caption that keeps going and going", 999.0),
            (1000, "", 0.4),
        ]:
            lines = self.orb._render(frame, cap, elapsed)
            counts.add(len(lines))
        self.assertEqual(len(counts), 1,
                         f"line count varied across frames: {counts}")

    def test_render_height_matches_geometry(self):
        # H orb rows + 2 pedestal rows + 1 caption row.
        Orb = self.scry.ScryingOrb
        lines = self.orb._render(7, "gazing…", 3.0)
        self.assertEqual(len(lines), Orb.H + 3)

    def test_render_never_raises_on_extreme_inputs(self):
        for frame in (0, 1, 7, 999999, -5):
            for elapsed in (0.0, 0.49, 9999.5, -1.0):
                lines = self.orb._render(frame, "edge…", elapsed)
                self.assertIsInstance(lines, list)


class TestOrbLifecycle(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_start_note_set_caption_stop_paints_without_raising(self):
        tty = h.FakeTTY(tty=True)
        orb = self.scry.ScryingOrb(tty)
        orb.start("go")
        try:
            orb.set_caption("next")
            orb.note("a scrolled line")
            # let at least one background tick paint a frame
            time.sleep(0.05)
        finally:
            orb.stop()
        out = tty.getvalue()
        self.assertTrue(out, "fake stream received no output")
        # the scrolled note survives in the buffer verbatim
        self.assertIn("a scrolled line", out)
        # start() hides the cursor and stop() restores it
        self.assertIn("\x1b[?25l", out)
        self.assertIn("\x1b[?25h", out)

    def test_stop_without_clear_still_restores_cursor(self):
        tty = h.FakeTTY(tty=True)
        orb = self.scry.ScryingOrb(tty)
        orb.start("go")
        time.sleep(0.05)
        orb.stop(clear=False)
        out = tty.getvalue()
        self.assertIn("\x1b[?25h", out)

    def test_note_before_start_does_not_raise(self):
        # note() guards on self._drawn, so it is safe before any tick has painted.
        tty = h.FakeTTY(tty=True)
        orb = self.scry.ScryingOrb(tty)
        orb.note("early line")
        self.assertIn("early line", tty.getvalue())

    def test_stop_is_idempotent(self):
        tty = h.FakeTTY(tty=True)
        orb = self.scry.ScryingOrb(tty)
        orb.start("go")
        time.sleep(0.05)
        orb.stop()
        # a second stop must not raise even though the thread is already joined
        orb.stop()
        self.assertIn("\x1b[?25h", tty.getvalue())


if __name__ == "__main__":
    unittest.main()
