"""scry_web.engine — the in-process bridge to scry's Python internals.

Proves the web backend reuses scry IN-PROCESS (no `scry` CLI subprocess): runs are
driven either by monkeypatching the engine's own scry.call_cli, or by real stub
provider binaries on PATH via StubBins (so the only subprocesses are the stubbed
model CLIs, exactly like the CLI pipeline — never a nested `scry`)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import _harness as h  # noqa: E402
from scry_web import engine  # noqa: E402


def _claude_only_cfg():
    cfg = engine.load_config(str(h.CONFIG_JSON))
    cfg["panel"] = [{"provider": "claude", "model": "opus", "label": "claude-opus"}]
    cfg["judge"] = {"provider": "claude", "model": "opus"}
    cfg["aggregator"] = {"provider": "claude", "model": "opus"}
    return cfg


class EnginePureTest(unittest.TestCase):
    def test_apply_options_maps_to_settings_and_cli(self):
        cfg = _claude_only_cfg()
        settings, cli = engine._apply_options(
            cfg, {"effort": "high", "max_tool_calls": 4, "web_tools": False})
        self.assertEqual(settings["effort"], "high")
        self.assertEqual(cli["effort"], "high")
        self.assertEqual(cli["max_tool_calls"], 4)
        self.assertFalse(cli["web_tools"])

    def test_apply_options_force_web(self):
        cfg = _claude_only_cfg()
        _settings, cli = engine._apply_options(cfg, {"web_tools": False}, force_web=True)
        self.assertTrue(cli["web_tools"])

    def test_build_contextual_prompt_includes_history_and_attachments(self):
        d = tempfile.mkdtemp()
        f = Path(d) / "n.txt"
        f.write_text("FILE BODY")
        prompt = engine.build_contextual_prompt(
            [{"role": "user", "content": "earlier q"},
             {"role": "assistant", "content": "earlier a"}],
            "the new question",
            context="some context",
            attachments=[{"filename": "n.txt", "path": str(f), "is_text": True,
                          "size": 9}])
        self.assertIn("earlier q", prompt)
        self.assertIn("earlier a", prompt)
        self.assertIn("some context", prompt)
        self.assertIn("FILE BODY", prompt)
        self.assertIn("the new question", prompt)


class EngineInProcessRunTest(unittest.TestCase):
    """Drive scry_run THROUGH the engine using real stub binaries (no real models,
    no nested scry process)."""

    def test_run_scry_sync_over_stub_binaries(self):
        cfg = _claude_only_cfg()
        with h.StubBins({"claude": h.claude_smart("PROP", "FUSED ANSWER")}):
            result = engine.run_scry_sync(cfg, "what is 2+2", {"mode": "fusion"})
        self.assertEqual(result["final"], "FUSED ANSWER")
        self.assertTrue(any(r["ok"] for r in result["responses"]))

    def test_run_research_forces_web_on_every_stage(self):
        cfg = _claude_only_cfg()
        scry = engine.load_scry()
        seen = []

        async def fake_call_cli(cfg, provider, model, system, user, cwd, depth,
                                web, settings, meta=None):
            seen.append(web)
            if system is None:
                return "PROP"
            if system == scry.JUDGE_SYSTEM:
                return json.dumps({"consensus": [], "contradictions": [],
                                   "partial_coverage": [], "unique_insights": [],
                                   "blind_spots": []})
            return "FUSED"

        orig = scry.call_cli
        scry.call_cli = fake_call_cli
        try:
            result = engine.run_research_sync(cfg, "research X", {"web_tools": False})
        finally:
            scry.call_cli = orig
        self.assertEqual(result["mode"], "research")
        self.assertEqual(result["prompt"], "research X")
        self.assertTrue(seen and all(seen),
                        "research must force web tools on for every stage")


class EnginePlanLoopTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="scry-home-")
        self._old = os.environ.get("SCRY_HOME")
        os.environ["SCRY_HOME"] = self.home

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SCRY_HOME", None)
        else:
            os.environ["SCRY_HOME"] = self._old

    def test_plan_questions_ready_done_loop_in_process(self):
        cfg = _claude_only_cfg()
        out = os.path.join(tempfile.mkdtemp(), "plan.md")
        with h.StubBins({"claude": h.claude_plan(rounds_before_ready=1)}):
            # round 1: questions
            e1 = engine.plan_step(cfg, "build a thing", {}, no_out=True)
            self.assertEqual(e1["status"], "questions")
            rid = e1["id"]
            self.assertTrue(e1["questions"])
            # round 2: answer -> ready
            e2 = engine.plan_step(cfg, "build a thing", {}, resume=rid,
                                  payload={"answers": [{"q": e1["questions"][0]["q"],
                                                        "a": "macos"}]}, no_out=True)
            self.assertEqual(e2["status"], "ready")
            # finalize -> done, writes the plan file
            e3 = engine.plan_step(cfg, "build a thing", {}, resume=rid,
                                  payload={"done": True}, out=out, no_out=False)
        self.assertEqual(e3["status"], "done")
        self.assertTrue(e3["final"])
        self.assertTrue(Path(out).exists(), "plan file should be written to out")


if __name__ == "__main__":
    unittest.main()
