"""Async logic tests for the scry-eval harness — fully monkeypatched, no real
model CLIs ever run (no subprocesses, no money spent).

We exercise:
  * call_with_retry        — success / transient-retry / non-transient / exhaustion
  * judge_once             — verdict parse / call failure -> (None, err)
  * judge_matchup          — order-swap A-bias cancellation, n_votes, all-None case
  * grade_oneshot          — valid one-shot JSON / garbage -> ungraded
  * grade_against_rubric   — per-criterion MET/UNMET reports
  * grade_output           — mode dispatch ("one-shot" -> n=1, per-criterion -> n=len)
  * eval_objective         — ok / SUT-error result shaping
  * eval_subjective        — ok path with judges, winrate float|None

Strategy: for functions that take `mod` (the scry module) we pass the real scry
module but monkeypatch scry.call_cli with an async fake. For eval_objective /
eval_subjective we monkeypatch ev.run_scry (the module global) with a fake that
returns a canned scry --json dict. Everything restored via addCleanup.
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


ev = h.load_scry_eval()
scry = h.load_scry()


def _cfg():
    return scry.load_config(str(h.CONFIG_JSON))


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cfg = _cfg()
        self.settings = dict(self.cfg["settings"])
        self.cwd = tempfile.mkdtemp(prefix="scry-eval-test-")
        self.sem = asyncio.Semaphore(4)
        # The eval drivers print progress to sys.stderr; mute it for the duration of
        # each test so the suite output stays clean (restored after the test).
        import io
        real_stderr = sys.stderr
        sys.stderr = io.StringIO()
        self.addCleanup(setattr, sys, "stderr", real_stderr)

    # ---- helpers to install/restore async fakes ---------------------------- #
    def patch_call_cli(self, fake):
        orig = scry.call_cli
        scry.call_cli = fake
        self.addCleanup(setattr, scry, "call_cli", orig)

    def patch_run_scry(self, fake):
        orig = ev.run_scry
        ev.run_scry = fake
        self.addCleanup(setattr, ev, "run_scry", orig)

    def patch_sleep(self):
        """Make asyncio.sleep a no-op so retry backoff doesn't actually wait.
        scry-eval calls `asyncio.sleep` via its module-global asyncio binding."""
        orig = ev.asyncio.sleep

        async def _noop(*a, **k):
            return None

        ev.asyncio.sleep = _noop
        self.addCleanup(setattr, ev.asyncio, "sleep", orig)


# --------------------------------------------------------------------------- #
# call_with_retry
# --------------------------------------------------------------------------- #
class TestCallWithRetry(_Base):
    async def test_success_first_try(self):
        calls = {"n": 0}

        async def make():
            calls["n"] += 1
            return "value"

        out = await ev.call_with_retry(make, attempts=4, base_delay=0.01)
        self.assertEqual(out, "value")
        self.assertEqual(calls["n"], 1)

    async def test_transient_then_success(self):
        self.patch_sleep()
        calls = {"n": 0}

        async def make():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("rate limit exceeded, try again")
            return "recovered"

        out = await ev.call_with_retry(make, attempts=4, base_delay=0.01)
        self.assertEqual(out, "recovered")
        self.assertEqual(calls["n"], 3)  # failed twice, succeeded on 3rd

    async def test_non_transient_reraised_immediately(self):
        self.patch_sleep()
        calls = {"n": 0}

        async def make():
            calls["n"] += 1
            raise ValueError("boom, total nonsense")

        with self.assertRaises(ValueError):
            await ev.call_with_retry(make, attempts=4, base_delay=0.01)
        self.assertEqual(calls["n"], 1)  # no retry on a non-transient error

    async def test_transient_exhausts_attempts(self):
        self.patch_sleep()
        calls = {"n": 0}

        async def make():
            calls["n"] += 1
            raise RuntimeError("429 rate limit, throttled")

        with self.assertRaises(RuntimeError):
            await ev.call_with_retry(make, attempts=3, base_delay=0.01)
        self.assertEqual(calls["n"], 3)  # tried exactly `attempts` times


# --------------------------------------------------------------------------- #
# judge_once
# --------------------------------------------------------------------------- #
class TestJudgeOnce(_Base):
    async def test_verdict_a(self):
        async def fake_call_cli(cfg, prov, model, system, user, cwd, depth, web, settings):
            return "VERDICT: A"

        self.patch_call_cli(fake_call_cli)
        judge = {"provider": "agy", "model": ""}
        v, err = await ev.judge_once(scry, self.cfg, self.settings, judge,
                                     self.cwd, "prompt", "ansA", "ansB")
        self.assertEqual(v, "A")
        self.assertIsNone(err)

    async def test_call_failure_returns_error(self):
        async def fake_call_cli(cfg, prov, model, system, user, cwd, depth, web, settings):
            raise RuntimeError("the judge blew up")

        self.patch_call_cli(fake_call_cli)
        judge = {"provider": "agy", "model": ""}
        v, err = await ev.judge_once(scry, self.cfg, self.settings, judge,
                                     self.cwd, "prompt", "ansA", "ansB")
        self.assertIsNone(v)
        self.assertIn("blew up", err)


# --------------------------------------------------------------------------- #
# judge_matchup
# --------------------------------------------------------------------------- #
class TestJudgeMatchup(_Base):
    async def test_a_bias_cancels_across_orderings(self):
        # A judge that ALWAYS says VERDICT: A. Over both orderings (fused as A,
        # then fused as B) the A-bias cancels: one ordering scores fused 1.0, the
        # other 0.0 -> mean 0.5 per judge, 0.5 overall.
        async def fake_call_cli(cfg, prov, model, system, user, cwd, depth, web, settings):
            return "VERDICT: A"

        self.patch_call_cli(fake_call_cli)
        judges = [{"provider": "agy", "model": ""}, {"provider": "kimi", "model": ""}]
        score, n = await ev.judge_matchup(scry, self.cfg, self.settings, self.cwd,
                                          self.sem, "prompt", "FUSED", "SOLO", judges)
        self.assertAlmostEqual(score, 0.5)
        self.assertEqual(n, 2 * len(judges))  # 2 orderings * 2 judges = 4 valid votes

    async def test_all_invalid_verdicts(self):
        # Judge output never parses to a verdict -> no valid votes -> (None, 0).
        async def fake_call_cli(cfg, prov, model, system, user, cwd, depth, web, settings):
            return "I cannot decide, sorry"

        self.patch_call_cli(fake_call_cli)
        judges = [{"provider": "agy", "model": ""}, {"provider": "kimi", "model": ""}]
        score, n = await ev.judge_matchup(scry, self.cfg, self.settings, self.cwd,
                                          self.sem, "prompt", "FUSED", "SOLO", judges)
        self.assertIsNone(score)
        self.assertEqual(n, 0)

    async def test_fused_always_wins(self):
        # Judge always picks whichever label is fused -> mean 1.0.
        async def fake_call_cli(cfg, prov, model, system, user, cwd, depth, web, settings):
            # The juser text embeds "Response A:\n<a>\n\nResponse B:\n<b>".
            # fused content is "FUSED"; pick the label whose answer is FUSED.
            a_is_fused = "Response A:\nFUSED" in user
            return "VERDICT: A" if a_is_fused else "VERDICT: B"

        self.patch_call_cli(fake_call_cli)
        judges = [{"provider": "agy", "model": ""}]
        score, n = await ev.judge_matchup(scry, self.cfg, self.settings, self.cwd,
                                          self.sem, "prompt", "FUSED", "SOLO", judges)
        self.assertAlmostEqual(score, 1.0)
        self.assertEqual(n, 2)


# --------------------------------------------------------------------------- #
# grade_oneshot / grade_against_rubric / grade_output
# --------------------------------------------------------------------------- #
class TestGrading(_Base):
    CRITERIA = [
        {"weight": 2.0, "requirement": "mentions X"},
        {"weight": 1.0, "requirement": "mentions Y"},
        {"weight": -1.0, "requirement": "makes error Z"},
    ]

    async def test_grade_oneshot_valid_json(self):
        # A grader returning a valid one-shot payload covering all criteria.
        payload = json.dumps({"criteria_evaluations": [
            {"criterion_idx": 0, "criterion_status": "MET", "explanation": "."},
            {"criterion_idx": 1, "criterion_status": "UNMET", "explanation": "."},
            {"criterion_idx": 2, "criterion_status": "MET", "explanation": "."},
        ]})

        async def fake_call_cli(cfg, prov, model, system, user, cwd, depth, web, settings):
            return payload

        self.patch_call_cli(fake_call_cli)
        grader = {"provider": "agy", "model": ""}
        reports = await ev.grade_oneshot(scry, self.cfg, self.settings, grader,
                                         self.cwd, "q", "answer", self.CRITERIA)
        self.assertEqual(reports, [("MET", 2.0), ("UNMET", 1.0), ("MET", -1.0)])

    async def test_grade_oneshot_garbage_is_ungraded(self):
        async def fake_call_cli(cfg, prov, model, system, user, cwd, depth, web, settings):
            return "this is not JSON at all"

        self.patch_call_cli(fake_call_cli)
        grader = {"provider": "agy", "model": ""}
        reports = await ev.grade_oneshot(scry, self.cfg, self.settings, grader,
                                         self.cwd, "q", "answer", self.CRITERIA)
        self.assertEqual(reports, [(None, 2.0), (None, 1.0), (None, -1.0)])

    async def test_grade_against_rubric_per_criterion(self):
        # Return MET for positive-weight criteria, UNMET for the negative one,
        # keyed off the criterion text echoed into the user prompt.
        async def fake_call_cli(cfg, prov, model, system, user, cwd, depth, web, settings):
            if "error Z" in user:
                return json.dumps({"criterion_status": "UNMET"})
            return json.dumps({"criterion_status": "MET"})

        self.patch_call_cli(fake_call_cli)
        grader = {"provider": "agy", "model": ""}
        reports = await ev.grade_against_rubric(scry, self.cfg, self.settings, grader,
                                                self.cwd, self.sem, "q", "answer",
                                                self.CRITERIA)
        # One report per criterion, in order.
        self.assertEqual(len(reports), len(self.CRITERIA))
        self.assertEqual(reports[0], ("MET", 2.0))
        self.assertEqual(reports[1], ("MET", 1.0))
        self.assertEqual(reports[2], ("UNMET", -1.0))

    async def test_grade_output_oneshot_dispatch(self):
        payload = json.dumps({"criteria_evaluations": [
            {"criterion_idx": 0, "criterion_status": "MET", "explanation": "."},
            {"criterion_idx": 1, "criterion_status": "MET", "explanation": "."},
            {"criterion_idx": 2, "criterion_status": "UNMET", "explanation": "."},
        ]})

        async def fake_call_cli(cfg, prov, model, system, user, cwd, depth, web, settings):
            return payload

        self.patch_call_cli(fake_call_cli)
        grader = {"provider": "agy", "model": ""}
        reports, n_calls = await ev.grade_output(
            "one-shot", scry, self.cfg, self.settings, grader, self.cwd, self.sem,
            "q", "answer", self.CRITERIA)
        self.assertEqual(n_calls, 1)
        self.assertEqual(len(reports), len(self.CRITERIA))

    async def test_grade_output_per_criterion_dispatch(self):
        async def fake_call_cli(cfg, prov, model, system, user, cwd, depth, web, settings):
            return json.dumps({"criterion_status": "MET"})

        self.patch_call_cli(fake_call_cli)
        grader = {"provider": "agy", "model": ""}
        reports, n_calls = await ev.grade_output(
            "per-criterion", scry, self.cfg, self.settings, grader, self.cwd,
            self.sem, "q", "answer", self.CRITERIA)
        self.assertEqual(n_calls, len(self.CRITERIA))
        self.assertEqual(len(reports), len(self.CRITERIA))


# --------------------------------------------------------------------------- #
# eval_objective
# --------------------------------------------------------------------------- #
class TestEvalObjective(_Base):
    def _canned_ok(self, final, responses):
        return {"status": "ok", "final": final, "responses": responses, "_seconds": 1.0}

    async def test_objective_ok_correct(self):
        canned = self._canned_ok(
            "ANSWER: 9.9",
            [{"model": "m1", "ok": True, "content": "ANSWER: 9.9"}],
        )

        def fake_run_scry(scry_path, prompt, extra_args, timeout=900.0):
            return canned

        self.patch_run_scry(fake_run_scry)
        items = [{"id": "t1", "prompt": "which is bigger 9.9 or 9.11?",
                  "expected": ["9.9"]}]
        results = ev.eval_objective("/fake/scry", items, [])
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertTrue(r["sut_ok"])
        self.assertTrue(r["fused"]["correct"])
        self.assertTrue(r["any_solo_correct"])
        self.assertEqual(r["fused"]["matched"], "9.9")

    async def test_objective_sut_error(self):
        def fake_run_scry(scry_path, prompt, extra_args, timeout=900.0):
            return {"status": "error", "failure_reason": "all proposers failed"}

        self.patch_run_scry(fake_run_scry)
        items = [{"id": "t2", "prompt": "anything", "expected": ["42"]}]
        results = ev.eval_objective("/fake/scry", items, [])
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertFalse(r["sut_ok"])
        self.assertEqual(r["error"], "all proposers failed")
        self.assertNotIn("fused", r)


# --------------------------------------------------------------------------- #
# eval_subjective
# --------------------------------------------------------------------------- #
class TestEvalSubjective(_Base):
    async def test_subjective_ok_with_judges(self):
        # Panel from config.json: labels claude-opus/codex-gpt/gemini-pro.
        # Use a solo whose model label maps to a NON-anthropic family so that
        # judges_for(anthropic_fused, solo_family) yields judges. solo "gemini-pro"
        # -> google family; fused family anthropic -> judges = {openai, moonshot}.
        canned = {
            "status": "ok",
            "final": "FUSED FINAL ANSWER",
            "_seconds": 2.0,
            "responses": [
                {"model": "gemini-pro", "ok": True, "content": "a solo gemini answer"},
            ],
        }

        def fake_run_scry(scry_path, prompt, extra_args, timeout=900.0):
            return canned

        self.patch_run_scry(fake_run_scry)

        # Judge always says VERDICT: A -> per-matchup score 0.5 (bias cancels).
        async def fake_call_cli(cfg, prov, model, system, user, cwd, depth, web, settings):
            return "VERDICT: A"

        self.patch_call_cli(fake_call_cli)

        items = [{"id": "s1", "prompt": "write a haiku"}]
        results, judge_calls = await ev.eval_subjective(
            scry, self.cfg, "/fake/scry", items, [], concurrency=4)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertTrue(r["sut_ok"])
        self.assertIn("matchups", r)
        self.assertGreater(len(r["matchups"]), 0)
        wr = r["fused_winrate_vs_field"]
        self.assertTrue(wr is None or isinstance(wr, float))
        self.assertIsInstance(wr, float)
        self.assertAlmostEqual(wr, 0.5)
        self.assertGreater(judge_calls, 0)

    async def test_subjective_sut_error(self):
        def fake_run_scry(scry_path, prompt, extra_args, timeout=900.0):
            return {"status": "error", "failure_reason": "scry exploded"}

        self.patch_run_scry(fake_run_scry)

        async def fake_call_cli(*a, **k):
            raise AssertionError("call_cli must not run when the SUT failed")

        self.patch_call_cli(fake_call_cli)

        items = [{"id": "s2", "prompt": "anything"}]
        results, judge_calls = await ev.eval_subjective(
            scry, self.cfg, "/fake/scry", items, [], concurrency=4)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["sut_ok"])
        self.assertEqual(results[0]["error"], "scry exploded")
        self.assertEqual(judge_calls, 0)

    async def test_subjective_no_judges_yields_no_matchups(self):
        # A solo in the SAME family as fused (anthropic). judges_for excludes both
        # fused_family and solo_family, and the remaining map still has judges, so
        # this exercises the path where the solo's own family is excluded. Use a
        # solo label mapping to claude (anthropic) == fused family -> judges_for
        # excludes only anthropic, leaving openai/google/moonshot judges -> matchup
        # still produced. To get ZERO matchups we need an unknown label (family '?')
        # which is NOT excluded, so judges include all four families; still nonzero.
        # The genuinely-empty case is when label maps to a family s.t. the union of
        # {fused, solo} covers every family; with 4 families that can't happen for a
        # single solo, so matchups is always nonzero here. Assert that.
        canned = {
            "status": "ok",
            "final": "FUSED",
            "_seconds": 1.0,
            "responses": [
                {"model": "claude-opus", "ok": True, "content": "anthropic solo"},
            ],
        }

        def fake_run_scry(scry_path, prompt, extra_args, timeout=900.0):
            return canned

        self.patch_run_scry(fake_run_scry)

        async def fake_call_cli(cfg, prov, model, system, user, cwd, depth, web, settings):
            return "VERDICT: TIE"

        self.patch_call_cli(fake_call_cli)

        items = [{"id": "s3", "prompt": "x"}]
        results, judge_calls = await ev.eval_subjective(
            scry, self.cfg, "/fake/scry", items, [], concurrency=4)
        r = results[0]
        self.assertTrue(r["sut_ok"])
        # solo family == fused family (anthropic): judges_for leaves 3 families.
        self.assertGreater(len(r["matchups"]), 0)
        # All TIE -> every matchup scores 0.5.
        self.assertAlmostEqual(r["fused_winrate_vs_field"], 0.5)


if __name__ == "__main__":
    unittest.main()
