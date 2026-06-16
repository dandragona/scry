"""End-to-end CLI tests for the real ./scry executable, driven as a SUBPROCESS
(h.run_scry) with stub provider binaries injected on PATH via h.StubBins(...).env.

To keep the pipeline single-provider we pass --panel "claude:opus": the panel is
then one claude member, and the DEFAULT_CONFIG judge + aggregator are also
claude:opus, so ONE h.claude_smart() stub plays all three roles (panel proposer /
judge / synthesis) by branching on --append-system-prompt. Because run_scry pipes
stdout/stderr, the process sees a non-tty: streaming + the orb animation stay off,
the fused answer is buffered, and the consensus map auto-print is suppressed unless
--map is passed. --no-anim is passed for belt-and-suspenders.

Nothing here spends money — every model call resolves to a stub.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


# A claude stub whose judge analysis carries a non-empty, multi-field map so that
# render_consensus_map() produces visible output (lets us assert it's printed with
# --map and absent without).
_ANALYSIS = {
    "consensus": ["the sky is blue"],
    "contradictions": ["disagree on shade"],
    "partial_coverage": [],
    "unique_insights": ["only one mentioned rayleigh scattering"],
    "blind_spots": [],
}


def _smart_stub():
    return h.claude_smart(proposer="PROPOSER ANSWER",
                          fused="THE FUSED ANSWER",
                          analysis=_ANALYSIS)


class TestCliE2E(unittest.TestCase):
    # ----------------------------------------------------------------- meta --
    def test_version(self):
        r = h.run_scry(["--version"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("scry "),
                        f"--version stdout should start with 'scry ': {r.stdout!r}")

    def test_help(self):
        r = h.run_scry(["--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage", r.stdout.lower())

    def test_no_prompt_is_argparse_error(self):
        # Empty stdin, non-tty (pipe): read_prompt returns "", main() calls
        # ap.error(...) which exits 2 and writes the message to stderr.
        r = h.run_scry([], input="")
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout!r} stderr={r.stderr!r}")
        self.assertIn("no prompt", r.stderr.lower())

    # ----------------------------------------------------------- happy paths --
    def test_happy_json_run(self):
        with h.StubBins({"claude": _smart_stub()}) as stub:
            r = h.run_scry(["--json", "--panel", "claude:opus", "--no-anim", "hi"],
                           env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "ok")
        self.assertIsInstance(data["responses"], list)
        self.assertEqual(len(data["responses"]), 1)
        self.assertIsInstance(data["analysis"], dict)
        self.assertIn("final", data)
        self.assertEqual(data["final"], "THE FUSED ANSWER")
        # main() strips the internal "streamed" flag under --json.
        self.assertNotIn("streamed", data)

    def test_stdin_prompt_json_run(self):
        # No positional prompt: it is read from (non-tty) stdin instead.
        with h.StubBins({"claude": _smart_stub()}) as stub:
            r = h.run_scry(["--json", "--panel", "claude:opus", "--no-anim"],
                           input="from stdin", env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "ok")
        # The prompt that was fanned out is the stdin text.
        self.assertEqual(data.get("prompt"), "from stdin")

    def test_show_proposers(self):
        # Non --json, animation off: the fused answer prints to stdout and the
        # proposer sections (incl. the "----- fused answer -----" header) go to
        # stderr.
        with h.StubBins({"claude": _smart_stub()}) as stub:
            r = h.run_scry(["--show-proposers", "--panel", "claude:opus",
                            "--no-anim", "hi"], env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        # Fused answer on stdout (buffered, since stdout is a pipe).
        self.assertIn("THE FUSED ANSWER", r.stdout)
        # Proposer dump + the fused-answer separator land on stderr.
        self.assertIn("fused answer", r.stderr)
        self.assertIn("-----", r.stderr)
        self.assertIn("PROPOSER ANSWER", r.stderr)

    # ---------------------------------------------------------- failure path --
    def test_all_panels_fail_json(self):
        # Every proposer exits non-zero with a "rate limit" message -> the panel
        # all-fails and the failure_reason classifies to "rate_limited".
        with h.StubBins({"claude": h.fail(1, "rate limit")}) as stub:
            r = h.run_scry(["--json", "--panel", "claude:opus", "--no-anim", "hi"],
                           env=stub.env)
        self.assertEqual(r.returncode, 1, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["failure_reason"], "rate_limited")

    # ---------------------------------------------------- consensus map gate --
    def test_map_flag_prints_consensus_map(self):
        # --map forces the consensus map onto stderr even on a non-tty.
        with h.StubBins({"claude": _smart_stub()}) as stub:
            r = h.run_scry(["--map", "--panel", "claude:opus", "--no-anim", "hi"],
                           env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        self.assertIn("consensus map", r.stderr)
        # Final answer still goes to stdout.
        self.assertIn("THE FUSED ANSWER", r.stdout)

    def test_no_map_flag_default_on_nontty(self):
        # Without --map, stderr is a pipe (non-tty) so the consensus map auto-print
        # is suppressed.
        with h.StubBins({"claude": _smart_stub()}) as stub:
            r = h.run_scry(["--panel", "claude:opus", "--no-anim", "hi"],
                           env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        self.assertNotIn("consensus map", r.stderr)
        self.assertIn("THE FUSED ANSWER", r.stdout)

    # --------------------------------------------------------------- --quiet --
    def test_quiet_suppresses_progress(self):
        # --quiet silences the progress log on stderr (the "▸ panel" stage line),
        # but the final fused answer still prints to stdout.
        with h.StubBins({"claude": _smart_stub()}) as stub:
            r = h.run_scry(["--quiet", "--panel", "claude:opus", "--no-anim", "hi"],
                           env=stub.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        self.assertNotIn("▸ panel", r.stderr)
        # No stage progress at all on stderr.
        self.assertNotIn("panel:", r.stderr)
        self.assertIn("THE FUSED ANSWER", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
