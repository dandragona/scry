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


if __name__ == "__main__":
    unittest.main()
