"""Tests for scry.scry_run — the panel→judge→synthesis pipeline.

Drives the real scry_run coroutine but MONKEYPATCHES scry.call_cli (and, for the
streaming test, scry.stream_call) with async fakes so NO model CLI is ever spawned
and no money is spent. scry_run resolves call_cli / stream_call by module-global
name, so reassigning those globals is sufficient to intercept every call.

The shipped config.json panel is [claude (claude-opus), codex (codex-gpt),
agy (gemini-pro)]; judge and aggregator are both claude (which declares a `stream`
capability, so the streaming path is reachable).
"""
import json
import os
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


ANALYSIS = {
    "consensus": ["c1", "c2"],
    "contradictions": [],
    "partial_coverage": ["p1"],
    "unique_insights": ["u1"],
    "blind_spots": [],
}


class ScryRunTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.cfg = self.scry.load_config(str(h.CONFIG_JSON))
        self.log = lambda *a, **k: None
        # Snapshot the expected panel so assertions track config.json.
        self.panel = self.cfg.get("panel", [])

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _patch_call_cli(self, fake):
        scry = self.scry
        orig = scry.call_cli
        scry.call_cli = fake
        self.addCleanup(setattr, scry, "call_cli", orig)

    def _patch_stream_call(self, fake):
        scry = self.scry
        orig = scry.stream_call
        scry.stream_call = fake
        self.addCleanup(setattr, scry, "stream_call", orig)

    def _assert_result_shape(self, result):
        for key in ("status", "prompt", "mode", "responses", "analysis",
                    "final", "streamed", "cost"):
            self.assertIn(key, result, f"missing key {key!r} in result")

    # ------------------------------------------------------------------ #
    # fusion happy path
    # ------------------------------------------------------------------ #
    async def test_fusion_happy_path(self):
        scry = self.scry

        async def fake_call_cli(cfg, provider, model, system, user, cwd,
                                depth, web, settings, meta=None):
            if system is None:
                return f"PROP[{provider}]"
            if system == scry.JUDGE_SYSTEM:
                return json.dumps(ANALYSIS)
            if system == scry.MOA_AGGREGATOR_SYSTEM:
                return "FUSED"
            raise AssertionError(f"unexpected system arg: {system!r}")

        self._patch_call_cli(fake_call_cli)
        result = await scry.scry_run(self.cfg, "the prompt", "fusion",
                                     self.cfg["settings"], self.log)

        self._assert_result_shape(result)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["prompt"], "the prompt")
        self.assertEqual(result["mode"], "fusion")
        self.assertEqual(len(result["responses"]), len(self.panel))
        self.assertTrue(all(r["ok"] for r in result["responses"]))
        # Each panel proposer got system=None -> PROP[<provider>].
        contents = {r["content"] for r in result["responses"]}
        expected = {f"PROP[{m['provider']}]" for m in self.panel}
        self.assertEqual(contents, expected)
        self.assertEqual(result["analysis"], ANALYSIS)
        self.assertEqual(result["final"], "FUSED")
        self.assertFalse(result["streamed"])

    # ------------------------------------------------------------------ #
    # synthesize mode: judge is skipped entirely
    # ------------------------------------------------------------------ #
    async def test_synthesize_mode_skips_judge(self):
        scry = self.scry
        judge_calls = []

        async def fake_call_cli(cfg, provider, model, system, user, cwd,
                                depth, web, settings, meta=None):
            if system is None:
                return f"PROP[{provider}]"
            if system == scry.JUDGE_SYSTEM:
                judge_calls.append(provider)
                raise AssertionError("judge must NOT be called in synthesize mode")
            if system == scry.MOA_AGGREGATOR_SYSTEM:
                return "SYNTH-FINAL"
            raise AssertionError(f"unexpected system arg: {system!r}")

        self._patch_call_cli(fake_call_cli)
        result = await scry.scry_run(self.cfg, "p", "synthesize",
                                     self.cfg["settings"], self.log)

        self._assert_result_shape(result)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "synthesize")
        self.assertEqual(judge_calls, [])
        self.assertIsNone(result["analysis"])
        self.assertEqual(result["final"], "SYNTH-FINAL")
        self.assertEqual(len(result["responses"]), len(self.panel))
        self.assertTrue(all(r["ok"] for r in result["responses"]))

    # ------------------------------------------------------------------ #
    # partial failure: one proposer raises, synthesis still runs
    # ------------------------------------------------------------------ #
    async def test_partial_failure(self):
        scry = self.scry

        async def fake_call_cli(cfg, provider, model, system, user, cwd,
                                depth, web, settings, meta=None):
            if system is None:
                if provider == "agy":
                    raise scry.ProviderError("agy exploded")
                return f"PROP[{provider}]"
            if system == scry.JUDGE_SYSTEM:
                return json.dumps(ANALYSIS)
            if system == scry.MOA_AGGREGATOR_SYSTEM:
                return "FUSED-PARTIAL"
            raise AssertionError(f"unexpected system arg: {system!r}")

        self._patch_call_cli(fake_call_cli)
        result = await scry.scry_run(self.cfg, "p", "fusion",
                                     self.cfg["settings"], self.log)

        self._assert_result_shape(result)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["responses"]), len(self.panel))

        # The agy panel member is labelled "gemini-pro" in config.json; find it by
        # locating the panel entry whose provider == "agy".
        agy_label = next(m.get("label", m["provider"])
                         for m in self.panel if m["provider"] == "agy")
        by_label = {r["model"]: r for r in result["responses"]}
        failed = by_label[agy_label]
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["content"], None)
        self.assertEqual(failed["error"], "agy exploded")

        others = [r for r in result["responses"] if r["model"] != agy_label]
        self.assertTrue(all(r["ok"] for r in others))
        self.assertTrue(all(r["content"] is not None for r in others))

        # Synthesis still produced a final answer despite the partial failure.
        self.assertEqual(result["final"], "FUSED-PARTIAL")
        self.assertEqual(result["analysis"], ANALYSIS)

    # ------------------------------------------------------------------ #
    # all panels fail -> AllPanelsFailed, with failure_reason mapping
    # ------------------------------------------------------------------ #
    async def _run_all_fail(self, error_message):
        scry = self.scry

        async def fake_call_cli(cfg, provider, model, system, user, cwd,
                                depth, web, settings, meta=None):
            if system is None:
                raise scry.ProviderError(error_message)
            raise AssertionError("judge/synthesis must not run when all panels fail")

        self._patch_call_cli(fake_call_cli)
        with self.assertRaises(scry.AllPanelsFailed) as ctx:
            await scry.scry_run(self.cfg, "p", "fusion",
                                self.cfg["settings"], self.log)
        return ctx.exception

    async def test_all_fail_rate_limited(self):
        e = await self._run_all_fail("rate limit hit, try later")
        self.assertEqual(e.failure_reason, "rate_limited")
        self.assertEqual(len(e.responses), len(self.panel))
        self.assertTrue(all(not r["ok"] for r in e.responses))

    async def test_all_fail_insufficient_credits(self):
        e = await self._run_all_fail("quota exceeded for this month")
        self.assertEqual(e.failure_reason, "insufficient_credits")
        self.assertEqual(len(e.responses), len(self.panel))

    async def test_all_fail_generic(self):
        e = await self._run_all_fail("boom")
        self.assertEqual(e.failure_reason, "all_panels_failed")
        self.assertEqual(len(e.responses), len(self.panel))

    # ------------------------------------------------------------------ #
    # judge returns non-JSON -> analysis None, synthesis still runs ok
    # ------------------------------------------------------------------ #
    async def test_judge_non_json(self):
        scry = self.scry

        async def fake_call_cli(cfg, provider, model, system, user, cwd,
                                depth, web, settings, meta=None):
            if system is None:
                return f"PROP[{provider}]"
            if system == scry.JUDGE_SYSTEM:
                return "not json"
            if system == scry.MOA_AGGREGATOR_SYSTEM:
                return "FUSED-NOJUDGE"
            raise AssertionError(f"unexpected system arg: {system!r}")

        self._patch_call_cli(fake_call_cli)
        result = await scry.scry_run(self.cfg, "p", "fusion",
                                     self.cfg["settings"], self.log)

        self._assert_result_shape(result)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["analysis"])
        self.assertEqual(result["final"], "FUSED-NOJUDGE")

    # ------------------------------------------------------------------ #
    # judge raises -> swallowed, analysis None, synthesis still runs ok
    # ------------------------------------------------------------------ #
    async def test_judge_raises(self):
        scry = self.scry

        async def fake_call_cli(cfg, provider, model, system, user, cwd,
                                depth, web, settings, meta=None):
            if system is None:
                return f"PROP[{provider}]"
            if system == scry.JUDGE_SYSTEM:
                raise scry.ProviderError("judge crashed")
            if system == scry.MOA_AGGREGATOR_SYSTEM:
                return "FUSED-JUDGEFAIL"
            raise AssertionError(f"unexpected system arg: {system!r}")

        self._patch_call_cli(fake_call_cli)
        result = await scry.scry_run(self.cfg, "p", "fusion",
                                     self.cfg["settings"], self.log)

        self._assert_result_shape(result)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["analysis"])
        self.assertEqual(result["final"], "FUSED-JUDGEFAIL")

    # ------------------------------------------------------------------ #
    # streaming final: stream_call provides the answer; on_stream_start fires
    # ------------------------------------------------------------------ #
    async def test_streaming_final(self):
        scry = self.scry

        async def fake_call_cli(cfg, provider, model, system, user, cwd,
                                depth, web, settings, meta=None):
            if system is None:
                return f"PROP[{provider}]"
            if system == scry.JUDGE_SYSTEM:
                return json.dumps(ANALYSIS)
            if system == scry.MOA_AGGREGATOR_SYSTEM:
                raise AssertionError("buffered synthesis must not run when streaming")
            raise AssertionError(f"unexpected system arg: {system!r}")

        async def fake_stream_call(cfg, provider, model, system, user, cwd,
                                   depth, settings, sink):
            # Emit some deltas to the sink, then report the full streamed text.
            sink("STR")
            sink("EAMED")
            return {"text": "STREAMED", "streamed": True, "meta": {}}

        self._patch_call_cli(fake_call_cli)
        self._patch_stream_call(fake_stream_call)

        on_start = Mock()
        # scry_run streams the synthesis to sys.stdout token-by-token; capture it so
        # the streamed text doesn't leak into the test runner's output.
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            result = await scry.scry_run(self.cfg, "p", "fusion",
                                         self.cfg["settings"], self.log,
                                         stream_final=True, on_stream_start=on_start)

        self._assert_result_shape(result)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["final"], "STREAMED")
        self.assertTrue(result["streamed"])
        on_start.assert_called_once()

    # ------------------------------------------------------------------ #
    # aggregator_system: default is MOA; an override is forwarded to synthesis
    # ------------------------------------------------------------------ #
    async def _run_capturing_synth_system(self, **kwargs):
        scry = self.scry
        seen = {}

        async def fake_call_cli(cfg, provider, model, system, user, cwd,
                                depth, web, settings, meta=None):
            if system is None:
                return f"PROP[{provider}]"
            if system == scry.JUDGE_SYSTEM:
                return json.dumps(ANALYSIS)
            seen["synth"] = system          # the only remaining call is synthesis
            return "FUSED"

        self._patch_call_cli(fake_call_cli)
        await scry.scry_run(self.cfg, "p", "fusion", self.cfg["settings"],
                            self.log, **kwargs)
        return seen["synth"]

    async def test_aggregator_system_defaults_to_moa(self):
        synth_system = await self._run_capturing_synth_system()
        self.assertEqual(synth_system, self.scry.MOA_AGGREGATOR_SYSTEM)

    async def test_aggregator_system_override_forwarded(self):
        synth_system = await self._run_capturing_synth_system(
            aggregator_system="CUSTOM-AGG-PROMPT")
        self.assertEqual(synth_system, "CUSTOM-AGG-PROMPT")

    # ------------------------------------------------------------------ #
    # panel_system: default None keeps proposers general-purpose; an override
    # (e.g. `scry plan`'s PLAN_DRAFTER_SYSTEM) reaches EVERY proposer.
    # ------------------------------------------------------------------ #
    async def _run_capturing_panel_systems(self, **kwargs):
        scry = self.scry
        seen = []

        async def fake_call_cli(cfg, provider, model, system, user, cwd,
                                depth, web, settings, meta=None):
            if system == scry.JUDGE_SYSTEM:
                return json.dumps(ANALYSIS)
            if system == scry.MOA_AGGREGATOR_SYSTEM:
                return "FUSED"
            seen.append(system)          # everything else is a panel proposer
            return f"PROP[{provider}]"

        self._patch_call_cli(fake_call_cli)
        await scry.scry_run(self.cfg, "p", "fusion", self.cfg["settings"],
                            self.log, **kwargs)
        return seen

    async def test_panel_system_defaults_to_none(self):
        # Normal runs: the panel stays general-purpose (no proposer system prompt).
        systems = await self._run_capturing_panel_systems()
        self.assertEqual(len(systems), len(self.panel))
        self.assertTrue(all(s is None for s in systems),
                        f"expected every proposer system None, got {systems!r}")

    async def test_panel_system_override_reaches_every_proposer(self):
        # `scry plan` passes PLAN_DRAFTER_SYSTEM so each drafter writes a plan as text.
        systems = await self._run_capturing_panel_systems(
            panel_system="DRAFTER-PROMPT")
        self.assertEqual(len(systems), len(self.panel))
        self.assertTrue(all(s == "DRAFTER-PROMPT" for s in systems),
                        f"expected every proposer to get the override, got {systems!r}")


