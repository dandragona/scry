"""Tests for `scry plan` — the interactive, panel-driven planning mode.

Layers, mirroring the rest of the suite:
  * pure-helper unit tests (render_plan_prompt, _local_dedup),
  * async unit tests that MONKEYPATCH scry.call_cli (gather_questions,
    dedup_questions) so no model CLI is ever spawned,
  * input-driven unit tests for the one-at-a-time asker (patch builtins.input),
  * end-to-end subprocess tests that drive the REAL ./scry plan with a branching
    `claude_plan` stub on PATH and piped answers (like test_init.py).

Nothing here spends money.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
class RenderPlanPromptTest(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_empty_transcript_states_no_answers(self):
        out = self.scry.render_plan_prompt("build a CLI", [])
        self.assertIn("Original request:\nbuild a CLI", out)
        self.assertIn("No clarifying questions have been answered yet.", out)

    def test_transcript_is_embedded_numbered(self):
        transcript = [{"q": "What platform?", "a": "linux"},
                      {"q": "Any budget?", "a": "no"}]
        out = self.scry.render_plan_prompt("build a CLI", transcript)
        self.assertIn("Original request:\nbuild a CLI", out)
        self.assertIn("Q1: What platform?", out)
        self.assertIn("A1: linux", out)
        self.assertIn("Q2: Any budget?", out)
        self.assertIn("A2: no", out)


class LocalDedupTest(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_dedups_case_insensitively_preserving_order(self):
        qs = [{"q": "Same question"}, {"q": "same QUESTION"}, {"q": "Other"}]
        out = self.scry._local_dedup(qs)
        self.assertEqual([q["q"] for q in out], ["Same question", "Other"])

    def test_drops_blank_questions(self):
        qs = [{"q": "  "}, {"q": "Real"}]
        out = self.scry._local_dedup(qs)
        self.assertEqual([q["q"] for q in out], ["Real"])


# --------------------------------------------------------------------------- #
# gather_questions — async fan-out of the interviewer prompt to the whole panel
# --------------------------------------------------------------------------- #
class GatherQuestionsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.log = lambda *a, **k: None
        self.cfg = {"panel": [
            {"provider": "claude", "model": "opus", "label": "claude-opus"},
            {"provider": "codex", "model": "", "label": "codex-gpt"},
        ]}
        self.settings = {"web_tools": False}

    def _patch_call_cli(self, fake):
        orig = self.scry.call_cli
        self.scry.call_cli = fake
        self.addCleanup(setattr, self.scry, "call_cli", orig)

    async def test_all_ready_returns_empty_and_ready(self):
        scry = self.scry
        seen_systems = []

        async def fake(cfg, provider, model, system, user, cwd, depth, web,
                       settings, meta=None):
            seen_systems.append(system)
            return json.dumps({"ready": True, "questions": []})

        self._patch_call_cli(fake)
        meters = []
        raw, all_ready = await scry.gather_questions(
            self.cfg, "req", [], self.settings, self.log, meters, 0, ".")
        self.assertEqual(raw, [])
        self.assertTrue(all_ready)
        self.assertEqual(len(meters), 2)  # one usage record per panel member
        self.assertTrue(all(s == scry.PLAN_INTERVIEWER_SYSTEM for s in seen_systems))

    async def test_collects_questions_not_ready(self):
        scry = self.scry

        async def fake(cfg, provider, model, system, user, cwd, depth, web,
                       settings, meta=None):
            return json.dumps({"ready": False,
                               "questions": [{"q": f"q-from-{provider}", "why": "x"}]})

        self._patch_call_cli(fake)
        raw, all_ready = await scry.gather_questions(
            self.cfg, "req", [], self.settings, self.log, [], 0, ".")
        self.assertFalse(all_ready)
        self.assertEqual({q["q"] for q in raw}, {"q-from-claude", "q-from-codex"})

    async def test_one_member_raising_does_not_abort(self):
        scry = self.scry

        async def fake(cfg, provider, model, system, user, cwd, depth, web,
                       settings, meta=None):
            if provider == "codex":
                raise scry.ProviderError("codex down")
            return json.dumps({"ready": False, "questions": [{"q": "only-claude"}]})

        self._patch_call_cli(fake)
        raw, all_ready = await scry.gather_questions(
            self.cfg, "req", [], self.settings, self.log, [], 0, ".")
        self.assertEqual([q["q"] for q in raw], ["only-claude"])
        self.assertFalse(all_ready)

    async def test_garbage_json_blocks_readiness(self):
        scry = self.scry

        async def fake(cfg, provider, model, system, user, cwd, depth, web,
                       settings, meta=None):
            if provider == "claude":
                return json.dumps({"ready": True, "questions": []})
            return "not json at all"   # codex: a non-ready responder

        self._patch_call_cli(fake)
        raw, all_ready = await scry.gather_questions(
            self.cfg, "req", [], self.settings, self.log, [], 0, ".")
        self.assertFalse(all_ready)
        self.assertEqual(raw, [])


# --------------------------------------------------------------------------- #
# dedup_questions — the judge merges duplicates; falls back to local dedup
# --------------------------------------------------------------------------- #
class DedupQuestionsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.log = lambda *a, **k: None
        self.cfg = {"judge": {"provider": "claude", "model": "opus"}}
        self.settings = {"web_tools": True}  # interview web is on; dedup must force off

    def _patch_call_cli(self, fake):
        orig = self.scry.call_cli
        self.scry.call_cli = fake
        self.addCleanup(setattr, self.scry, "call_cli", orig)

    async def test_short_circuits_without_calling_judge(self):
        scry = self.scry
        called = []

        async def fake(*a, **k):
            called.append(1)
            return "{}"

        self._patch_call_cli(fake)
        one = [{"q": "only one"}]
        out = await scry.dedup_questions(self.cfg, "req", [], one,
                                         self.settings, self.log, [], 0, ".")
        self.assertEqual(out, one)
        self.assertEqual(called, [])  # no judge call for <= 1 question

    async def test_judge_output_is_used(self):
        scry = self.scry
        seen = {}

        async def fake(cfg, provider, model, system, user, cwd, depth, web,
                       settings, meta=None):
            seen["system"], seen["web"] = system, web
            return json.dumps({"questions": [{"q": "merged-A"}, {"q": "B"}]})

        self._patch_call_cli(fake)
        raw = [{"q": "A"}, {"q": "a"}, {"q": "B"}]
        out = await scry.dedup_questions(self.cfg, "req", [], raw,
                                         self.settings, self.log, [], 0, ".")
        self.assertEqual([q["q"] for q in out], ["merged-A", "B"])
        self.assertEqual(seen["system"], scry.PLAN_QUESTION_JUDGE_SYSTEM)
        self.assertFalse(seen["web"])  # dedup is not a research task

    async def test_non_json_judge_falls_back_to_local_dedup(self):
        scry = self.scry

        async def fake(*a, **k):
            return "this is not json"

        self._patch_call_cli(fake)
        raw = [{"q": "A"}, {"q": "a"}, {"q": "B"}]
        out = await scry.dedup_questions(self.cfg, "req", [], raw,
                                         self.settings, self.log, [], 0, ".")
        self.assertEqual([q["q"] for q in out], ["A", "B"])  # local dedup

    async def test_judge_raising_falls_back_to_local_dedup(self):
        scry = self.scry

        async def fake(*a, **k):
            raise scry.ProviderError("judge crashed")

        self._patch_call_cli(fake)
        raw = [{"q": "A"}, {"q": "a"}, {"q": "B"}]
        out = await scry.dedup_questions(self.cfg, "req", [], raw,
                                         self.settings, self.log, [], 0, ".")
        self.assertEqual([q["q"] for q in out], ["A", "B"])

    async def test_meter_records_seconds_on_success(self):
        scry = self.scry

        async def fake(*a, **k):
            return json.dumps({"questions": [{"q": "A"}]})

        self._patch_call_cli(fake)
        meters = []
        await scry.dedup_questions(self.cfg, "req", [], [{"q": "A"}, {"q": "B"}],
                                   self.settings, self.log, meters, 0, ".")
        self.assertEqual(len(meters), 1)
        self.assertTrue(meters[0]["ok"])
        self.assertIn("seconds", meters[0])     # every stage is timed now

    async def test_failed_dedup_meter_carries_timeout_reason(self):
        # Regression for the diagnosed run: a dedup that times out must leave a FAILED
        # meter WITH the reason, so the diagnostics can show *why* (not a blank row).
        scry = self.scry

        async def fake(*a, **k):
            raise scry.ProviderError("timeout after 420s")

        self._patch_call_cli(fake)
        meters = []
        await scry.dedup_questions(self.cfg, "req", [], [{"q": "A"}, {"q": "B"}],
                                   self.settings, self.log, meters, 0, ".")
        self.assertEqual(len(meters), 1)
        self.assertFalse(meters[0]["ok"])
        self.assertIn("seconds", meters[0])
        self.assertIn("timeout after 420s", meters[0]["error"])


# --------------------------------------------------------------------------- #
# ask_questions_interactively — one at a time, options + free text, done/EOF
# --------------------------------------------------------------------------- #
class AskQuestionsTest(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def _ask(self, questions, answers, asked_keys=None):
        asked_keys = set() if asked_keys is None else asked_keys
        with patch("builtins.input", side_effect=answers), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            qa, done = self.scry.ask_questions_interactively(questions, asked_keys)
        return qa, done, asked_keys

    def test_free_text_records_answers_and_keys(self):
        qa, done, keys = self._ask(
            [{"q": "Platform?"}, {"q": "Budget?"}], ["linux", "no"])
        self.assertFalse(done)
        self.assertEqual(qa, [{"q": "Platform?", "a": "linux"},
                              {"q": "Budget?", "a": "no"}])
        self.assertEqual(keys, {"platform?", "budget?"})

    def test_numeric_option_resolves_to_text(self):
        qa, done, _ = self._ask(
            [{"q": "OS?", "options": ["linux", "macos"]}], ["2"])
        self.assertEqual(qa[0]["a"], "macos")

    def test_free_text_answer_in_option_question(self):
        qa, done, _ = self._ask(
            [{"q": "OS?", "options": ["linux", "macos"]}], ["freebsd"])
        self.assertEqual(qa[0]["a"], "freebsd")

    def test_multi_numeric_options_resolve_to_text(self):
        # A multi-select answer ("1,3,4,5") must record the chosen option TEXT,
        # not the bare numbers — otherwise the transcript sent to the drafting
        # models says "A: 1,3,4,5" with no idea what those numbers meant.
        qa, done, _ = self._ask(
            [{"q": "Sources?",
              "options": ["X/Twitter", "Reddit", "RSS", "HN", "Mastodon"]}],
            ["1,3,4,5"])
        self.assertEqual(qa[0]["a"], "X/Twitter, RSS, HN, Mastodon")

    def test_multi_numeric_options_space_separated(self):
        qa, done, _ = self._ask(
            [{"q": "OS?", "options": ["linux", "macos", "windows"]}], ["1 3"])
        self.assertEqual(qa[0]["a"], "linux, windows")

    def test_multi_numeric_dedups_preserving_order(self):
        qa, done, _ = self._ask(
            [{"q": "OS?", "options": ["linux", "macos", "windows"]}], ["3, 1, 3"])
        self.assertEqual(qa[0]["a"], "windows, linux")

    def test_multi_numeric_out_of_range_kept_as_free_text(self):
        # Not a clean list of in-range numbers → leave it verbatim (free text).
        qa, done, _ = self._ask(
            [{"q": "OS?", "options": ["linux", "macos"]}], ["1,9"])
        self.assertEqual(qa[0]["a"], "1,9")

    def test_done_sentinel_stops_immediately(self):
        qa, done, _ = self._ask([{"q": "A"}, {"q": "B"}], ["done"])
        self.assertTrue(done)
        self.assertEqual(qa, [])

    def test_done_after_one_answer_keeps_it(self):
        qa, done, _ = self._ask([{"q": "A"}, {"q": "B"}], ["yes", "done"])
        self.assertTrue(done)
        self.assertEqual(qa, [{"q": "A", "a": "yes"}])

    def test_skips_already_asked_keys(self):
        qa, done, _ = self._ask([{"q": "A"}, {"q": "B"}], ["only-b"],
                                asked_keys={"a"})
        self.assertFalse(done)
        self.assertEqual(qa, [{"q": "B", "a": "only-b"}])

    def test_eof_is_treated_as_done(self):
        qa, done, _ = self._ask([{"q": "A"}], EOFError())
        self.assertTrue(done)
        self.assertEqual(qa, [])

    def test_renders_question_before_why_and_options(self):
        # Natural reading order: the question first, then its rationale, then the
        # numbered options, then the bare input prompt — not options-before-question.
        err = io.StringIO()
        with patch("builtins.input", side_effect=["1"]), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            self.scry.ask_questions_interactively(
                [{"q": "Which store?", "why": "decides the source",
                  "options": ["history", "scan runs"]}], set())
        out = err.getvalue()
        self.assertLess(out.index("Which store?"), out.index("decides the source"))
        self.assertLess(out.index("decides the source"), out.index("1. history"))
        self.assertLess(out.index("1. history"), out.index("type your own"))
        self.assertEqual(out.count("Which store?"), 1)  # not repeated in the prompt


# --------------------------------------------------------------------------- #
# End-to-end: drive the REAL ./scry plan as a subprocess with a branching
# claude_plan stub on PATH and piped answers (mirrors test_init.py). A claude-only
# panel/judge/aggregator (via overrides) lets the one stub play every role.
# --------------------------------------------------------------------------- #
class PlanSubprocessTest(unittest.TestCase):
    REQUEST = "build a rate limiter"

    def _env(self, stub, **extra_env):
        s = h.StubBins({"claude": stub})
        self.addCleanup(shutil.rmtree, s.dir, ignore_errors=True)
        env = s.env
        # Sandbox history/checkpoints to a temp dir so plan runs (and the resume
        # checkpoints they now write) never touch the real ~/.scry.
        home = tempfile.mkdtemp(prefix="scry-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        env["SCRY_HOME"] = home
        env.update(extra_env)   # a test may override SCRY_HOME to inspect it
        return env

    def _args(self, *extra, panel="claude:opus"):
        return ["plan", self.REQUEST, "--no-anim",
                "--panel", panel, "--judge", "claude:opus",
                "--aggregator", "claude:opus", *extra]

    def _json_run(self, stub, stdin, *extra, panel="claude:opus", env=None):
        env = env or self._env(stub)
        cp = h.run_scry(self._args("--json", *extra, panel=panel),
                        input=stdin, env=env)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        return json.loads(cp.stdout), cp

    # ----- converges to a plan ---------------------------------------------- #
    def test_converges_and_prints_plan(self):
        env = self._env(h.claude_plan(rounds_before_ready=1))
        cp = h.run_scry(self._args(), input="linux\nbudget-ok\n", env=env)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        self.assertIn("## Context", cp.stdout)

    def test_json_record_shape(self):
        rec, _ = self._json_run(h.claude_plan(rounds_before_ready=1),
                                "linux\nbudget-ok\n")
        self.assertEqual(rec["mode"], "plan")
        self.assertEqual(rec["rounds"], 2)
        self.assertEqual(len(rec["transcript"]), 2)
        self.assertIn("## Context", rec["final"])
        for key in ("status", "prompt", "responses", "transcript", "rounds",
                    "final", "cost"):
            self.assertIn(key, rec)
        self.assertEqual(rec["prompt"], self.REQUEST)  # original request, not enriched

    # ----- the panel drafters are told to WRITE a plan, not implement ------- #
    def test_final_draft_panel_receives_drafter_system(self):
        # Regression for the half-strength plan panel: each final-draft proposer must
        # be handed PLAN_DRAFTER_SYSTEM. Without it an agentic CLI tries to EXECUTE the
        # task (churns tool calls / attempts file writes in the repo cwd) instead of
        # drafting a plan as text, which is what made opus time out at 0 tokens.
        scry = h.load_scry()
        d = tempfile.mkdtemp(prefix="scry-sysdump-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        dump = os.path.join(d, "sys.txt")
        env = self._env(h.claude_plan(rounds_before_ready=1), SCRY_SYSDUMP=dump)
        cp = h.run_scry(self._args(), input="linux\nok\n", env=env)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        with open(dump) as f:
            seen = f.read()
        # The drafter prompt reached the panel, and it is distinct from the synth one.
        self.assertIn(scry.PLAN_DRAFTER_SYSTEM, seen)
        self.assertNotEqual(scry.PLAN_DRAFTER_SYSTEM, scry.PLAN_SYNTH_SYSTEM)

    # ----- plan = research + a plan phase ----------------------------------- #
    def test_plan_researches_then_drafts_from_the_synthesis(self):
        # Plan finalize now runs the deep-research pipeline first, then feeds its
        # synthesis to the drafters. Assert (a) the research phase ran (its synthesis
        # system prompt shows up alongside the drafter's), and (b) the drafter prompt
        # carried the research synthesis text — not just the bare interview transcript.
        scry = h.load_scry()
        d = tempfile.mkdtemp(prefix="scry-research-plan-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        sysdump = os.path.join(d, "sys.txt")
        draftdump = os.path.join(d, "draft.txt")
        env = self._env(
            h.claude_plan(rounds_before_ready=1, research_final="RESEARCH-MARKER-XYZ"),
            SCRY_SYSDUMP=sysdump, SCRY_DRAFTDUMP=draftdump)
        cp = h.run_scry(self._args(), input="linux\nok\n", env=env)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        with open(sysdump) as f:
            seen = f.read()
        self.assertIn(scry.RESEARCH_SYNTH_SYSTEM, seen)   # the research phase ran
        self.assertIn(scry.PLAN_DRAFTER_SYSTEM, seen)     # the drafters ran too
        with open(draftdump) as f:
            draft_in = f.read()
        # The drafters were handed the research synthesis, not just the Q&A transcript.
        self.assertIn("RESEARCH-MARKER-XYZ", draft_in)

    # ----- done sentinel stops early ---------------------------------------- #
    def test_done_sentinel_stops_in_round_one(self):
        rec, _ = self._json_run(h.claude_plan(rounds_before_ready=99), "done\n")
        self.assertEqual(rec["rounds"], 1)
        self.assertEqual(rec["transcript"], [])
        self.assertIn("## Context", rec["final"])

    # ----- max-rounds cap (questions are unique each round) ------------------ #
    def test_max_rounds_cap(self):
        rec, _ = self._json_run(
            h.claude_plan(rounds_before_ready=99, unique_each_round=True),
            "a\na\na\na\n", "--interview-rounds", "2")
        self.assertEqual(rec["rounds"], 2)
        self.assertIn("## Context", rec["final"])

    # ----- no new questions terminates -------------------------------------- #
    def test_no_new_questions_terminates(self):
        # never "ready", but the same two questions every round -> round 2 yields
        # no NEW questions (asked_keys filters them) -> stop and synthesize.
        rec, _ = self._json_run(
            h.claude_plan(rounds_before_ready=99), "linux\nok\n", "--interview-rounds", "9")
        self.assertEqual(rec["rounds"], 2)
        self.assertEqual(len(rec["transcript"]), 2)

    # ----- the judge dedups duplicate questions across panel members -------- #
    def test_dedup_collapses_duplicate_questions(self):
        # Two claude members propose the SAME two questions -> raw has 4; the judge
        # dedups to 2, so round 1 asks exactly 2 and round 2 reaches "ready".
        # If dedup had failed (4 asked) we'd exhaust the 2 answers and bail via EOF
        # in round 1 (rounds == 1) -> asserting rounds == 2 proves the dedup.
        rec, _ = self._json_run(
            h.claude_plan(rounds_before_ready=1), "linux\nok\n",
            panel="claude:opus,claude:sonnet")
        self.assertEqual(rec["rounds"], 2)
        self.assertEqual(len(rec["transcript"]), 2)

    # ----- --out writes the plan file --------------------------------------- #
    def test_out_writes_plan_file(self):
        d = tempfile.mkdtemp(prefix="scry-plan-out-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = os.path.join(d, "nested", "plan.md")     # parent created on demand
        env = self._env(h.claude_plan(rounds_before_ready=1))
        cp = h.run_scry(self._args("--out", out),
                        input="linux\nbudget-ok\n", env=env)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        with open(out) as f:
            written = f.read()
        self.assertIn("## Context", written)
        self.assertIn("## Context", cp.stdout)         # still printed too

    # ----- default: writes plan + diagnostics into the cwd (no --out) ------- #
    def test_default_writes_plan_and_diagnostics_to_cwd(self):
        d = tempfile.mkdtemp(prefix="scry-plan-cwd-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        env = self._env(h.claude_plan(rounds_before_ready=1))
        cp = h.run_scry(self._args(), input="linux\nok\n", env=env, cwd=d)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        files = os.listdir(d)
        plans = [f for f in files if f.startswith("scry-plan-")
                 and f.endswith(".md") and not f.endswith(".diagnostics.md")]
        diags = [f for f in files if f.endswith(".diagnostics.md")]
        self.assertEqual(len(plans), 1, files)
        self.assertEqual(len(diags), 1, files)
        with open(os.path.join(d, plans[0])) as f:
            self.assertIn("## Context", f.read())
        with open(os.path.join(d, diags[0])) as f:
            diag = f.read()
        self.assertIn("diagnostics", diag.lower())
        self.assertIn("## settings", diag)

    # ----- default name is a topic slug, not a bare timestamp -------------- #
    def test_default_plan_filename_is_topic_slug(self):
        d = tempfile.mkdtemp(prefix="scry-plan-name-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        env = self._env(h.claude_plan(rounds_before_ready=1))
        cp = h.run_scry(self._args(), input="linux\nok\n", env=env, cwd=d)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        plan = [f for f in os.listdir(d) if f.startswith("scry-plan-")
                and f.endswith(".md") and not f.endswith(".diagnostics.md")][0]
        # The title call (stub's panel-proposer branch -> "PLAN DRAFT") yields a slug,
        # so the name is words — NOT the old `scry-plan-<13-digit-timestamp>.md`.
        self.assertNotRegex(plan, r"^scry-plan-\d+\.md$")
        self.assertRegex(plan, r"^scry-plan-[a-z0-9-]+\.md$")

    # ----- --no-out: print only, leave no files behind --------------------- #
    def test_no_out_writes_nothing(self):
        d = tempfile.mkdtemp(prefix="scry-plan-noout-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        env = self._env(h.claude_plan(rounds_before_ready=1))
        cp = h.run_scry(self._args("--out", "-"), input="linux\nok\n", env=env, cwd=d)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        self.assertEqual([f for f in os.listdir(d) if f.startswith("scry-plan-")], [])
        self.assertIn("## Context", cp.stdout)            # still printed to stdout

    # ----- --out: diagnostics file rides alongside the plan ---------------- #
    def test_out_writes_diagnostics_alongside(self):
        d = tempfile.mkdtemp(prefix="scry-plan-out-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = os.path.join(d, "myplan.md")
        env = self._env(h.claude_plan(rounds_before_ready=1))
        cp = h.run_scry(self._args("--out", out), input="linux\nok\n", env=env, cwd=d)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        self.assertTrue(os.path.exists(out))
        self.assertTrue(os.path.exists(os.path.join(d, "myplan.diagnostics.md")))

    # ----- diagnostics show the research phase as its own timeline segment --- #
    def test_diagnostics_show_a_research_segment(self):
        d = tempfile.mkdtemp(prefix="scry-plan-diag-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = os.path.join(d, "myplan.md")
        env = self._env(h.claude_plan(rounds_before_ready=1))
        cp = h.run_scry(self._args("--out", out), input="linux\nok\n", env=env, cwd=d)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        with open(os.path.join(d, "myplan.diagnostics.md")) as f:
            diag = f.read()
        self.assertIn("## timeline", diag)
        # The research phase is a distinct timeline segment, not folded into the draft.
        self.assertIn("**research**", diag)

    # ----- history records mode "plan"; `scry last` reprints it ------------- #
    def test_history_saved_as_plan_mode(self):
        home = tempfile.mkdtemp(prefix="scry-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        env = self._env(h.claude_plan(rounds_before_ready=1), SCRY_HOME=home)
        cp = h.run_scry(self._args(), input="linux\nbudget-ok\n", env=env)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)

        hist = os.path.join(home, "history.jsonl")
        with open(hist) as f:
            last = json.loads(f.read().splitlines()[-1])
        self.assertEqual(last["mode"], "plan")
        self.assertEqual(last["prompt"], self.REQUEST)

        # `scry last` reprints the plan from the saved transcript.
        last_cp = h.run_scry(["last"], env=env)
        self.assertEqual(last_cp.returncode, 0, last_cp.stderr)
        self.assertIn("## Context", last_cp.stdout)

    def _cwd_by_stage(self, cwddump):
        """Parse a SCRY_CWDDUMP file into {stage: set(cwds)}."""
        by_stage = {}
        with open(cwddump) as f:
            for ln in f.read().splitlines():
                if "\t" not in ln:
                    continue
                stage, cwd = ln.split("\t", 1)
                by_stage.setdefault(stage, set()).add(cwd)
        return by_stage

    # ----- repo context: panelists read the repo; the synthesis is tool-free ---- #
    def test_repo_context_panel_in_repo_but_synth_is_scrubbed(self):
        # The panel DRAFTS in the invocation dir (so plans are grounded in the code), but
        # the judge + synthesis only fuse the drafts they're handed — they run in a
        # scrubbed throwaway cwd so the "final draft" makes no repo tool calls.
        repo = tempfile.mkdtemp(prefix="scry-fake-repo-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        real = os.path.realpath(repo)
        d = tempfile.mkdtemp(prefix="scry-cwddump-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        dump = os.path.join(d, "cwd.txt")
        env = self._env(h.claude_plan(rounds_before_ready=1, report_cwd=True),
                        SCRY_CWDDUMP=dump)
        cp = h.run_scry(self._args(), input="ok\n", env=env, cwd=repo)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        by_stage = self._cwd_by_stage(dump)
        # Panel drafts + interview ran in the repo:
        self.assertIn(real, by_stage.get("draft", set()))
        self.assertIn(real, by_stage.get("interview", set()))
        # Synthesis (and judge) fused in a scrubbed temp cwd, NOT the repo:
        self.assertTrue(by_stage.get("synth"))
        self.assertTrue(all("scry-fuse-" in c and real not in c
                            for c in by_stage["synth"]), by_stage.get("synth"))
        self.assertTrue(all(real not in c for c in by_stage.get("judge", set())))

    def test_no_repo_uses_scrubbed_temp_cwd(self):
        repo = tempfile.mkdtemp(prefix="scry-fake-repo-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        real = os.path.realpath(repo)
        d = tempfile.mkdtemp(prefix="scry-cwddump-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        dump = os.path.join(d, "cwd.txt")
        env = self._env(h.claude_plan(rounds_before_ready=1, report_cwd=True),
                        SCRY_CWDDUMP=dump)
        cp = h.run_scry(self._args("--repo", "none"), input="ok\n", env=env, cwd=repo)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        self.assertNotIn(real, cp.stdout)              # final draft did NOT see the repo
        by_stage = self._cwd_by_stage(dump)
        # Panel drafts ran in their own scrubbed run cwd; synthesis in the fuse cwd.
        self.assertTrue(all("scry-run-" in c and real not in c
                            for c in by_stage.get("draft", set())), by_stage.get("draft"))
        self.assertTrue(all("scry-fuse-" in c and real not in c
                            for c in by_stage.get("synth", set())), by_stage.get("synth"))
        self.assertNotIn(real, cp.stderr)              # interview didn't see the repo
        self.assertIn("scry-plan-", cp.stderr)         # interview used a temp cwd

    # ----- no animation when stderr isn't a tty (subprocess pipe) ----------- #
    def test_no_orb_escape_codes_on_non_tty(self):
        env = self._env(h.claude_plan(rounds_before_ready=1))
        # NOTE: no --no-anim here; degradation must come from the non-tty check.
        cp = h.run_scry(["plan", self.REQUEST, "--panel", "claude:opus",
                         "--judge", "claude:opus", "--aggregator", "claude:opus"],
                        input="linux\nbudget-ok\n", env=env)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        self.assertNotIn("\x1b[?25l", cp.stderr)  # cursor-hide never emitted


# --------------------------------------------------------------------------- #
# `scry plan --resume` — continue an unfinished planning session. Sandboxed to a
# temp SCRY_HOME; checkpoints are either pre-written or produced by a run whose
# final synthesis is made to fail (leaving an unfinished checkpoint behind).
# --------------------------------------------------------------------------- #
class ResumeSubprocessTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="scry-home-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        os.makedirs(os.path.join(self.home, "runs"), exist_ok=True)

    def _env(self, stub):
        s = h.StubBins({"claude": stub})
        self.addCleanup(shutil.rmtree, s.dir, ignore_errors=True)
        env = s.env
        env["SCRY_HOME"] = self.home    # shared across runs so resume sees the checkpoint
        return env

    def _base(self, *extra):
        return ["plan", *extra, "--no-anim", "--repo", "none",
                "--panel", "claude:opus", "--judge", "claude:opus",
                "--aggregator", "claude:opus"]

    def _write_checkpoint(self, run_id, prompt="resume me", transcript=None,
                          interview_done=True):
        if transcript is None:
            transcript = [{"q": "What platform?", "a": "linux"}]
        rec = {"mode": "plan", "status": "unfinished", "prompt": prompt,
               "transcript": transcript,
               "asked_keys": [t["q"].lower() for t in transcript],
               "meters": [], "rounds": 2, "pending_qs": None,
               "all_ready": False, "interview_done": interview_done, "ts": 1718000000.0}
        with open(os.path.join(self.home, "runs", f"{run_id}.json"), "w") as f:
            json.dump(rec, f)
        with open(os.path.join(self.home, "history.jsonl"), "a") as f:
            f.write(json.dumps({"ts": 1718000000.0, "file": f"{run_id}.json",
                                "prompt": prompt, "mode": "plan",
                                "unfinished": True}) + "\n")

    def _last_history(self):
        with open(os.path.join(self.home, "history.jsonl")) as f:
            return json.loads(f.read().splitlines()[-1])

    def test_resume_with_no_session_errors(self):
        env = self._env(h.claude_plan())
        cp = h.run_scry(self._base("--resume"), env=env)
        self.assertEqual(cp.returncode, 1)
        self.assertIn("no unfinished plan session", cp.stderr)

    def test_resume_loads_transcript_and_completes(self):
        self._write_checkpoint("1700000000001", prompt="design a cache",
                               interview_done=True)
        env = self._env(h.claude_plan())
        cp = h.run_scry(self._base("--resume"), env=env)   # no stdin: interview is done
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        self.assertIn("## Context", cp.stdout)             # synthesized from saved transcript
        self.assertNotIn("unfinished", json.dumps(self._last_history()))  # now finished

    def test_resume_by_explicit_id(self):
        self._write_checkpoint("1700000000777", prompt="design a cache")
        env = self._env(h.claude_plan())
        cp = h.run_scry(self._base("--resume=1700000000777"), env=env)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        self.assertIn("## Context", cp.stdout)

    def test_resume_rejects_conflicting_prompt(self):
        self._write_checkpoint("1700000000002", prompt="the original request")
        env = self._env(h.claude_plan())
        # prompt BEFORE --resume so argparse keeps it as the positional, not the id
        cp = h.run_scry(self._base("a different request", "--resume"), env=env)
        self.assertEqual(cp.returncode, 1)
        self.assertIn("cannot combine --resume with a new prompt", cp.stderr)

    def test_interrupted_run_is_resumable_then_completes(self):
        # Run 1: interview succeeds, the final synthesis panel fails -> the run aborts
        # (exit 1) leaving an UNFINISHED checkpoint written by _save_state.
        env1 = self._env(h.claude_plan(rounds_before_ready=1, fail_synthesis=True))
        cp1 = h.run_scry(self._base("build a rate limiter"),
                         input="linux\nyes\n", env=env1)
        self.assertEqual(cp1.returncode, 1, cp1.stderr + cp1.stdout)
        self.assertTrue(self._last_history().get("unfinished"))  # resumable session exists

        # Run 2: a healthy panel resumes it (skipping the interview) and completes.
        env2 = self._env(h.claude_plan(rounds_before_ready=1))
        cp2 = h.run_scry(self._base("--resume"), env=env2)
        self.assertEqual(cp2.returncode, 0, cp2.stderr + cp2.stdout)
        self.assertIn("## Context", cp2.stdout)
        self.assertNotIn("unfinished", json.dumps(self._last_history()))


# --------------------------------------------------------------------------- #
# `scry plan --list` — lists unfinished, resumable planning sessions.
# Non-billable (makes no model calls); reads the same history.jsonl --resume does.
# --------------------------------------------------------------------------- #
class PlanListSubprocessTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="scry-home-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        os.makedirs(os.path.join(self.home, "runs"), exist_ok=True)

    def _env(self, stub=None):
        # --list spawns NO model: an empty StubBins (no provider on PATH) proves it.
        s = h.StubBins({"claude": stub} if stub is not None else {})
        self.addCleanup(shutil.rmtree, s.dir, ignore_errors=True)
        env = s.env
        env["SCRY_HOME"] = self.home    # shared so --list/--resume see the checkpoints
        return env

    def _write_checkpoint(self, run_id, prompt="resume me", transcript=None,
                          rounds=2, interview_done=True, ts=1718000000.0):
        if transcript is None:
            transcript = [{"q": "What platform?", "a": "linux"}]
        rec = {"mode": "plan", "status": "unfinished", "prompt": prompt,
               "transcript": transcript,
               "asked_keys": [t["q"].lower() for t in transcript],
               "meters": [], "rounds": rounds, "pending_qs": None,
               "all_ready": False, "interview_done": interview_done, "ts": ts}
        with open(os.path.join(self.home, "runs", f"{run_id}.json"), "w") as f:
            json.dump(rec, f)
        self._append_history(run_id, prompt, unfinished=True, ts=ts)

    def _append_history(self, run_id, prompt, unfinished, ts=1718000000.0):
        entry = {"ts": ts, "file": f"{run_id}.json", "prompt": prompt, "mode": "plan"}
        if unfinished:
            entry["unfinished"] = True
        with open(os.path.join(self.home, "history.jsonl"), "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _list(self, *extra, env=None):
        return h.run_scry(["plan", "--list", *extra, "--no-anim"],
                          env=env or self._env())

    # --- empty state -------------------------------------------------------- #
    def test_empty_history_prints_note_to_stderr_exit_zero(self):
        cp = self._list()
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(cp.stdout.strip(), "")
        self.assertIn("no unfinished plan sessions", cp.stderr)

    def test_empty_history_json_is_empty_array(self):
        cp = self._list("--json")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(cp.stdout.strip(), "[]")

    # --- human row ---------------------------------------------------------- #
    def test_human_row_shows_id_rounds_answered_and_prompt(self):
        self._write_checkpoint("1700000000444", prompt="build a rate limiter",
                               transcript=[{"q": "a", "a": "1"}], rounds=2)
        cp = self._list()
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("1700000000444", cp.stdout)
        self.assertIn("R2", cp.stdout)
        self.assertIn("1Q", cp.stdout)
        self.assertIn("build a rate limiter", cp.stdout)

    # --- driver: JSON shape exposes `answered`/`updated` (was q_count/ts) ---- #
    def test_json_shape_exposes_answered_and_updated(self):
        self._write_checkpoint("1700000000333", prompt="x", rounds=3,
                               transcript=[{"q": "a", "a": "1"}, {"q": "b", "a": "2"}])
        cp = self._list("--json")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        s = json.loads(cp.stdout)[0]
        self.assertEqual(s["id"], "1700000000333")
        self.assertEqual(s["rounds"], 3)
        self.assertEqual(s["answered"], 2)       # renamed from q_count
        self.assertIn("updated", s)              # renamed from ts (now run-file mtime)
        self.assertEqual(s["prompt"], "x")       # full prompt, untruncated in JSON
        self.assertIs(s["interview_done"], True)

    # --- driver: top row matches what `--resume` (no id) actually resumes ---- #
    def test_top_row_is_most_recently_saved_not_highest_ts(self):
        # `--resume` (no id) loads unfinished[-1] (last history line). The list's
        # first row must agree — even when an OLDER session was resumed (re-appended
        # last) so its frozen creation-ts is HIGHER than the genuinely-newest one.
        self._write_checkpoint("1700000000111", prompt="older, higher ts", ts=2000.0)
        self._write_checkpoint("1700000000222", prompt="newest, lower ts", ts=1000.0)
        cp = self._list("--json")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        sessions = json.loads(cp.stdout)
        self.assertEqual(sessions[0]["id"], "1700000000222")  # last appended, not max ts

    # --- exclusion: finished + orphaned entries are skipped, no crash ------- #
    def test_excludes_finished_and_missing_run_files(self):
        self._write_checkpoint("1700000000555", prompt="keep me")        # resumable
        self._append_history("1700000000666", "done already", unfinished=False)
        self._append_history("1700000000777", "orphaned", unfinished=True)  # no run file
        cp = self._list("--json")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        ids = [s["id"] for s in json.loads(cp.stdout)]
        self.assertEqual(ids, ["1700000000555"])

    # --- path fix: the listed id round-trips straight into --resume --------- #
    def test_listed_id_round_trips_into_resume(self):
        self._write_checkpoint("1700000000888", prompt="design a cache",
                               interview_done=True)
        env = self._env(h.claude_plan())   # working stub for the resume synthesis
        cp = self._list("--json", env=env)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        rid = json.loads(cp.stdout)[0]["id"]
        self.assertEqual(rid, "1700000000888")          # bare id, no `.json`
        resumed = h.run_scry(["plan", f"--resume={rid}", "--no-anim", "--repo", "none",
                              "--panel", "claude:opus", "--judge", "claude:opus",
                              "--aggregator", "claude:opus"], env=env)
        self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
        self.assertIn("## Context", resumed.stdout)

    # --- non-billable: works with no provider binary on PATH ---------------- #
    def test_list_is_non_billable(self):
        self._write_checkpoint("1700000000999", prompt="x")
        cp = self._list(env=self._env())   # empty StubBins → no model could be spawned
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("1700000000999", cp.stdout)


# --------------------------------------------------------------------------- #
# `scry plan --step` — the headless, JSON-driven interview protocol that the
# /scry-plan skill uses to drive the full plan mode from inside Claude Code.
# Each call reads an optional answers payload on stdin and prints ONE JSON
# envelope; state is carried between calls via the existing resume checkpoints.
# --------------------------------------------------------------------------- #
class PlanStepSubprocessTest(unittest.TestCase):
    REQUEST = "build a rate limiter"

    def _env(self, stub):
        s = h.StubBins({"claude": stub})
        self.addCleanup(shutil.rmtree, s.dir, ignore_errors=True)
        env = s.env
        home = tempfile.mkdtemp(prefix="scry-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        env["SCRY_HOME"] = home
        return env

    _PANEL = ["--panel", "claude:opus", "--judge", "claude:opus",
              "--aggregator", "claude:opus"]

    def _start_args(self, *extra):
        return ["plan", self.REQUEST, "--no-anim", "--step", "--json",
                *self._PANEL, *extra]

    def _resume_args(self, rid, *extra):
        return ["plan", f"--resume={rid}", "--no-anim", "--step", "--json",
                *self._PANEL, *extra]

    def _run(self, args, stdin, env, cwd=None):
        cp = h.run_scry(args, input=stdin, env=env, cwd=cwd)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        return json.loads(cp.stdout), cp

    # ----- start emits the first round's questions -------------------------- #
    def test_start_emits_questions(self):
        env = self._env(h.claude_plan(rounds_before_ready=1))
        rec, _ = self._run(self._start_args(), "", env)
        self.assertEqual(rec["status"], "questions")
        self.assertTrue(rec["id"])
        self.assertGreaterEqual(len(rec["questions"]), 1)
        self.assertIn("q", rec["questions"][0])

    # ----- a confident panel skips straight to ready ----------------------- #
    def test_start_ready_when_panel_confident(self):
        env = self._env(h.claude_plan(rounds_before_ready=0))
        rec, _ = self._run(self._start_args(), "", env)
        self.assertEqual(rec["status"], "ready")
        self.assertTrue(rec["id"])

    # ----- answering a round advances the interview ------------------------ #
    def test_answer_advances_to_ready(self):
        env = self._env(h.claude_plan(rounds_before_ready=1))
        rec1, _ = self._run(self._start_args(), "", env)
        rid = rec1["id"]
        payload = json.dumps({"answers": [{"q": rec1["questions"][0]["q"],
                                           "a": "linux"}], "done": False})
        rec2, _ = self._run(self._resume_args(rid), payload, env)
        self.assertEqual(rec2["status"], "ready")

    # ----- done:true drafts the plan and writes the files ------------------ #
    def test_done_drafts_and_writes_files(self):
        d = tempfile.mkdtemp(prefix="scry-step-out-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        env = self._env(h.claude_plan(rounds_before_ready=1))
        rec, _ = self._run(self._start_args(), json.dumps({"done": True}),
                           env, cwd=d)
        self.assertEqual(rec["status"], "done")
        self.assertIn("## Context", rec["final"])
        self.assertTrue(rec["plan_path"])
        plans = [f for f in os.listdir(d)
                 if f.endswith(".md") and not f.endswith(".diagnostics.md")]
        diags = [f for f in os.listdir(d) if f.endswith(".diagnostics.md")]
        self.assertEqual(len(plans), 1, os.listdir(d))
        self.assertEqual(len(diags), 1, os.listdir(d))

    # ----- the whole loop: start -> answer -> ready -> draft --------------- #
    def test_full_loop_start_answer_draft(self):
        env = self._env(h.claude_plan(rounds_before_ready=1))
        rec1, _ = self._run(self._start_args(), "", env)
        self.assertEqual(rec1["status"], "questions")
        rid = rec1["id"]
        ans = json.dumps({"answers": [{"q": rec1["questions"][0]["q"], "a": "x"}]})
        rec2, _ = self._run(self._resume_args(rid), ans, env)
        self.assertEqual(rec2["status"], "ready")
        rec3, _ = self._run(self._resume_args(rid), json.dumps({"done": True}), env)
        self.assertEqual(rec3["status"], "done")
        self.assertIn("## Context", rec3["final"])

    # ----- an unknown resume id is a clean JSON error, exit 1 -------------- #
    def test_unknown_resume_emits_json_error(self):
        env = self._env(h.claude_plan())
        cp = h.run_scry(self._resume_args("9999999999999"), input="{}", env=env)
        self.assertEqual(cp.returncode, 1)
        rec = json.loads(cp.stdout)
        self.assertEqual(rec["status"], "error")

    # ----- the --step draft hands the drafter prompt to the panel ----------- #
    def test_step_draft_panel_receives_drafter_system(self):
        scry = h.load_scry()
        d = tempfile.mkdtemp(prefix="scry-step-sysdump-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        dump = os.path.join(d, "sys.txt")
        out = tempfile.mkdtemp(prefix="scry-step-out-")
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        env = self._env(h.claude_plan(rounds_before_ready=1))
        env["SCRY_SYSDUMP"] = dump
        rec, _ = self._run(self._start_args(), json.dumps({"done": True}),
                           env, cwd=out)
        self.assertEqual(rec["status"], "done")
        with open(dump) as f:
            seen = f.read()
        # The panel proposers must be reframed as plan AUTHORS, not executors.
        self.assertIn(scry.PLAN_DRAFTER_SYSTEM, seen)
        # Guard the invariant the assertion relies on: the drafter prompt is a
        # distinct prompt from the synth one (mirrors the interactive analog).
        self.assertNotEqual(scry.PLAN_DRAFTER_SYSTEM, scry.PLAN_SYNTH_SYSTEM)

    # ----- the draft streams pipeline progress to stderr -------------------- #
    def test_done_draft_emits_progress_to_stderr(self):
        d = tempfile.mkdtemp(prefix="scry-step-prog-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        env = self._env(h.claude_plan(rounds_before_ready=1))
        rec, cp = self._run(self._start_args(), json.dumps({"done": True}),
                            env, cwd=d)
        self.assertEqual(rec["status"], "done")
        # Pipeline progress lands on stderr…
        self.assertIn("panel", cp.stderr)
        self.assertIn("synthesis", cp.stderr)
        # …and never pollutes stdout, which stays exactly one JSON envelope.
        self.assertNotIn("▸", cp.stdout)              # the ▸ stage marker
        self.assertEqual(cp.stdout.strip().count("\n"), 0)  # single line

    # ----- each interview round shows panel/judge activity on stderr -------- #
    def test_question_round_emits_progress_to_stderr(self):
        env = self._env(h.claude_plan(rounds_before_ready=1))
        rec, cp = self._run(self._start_args(), "", env)
        self.assertEqual(rec["status"], "questions")
        self.assertIn("gathering clarifying questions", cp.stderr)
        self.assertIn("deduplicating", cp.stderr)


# --------------------------------------------------------------------------- #
# Pure helpers for the default-output + diagnostics feature:
#   _diag_path             — derive the diagnostics path from the plan path
#   render_plan_diagnostics — the human-readable .diagnostics.md body
# (The plan final draft's budget now comes from phases.final via _phase_settings;
#  see test_scry_run.PhaseSettingsTest for the resolution tests.)
# --------------------------------------------------------------------------- #
class DiagPathTest(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_replaces_md_extension(self):
        self.assertEqual(self.scry._diag_path("plan.md"), "plan.diagnostics.md")

    def test_keeps_directory(self):
        self.assertEqual(self.scry._diag_path("a/b/plan.md"),
                         "a/b/plan.diagnostics.md")

    def test_appends_when_no_md_extension(self):
        self.assertEqual(self.scry._diag_path("plan"), "plan.diagnostics.md")

    def test_default_id_filename(self):
        self.assertEqual(self.scry._diag_path("scry-plan-123.md"),
                         "scry-plan-123.diagnostics.md")


class RenderPlanDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.result = {
            "mode": "plan",
            "prompt": "build a rate limiter",
            "rounds": 2,
            "responses": [
                {"model": "claude-opus", "content": "", "ok": False,
                 "error": "model error: exit 1", "seconds": 4.2},
                {"model": "codex-gpt", "content": "draft", "ok": True,
                 "error": "", "seconds": 101.0},
            ],
            "analysis": {"consensus": ["c1"], "contradictions": ["x1"],
                         "partial_coverage": [], "unique_insights": [],
                         "blind_spots": []},
            "cost": {"total_usd": 0.12, "seconds": 110.0, "by_stage": [
                {"stage": "panel", "label": "claude-opus", "ok": False,
                 "output_tokens": 0},
                {"stage": "panel", "label": "codex-gpt", "ok": True,
                 "output_tokens": 1500, "cost_usd": 0.05},
                {"stage": "synth", "label": "synth", "ok": True,
                 "output_tokens": 2000, "cost_usd": 0.07},
            ]},
        }
        self.cfg = {"panel": [{"label": "claude-opus"}, {"label": "codex-gpt"}],
                    "judge": {"provider": "claude", "model": "opus"},
                    "aggregator": {"provider": "claude", "model": "opus"},
                    "phases": {"panel": {}, "judge": {},
                               "synthesis": {"web_tools": False},
                               "interview": {"web_tools": False},
                               "final": {"max_tool_calls": 24, "timeout": 2100}}}
        self.settings = {"max_tool_calls": 8, "web_tools": True, "timeout": 420,
                         "effort": None}
        self.plan_settings = {"max_rounds": 6, "repo_context": True}

    def _render(self):
        return self.scry.render_plan_diagnostics(
            self.result, self.cfg, self.settings, self.plan_settings,
            "1700000000001")

    def test_header_has_request_and_run_id(self):
        md = self._render()
        self.assertIn("diagnostics", md.lower())
        self.assertIn("build a rate limiter", md)
        self.assertIn("1700000000001", md)

    def test_failed_model_row_shows_error(self):
        md = self._render()
        self.assertIn("claude-opus", md)
        self.assertIn("model error: exit 1", md)
        self.assertIn("FAILED", md)

    def test_ok_model_listed(self):
        self.assertIn("codex-gpt", self._render())

    def test_settings_show_resolved_phase_budgets(self):
        md = self._render()
        self.assertIn("final draft", md)
        self.assertIn("24", md)            # phases.final max_tool_calls, layered on the draft
        self.assertIn("2100", md)          # phases.final timeout
        self.assertIn("interview phase", md)

    def test_consensus_map_rendered(self):
        md = self._render()
        self.assertIn("c1", md)
        self.assertIn("x1", md)


# --------------------------------------------------------------------------- #
# The enriched diagnostics — true end-to-end elapsed, per-round timeline, the
# bottleneck call, the FAILED-dedup reason, and the metering-floor note. This is
# the report rebuilt to actually diagnose the "why did it take an hour" run.
# --------------------------------------------------------------------------- #
class RenderPlanDiagnosticsRichTest(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.cfg = {
            "panel": [{"provider": "claude", "label": "claude-opus"},
                      {"provider": "agy", "label": "gemini-pro"},
                      {"provider": "kimi", "label": "kimi"}],
            "judge": {"provider": "claude"}, "aggregator": {"provider": "claude"},
            "providers": {
                "claude": {"effort": "max", "caps": {"effort": ["--effort", "{e}"]}},
                "agy": {"caps": {}},        # effort is model-encoded, no flag
                "kimi": {"caps": {}},       # no effort flag at all
            },
            "phases": {"panel": {}, "judge": {}, "synthesis": {"web_tools": False},
                       "interview": {"web_tools": False},
                       "final": {"timeout": 2100}},
        }
        self.settings = {"web_tools": True, "max_tool_calls": None, "timeout": 420,
                         "effort": None}
        self.plan_settings = {"max_rounds": 6}
        self.result = {
            "prompt": "visualize kernel HW usage", "rounds": 2,
            "responses": [{"model": "claude-opus", "ok": True, "error": "",
                           "seconds": 1585.2, "content": "draft"}],
            "analysis": {"consensus": ["c1"], "contradictions": [],
                         "partial_coverage": [], "unique_insights": [],
                         "blind_spots": []},
            "cost": {
                "total_usd": 12.95, "seconds": 1926.1, "wall_seconds": 5400.0,
                "calls": 8, "metered_calls": 4,
                "by_stage": [
                    {"stage": "interview", "label": "claude-opus", "ok": True,
                     "round": 1, "seconds": 40.0, "output_tokens": 8700, "cost_usd": 0.48},
                    {"stage": "interview", "label": "gemini-pro", "ok": True,
                     "round": 1, "seconds": 35.0, "output_tokens": 0},
                    {"stage": "dedup", "label": "dedup", "ok": True, "round": 1,
                     "seconds": 38.0, "output_tokens": 30000, "cost_usd": 0.90},
                    {"stage": "dedup", "label": "dedup", "ok": False, "round": 2,
                     "seconds": 420.0, "error": "timeout after 420s"},
                    {"stage": "panel", "label": "claude-opus", "ok": True,
                     "seconds": 1585.2, "output_tokens": 75000, "cost_usd": 6.64},
                    {"stage": "panel", "label": "kimi", "ok": True,
                     "seconds": 310.5, "output_tokens": 0},
                    {"stage": "judge", "label": "judge", "ok": True,
                     "seconds": 60.0, "output_tokens": 11000, "cost_usd": 1.10},
                    {"stage": "synth", "label": "synth", "ok": True,
                     "seconds": 40.0, "output_tokens": 16000, "cost_usd": 1.26},
                ],
            },
        }

    def _render(self):
        return self.scry.render_plan_diagnostics(
            self.result, self.cfg, self.settings, self.plan_settings, "1782535087888")

    def test_elapsed_is_end_to_end_not_just_the_draft_step(self):
        md = self._render()
        self.assertIn("5400.0s", md)                 # true end-to-end wall
        self.assertIn("end-to-end", md.lower())
        self.assertIn("1926.1s", md)                 # the draft step, shown separately

    def test_bottleneck_calls_out_the_slowest_call(self):
        md = self._render()
        self.assertIn("bottleneck", md.lower())
        self.assertIn("claude-opus", md)
        self.assertIn("1585.2s", md)

    def test_timeline_groups_by_round_and_shows_dedup_timeout(self):
        md = self._render()
        self.assertIn("timeline", md.lower())
        self.assertIn("round 1", md)
        self.assertIn("round 2", md)
        self.assertIn("FAILED", md)
        self.assertIn("timeout after 420s", md)      # the *reason* is visible

    def test_round_tag_in_models_table(self):
        self.assertIn("· r2", self._render())        # dedup row tagged round 2

    def test_metering_note_flags_unmetered_calls(self):
        md = self._render()
        self.assertIn("4 of 8", md)                  # 8 calls, 4 metered
        self.assertIn("floor", md)
        self.assertIn("Claude-metered only", md)     # caveat on the cost line

    def test_effort_is_honest_not_default(self):
        md = self._render()
        # claude is maxed; gemini/kimi have no effort flag -> 'n/a', never a fake 'default'
        self.assertIn("claude-opus=max", md)
        self.assertIn("gemini-pro=n/a", md)
        self.assertIn("kimi=n/a", md)

    def test_synthesis_phase_marked_tool_free(self):
        self.assertIn("tool-free", self._render().lower())


# --------------------------------------------------------------------------- #
# scry_run scrub_fuse_cwd — `scry plan` runs the panel in the repo cwd but the
# judge + synthesis in a throwaway dir, so the "final draft" makes no tool calls.
# --------------------------------------------------------------------------- #
class ScryRunScrubFuseCwdTest(unittest.IsolatedAsyncioTestCase):
    async def test_panel_keeps_cwd_but_judge_and_synth_are_scrubbed(self):
        scry = h.load_scry()
        seen = {}     # role -> set(cwd)

        async def fake(cfg, provider, model, system, user, cwd, depth, web,
                       settings, meta=None):
            role = ("judge" if (system and "impartial judge" in system)
                    else "panel" if system is None else "synth")
            seen.setdefault(role, set()).add(cwd)
            return json.dumps({"consensus": []}) if role == "judge" else "answer"

        orig = scry.call_cli
        scry.call_cli = fake
        self.addCleanup(setattr, scry, "call_cli", orig)
        cfg = {"panel": [{"provider": "claude", "label": "c"}],
               "judge": {"provider": "claude"}, "aggregator": {"provider": "claude"},
               "phases": {}, "providers": {}}
        await scry.scry_run(cfg, "q", "fusion", {"web_tools": False},
                            lambda *a, **k: None, cwd="/panel/cwd", scrub_fuse_cwd=True)
        self.assertEqual(seen["panel"], {"/panel/cwd"})        # panel kept the repo cwd
        for role in ("judge", "synth"):
            self.assertTrue(all(c != "/panel/cwd" and "scry-fuse-" in c
                                for c in seen[role]), seen.get(role))

    async def test_without_scrub_all_stages_share_cwd(self):
        scry = h.load_scry()
        seen = {}

        async def fake(cfg, provider, model, system, user, cwd, depth, web,
                       settings, meta=None):
            role = ("judge" if (system and "impartial judge" in system)
                    else "panel" if system is None else "synth")
            seen.setdefault(role, set()).add(cwd)
            return json.dumps({"consensus": []}) if role == "judge" else "answer"

        orig = scry.call_cli
        scry.call_cli = fake
        self.addCleanup(setattr, scry, "call_cli", orig)
        cfg = {"panel": [{"provider": "claude", "label": "c"}],
               "judge": {"provider": "claude"}, "aggregator": {"provider": "claude"},
               "phases": {}, "providers": {}}
        await scry.scry_run(cfg, "q", "fusion", {"web_tools": False},
                            lambda *a, **k: None, cwd="/shared/cwd")
        self.assertEqual(seen["panel"], {"/shared/cwd"})
        self.assertEqual(seen["judge"], {"/shared/cwd"})       # default: one shared cwd
        self.assertEqual(seen["synth"], {"/shared/cwd"})


# --------------------------------------------------------------------------- #
# The heavy web/tool wall-clock belongs to the PANELIST phases only: judge +
# synthesis run web-off by default even when the global web_tools is on.
# --------------------------------------------------------------------------- #
class JudgeSynthWebOffTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_panel_gets_web_by_default(self):
        scry = h.load_scry()
        seen = {}     # role -> web flag the stage was called with

        async def fake(cfg, provider, model, system, user, cwd, depth, web,
                       settings, meta=None):
            role = ("judge" if (system and "impartial judge" in system)
                    else "panel" if system is None else "synth")
            seen[role] = web
            return json.dumps({"consensus": []}) if role == "judge" else "answer"

        orig = scry.call_cli
        scry.call_cli = fake
        self.addCleanup(setattr, scry, "call_cli", orig)
        cfg = {"panel": [{"provider": "claude", "label": "c"}],
               "judge": {"provider": "claude"}, "aggregator": {"provider": "claude"},
               "phases": dict(scry.DEFAULT_PHASES), "providers": {}}
        # Global web ON — but only the panel (a panelist phase) should actually get it.
        await scry.scry_run(cfg, "q", "fusion", {"web_tools": True},
                            lambda *a, **k: None, cwd="/x")
        self.assertTrue(seen["panel"])     # panelists do the web-heavy work
        self.assertFalse(seen["judge"])    # judge reasons over panel output: no web
        self.assertFalse(seen["synth"])    # synthesis: no web

    def test_default_phases_pin_judge_web_off(self):
        scry = h.load_scry()
        jset = scry._phase_settings({"web_tools": True}, scry.DEFAULT_PHASES, "judge")
        self.assertIs(jset["web_tools"], False)


# --------------------------------------------------------------------------- #
# _plan_result_fields — cost.seconds is the DRAFT step, cost.wall_seconds is the
# true end-to-end (>= seconds), clamped non-negative against clock skew.
# --------------------------------------------------------------------------- #
class PlanResultFieldsTest(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def test_wall_spans_session_while_seconds_is_the_step(self):
        now = time.time()
        result = {}
        # t0 = draft start (2s ago); started_at = session start (120s ago).
        self.scry._plan_result_fields(result, "req", [], 2, [],
                                      now - 2, started_at=now - 120)
        cost = result["cost"]
        self.assertGreaterEqual(cost["wall_seconds"], cost["seconds"])
        self.assertGreaterEqual(cost["wall_seconds"], 100)   # ~120s end-to-end
        self.assertLess(cost["seconds"], 60)                 # ~2s draft step only

    def test_future_started_at_clamps_wall_to_zero(self):
        now = time.time()
        result = {}
        self.scry._plan_result_fields(result, "req", [], 1, [],
                                      now, started_at=now + 100)   # corrupt future anchor
        self.assertGreaterEqual(result["cost"]["wall_seconds"], 0.0)  # never negative


# --------------------------------------------------------------------------- #
# render_plan_diagnostics robustness — must never crash a finished (expensive!)
# run, even on corrupted checkpoints or odd cost shapes.
# --------------------------------------------------------------------------- #
class RenderPlanDiagnosticsRobustnessTest(unittest.TestCase):
    def setUp(self):
        self.scry = h.load_scry()

    def _render(self, result, cfg=None):
        cfg = cfg or {"panel": [{"label": "x"}], "judge": {}, "aggregator": {},
                      "phases": {}, "providers": {}}
        return self.scry.render_plan_diagnostics(
            result, cfg, {"web_tools": True, "timeout": 420}, {"max_rounds": 6}, "rid")

    def test_mixed_type_rounds_do_not_crash(self):
        # A hand-edited/corrupted checkpoint can carry a STRING round; sorting it
        # against int rounds must not raise (regression for the timeline sort).
        result = {"prompt": "p", "rounds": 2, "responses": [],
                  "cost": {"seconds": 10.0, "wall_seconds": 20.0, "by_stage": [
                      {"stage": "interview", "label": "a", "ok": True,
                       "round": 1, "seconds": 5.0},
                      {"stage": "dedup", "label": "dedup", "ok": True,
                       "round": "2", "seconds": 3.0}]}}
        md = self._render(result)
        self.assertIn("timeline", md.lower())

    def test_panel_none_does_not_crash(self):
        result = {"prompt": "p", "rounds": 1, "responses": [],
                  "cost": {"seconds": 1.0, "by_stage": []}}
        cfg = {"panel": None, "judge": {}, "aggregator": {},
               "phases": {}, "providers": {}}
        self.assertIn("diagnostics", self._render(result, cfg).lower())

    def test_negative_wall_seconds_is_not_rendered(self):
        result = {"prompt": "p", "rounds": 1, "responses": [],
                  "cost": {"seconds": 50.0, "wall_seconds": -5.0, "by_stage": []}}
        md = self._render(result)
        self.assertNotIn("-5.0s", md)        # the corrupt negative is dropped
        self.assertIn("50.0s", md)           # falls back to the step time

    def test_gap_note_suppressed_without_true_wall(self):
        # Only the draft-step `seconds` is known, but interview rounds exist — the
        # per-round model time can exceed it, so don't claim "the gap is answer time".
        result = {"prompt": "p", "rounds": 1, "responses": [],
                  "cost": {"seconds": 10.0, "by_stage": [
                      {"stage": "interview", "label": "a", "ok": True,
                       "round": 1, "seconds": 40.0},
                      {"stage": "panel", "label": "a", "ok": True, "seconds": 10.0}]}}
        self.assertNotIn("answering the clarifying questions", self._render(result))


if __name__ == "__main__":
    unittest.main(verbosity=2)