class ScryRunCwdTest(unittest.IsolatedAsyncioTestCase):
    """scry_run's optional cwd override (used by `scry plan` for repo-aware drafting).
    SAFETY-CRITICAL: a caller-provided cwd (the user's repo) must NEVER be deleted."""

    def setUp(self):
        self.scry = h.load_scry()
        self.cfg = self.scry.load_config(str(h.CONFIG_JSON))
        self.log = lambda *a, **k: None

    def _fake_capturing(self, seen):
        scry = self.scry

        async def fake(cfg, provider, model, system, user, cwd, depth, web,
                       settings, meta=None):
            seen["cwd"] = cwd
            if system is None:
                return f"PROP[{provider}]"
            if system == scry.JUDGE_SYSTEM:
                return json.dumps(ANALYSIS)
            return "FUSED"

        orig = scry.call_cli
        scry.call_cli = fake
        self.addCleanup(setattr, scry, "call_cli", orig)

    async def test_provided_cwd_is_not_deleted(self):
        import os
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix="scry-repo-test-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        seen = {}
        self._fake_capturing(seen)
        await self.scry.scry_run(self.cfg, "p", "fusion", self.cfg["settings"],
                                 self.log, cwd=d)
        self.assertTrue(os.path.isdir(d),
                        "scry_run must NOT delete a caller-provided cwd")
        self.assertEqual(seen["cwd"], d)  # provider calls ran in the provided cwd

    async def test_default_cwd_is_temp_and_cleaned(self):
        import os
        seen = {}
        self._fake_capturing(seen)
        await self.scry.scry_run(self.cfg, "p", "fusion", self.cfg["settings"], self.log)
        self.assertIn("scry-run-", seen["cwd"])
        self.assertFalse(os.path.isdir(seen["cwd"]),
                         "an internally-created temp cwd should be cleaned up")


class EffectiveTimeoutTest(unittest.TestCase):
    """call_cli's per-call timeout, read straight from a call's resolved (phase) settings;
    falls back to DEFAULT_TIMEOUT when unset/zero. No scaling, no per-provider override —
    the value is whatever the phase resolved to (e.g. phases.final raises it for the draft).
    Returns None to disable the cap entirely (SCRY_NO_TIMEOUT env, or a negative timeout)."""

    def setUp(self):
        self.scry = h.load_scry()

    def test_reads_timeout_from_settings(self):
        self.assertEqual(self.scry._effective_timeout({"timeout": 360}), 360.0)

    def test_missing_falls_back_to_default(self):
        self.assertEqual(self.scry._effective_timeout({}),
                         float(self.scry.DEFAULT_TIMEOUT))

    def test_zero_or_none_falls_back_to_default(self):
        self.assertEqual(self.scry._effective_timeout({"timeout": 0}),
                         float(self.scry.DEFAULT_TIMEOUT))
        self.assertEqual(self.scry._effective_timeout({"timeout": None}),
                         float(self.scry.DEFAULT_TIMEOUT))

    def test_negative_timeout_disables_cap(self):
        # A negative timeout (`--timeout -1`, or `"timeout": -1` in config) is the
        # explicit opt-out: no kill, the call runs to completion.
        self.assertIsNone(self.scry._effective_timeout({"timeout": -1}))

    def test_env_var_disables_cap(self):
        # SCRY_NO_TIMEOUT overrides everything, even an explicit positive timeout.
        prev = os.environ.get("SCRY_NO_TIMEOUT")
        os.environ["SCRY_NO_TIMEOUT"] = "1"
        try:
            self.assertIsNone(self.scry._effective_timeout({"timeout": 2100}))
        finally:
            if prev is None:
                os.environ.pop("SCRY_NO_TIMEOUT", None)
            else:
                os.environ["SCRY_NO_TIMEOUT"] = prev
        # ...and once unset, the cap is back.
        self.assertEqual(self.scry._effective_timeout({"timeout": 2100}), 2100.0)


class PhaseSettingsTest(unittest.TestCase):
    """_phase_settings merges base settings -> phases[phase] -> run overlay (phases.final,
    plan draft) -> CLI overrides, later wins. The resolution backbone for per-phase config."""

    def setUp(self):
        self.scry = h.load_scry()

    def test_inherits_base_when_phase_empty(self):
        s = {"web_tools": True, "max_tool_calls": 8, "timeout": 420}
        self.assertEqual(self.scry._phase_settings(s, {"panel": {}}, "panel"), s)

    def test_phase_overrides_only_its_keys(self):
        s = {"web_tools": True, "max_tool_calls": 8}
        eff = self.scry._phase_settings(s, {"judge": {"web_tools": False}}, "judge")
        self.assertFalse(eff["web_tools"])
        self.assertEqual(eff["max_tool_calls"], 8)   # untouched key still inherited

    def test_unknown_phase_returns_base(self):
        s = {"web_tools": True}
        self.assertEqual(self.scry._phase_settings(s, {}, "nope"), s)

    def test_run_overlay_beats_phase(self):
        s = {"max_tool_calls": 8}
        eff = self.scry._phase_settings(
            s, {"panel": {"max_tool_calls": 10}}, "panel",
            run_overlay={"max_tool_calls": 24})
        self.assertEqual(eff["max_tool_calls"], 24)

    def test_cli_overrides_beat_everything(self):
        s = {"max_tool_calls": 8}
        eff = self.scry._phase_settings(
            s, {"panel": {"max_tool_calls": 10}}, "panel",
            run_overlay={"max_tool_calls": 24}, cli_overrides={"max_tool_calls": 5})
        self.assertEqual(eff["max_tool_calls"], 5)

    def test_does_not_mutate_base(self):
        s = {"max_tool_calls": 8}
        self.scry._phase_settings(s, {"panel": {"max_tool_calls": 24}}, "panel")
        self.assertEqual(s, {"max_tool_calls": 8})


if __name__ == "__main__":
    unittest.main()
