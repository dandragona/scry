"""Deep Research mode — end-to-end CLI tests driving the real ./scry as a subprocess
with stub provider binaries. An explicit --config isolates these from the developer's
global ~/.config/scry/config.json (which run_scry does not sandbox), so the default
mode + panel are deterministic. Nothing here calls a real model.

Run hermetically:  python3 -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

import _harness as h


def _write_cfg(panel, research, judge="claude", aggregator="claude"):
    """Write a throwaway research config and return its path. Providers/phases/settings
    are backfilled from the shipped defaults by load_config, so only the bits that vary
    per test need to be named."""
    cfg = {
        "mode": "research",
        "panel": panel,
        "judge": {"provider": judge},
        "aggregator": {"provider": aggregator},
        "research": research,
    }
    d = tempfile.mkdtemp(prefix="scry-research-cfg-")
    path = os.path.join(d, "scry.config.json")
    with open(path, "w") as f:
        json.dump(cfg, f)
    return path


CLAUDE_ONLY = [{"provider": "claude", "label": "claude-opus"}]
FULL_PANEL = [
    {"provider": "claude", "label": "claude-opus"},
    {"provider": "codex", "label": "codex-gpt"},
    {"provider": "agy", "label": "gemini-pro"},
    {"provider": "deepseek", "label": "deepseek"},
    {"provider": "kimi", "label": "kimi"},
]


def _full_stubs(**rk):
    return {
        "claude": h.claude_research(**rk),
        "codex": h.codex_outfile("CODEX FINDINGS"),
        "agy": h.agy_text("GEMINI FINDINGS"),
        "kimi-cli": h.kimi_text("KIMI FINDINGS"),
        "scry-deepseek": h.deepseek_text("DEEPSEEK FINDINGS"),
    }


class TestResearchCliJson(unittest.TestCase):
    def test_json_research_run_shape(self):
        cfg = _write_cfg(CLAUDE_ONLY, {"max_rounds": 1, "hard_cap": 1, "clarify": False})
        with h.StubBins({"claude": h.claude_research(gaps=False)}, patch_path=False) as sb:
            r = h.run_scry(["--json", "--no-anim", "--config", cfg, "what is X?"],
                           input="", env=sb.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["mode"], "research")
        self.assertEqual(data["brief"]["sub_questions"], ["sub-q-1", "sub-q-2", "sub-q-3"])
        self.assertEqual(len(data["rounds"]), 1)
        self.assertEqual(data["final"], "RESEARCH ANSWER")
        self.assertNotIn("streamed", data)        # stripped under --json

    def test_plain_output_prints_final(self):
        cfg = _write_cfg(CLAUDE_ONLY, {"max_rounds": 1, "hard_cap": 1, "clarify": False})
        with h.StubBins({"claude": h.claude_research(gaps=False)}, patch_path=False) as sb:
            r = h.run_scry(["--no-anim", "--config", cfg, "what is X?"],
                           input="", env=sb.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        self.assertIn("RESEARCH ANSWER", r.stdout)


class TestResearchCliFlags(unittest.TestCase):
    def test_max_rounds_flag_caps_rounds(self):
        # Persistent gaps would otherwise run to hard_cap; --max-rounds overrides it.
        cfg = _write_cfg(CLAUDE_ONLY, {"max_rounds": 1, "hard_cap": 5, "clarify": False})
        with h.StubBins({"claude": h.claude_research(gaps=True, needs_web=True)},
                        patch_path=False) as sb:
            r = h.run_scry(["--json", "--no-anim", "--config", cfg,
                            "--max-rounds", "2", "what is X?"], input="", env=sb.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        data = json.loads(r.stdout)
        self.assertEqual(len(data["rounds"]), 2)

    def test_depth_flag_is_accepted(self):
        cfg = _write_cfg(CLAUDE_ONLY, {"max_rounds": 1, "hard_cap": 1, "clarify": False})
        with h.StubBins({"claude": h.claude_research(gaps=False)}, patch_path=False) as sb:
            r = h.run_scry(["--json", "--no-anim", "--config", cfg,
                            "--depth", "2", "what is X?"], input="", env=sb.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")

    def test_no_clarify_flag_is_accepted(self):
        cfg = _write_cfg(CLAUDE_ONLY, {"max_rounds": 1, "hard_cap": 1, "clarify": True})
        with h.StubBins({"claude": h.claude_research(gaps=False)}, patch_path=False) as sb:
            r = h.run_scry(["--json", "--no-anim", "--no-clarify", "--config", cfg,
                            "what is X?"], input="", env=sb.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        self.assertEqual(json.loads(r.stdout)["status"], "ok")


class TestResearchCliRepo(unittest.TestCase):
    def test_repo_flag_grounds_panel_in_repo(self):
        repo = tempfile.mkdtemp(prefix="scry-repo-")
        os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
        cfg = _write_cfg(CLAUDE_ONLY, {"max_rounds": 1, "hard_cap": 1, "clarify": False})
        with h.StubBins({"claude": h.claude_research(gaps=False, report_cwd=True)},
                        patch_path=False) as sb:
            r = h.run_scry(["--json", "--no-anim", "--config", cfg,
                            "--repo", repo, "what is X?"], input="", env=sb.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        data = json.loads(r.stdout)
        claude = next(x for x in data["responses"] if x["model"] == "claude-opus")
        # realpath: macOS resolves /var -> /private/var, so the stub's getcwd() differs
        # textually from `repo` though it is the same directory (grounding worked).
        self.assertIn(f"CWD={os.path.realpath(repo)}", claude["content"])


class TestResearchCliRouting(unittest.TestCase):
    def test_round_two_skips_no_web_provider(self):
        cfg = _write_cfg(FULL_PANEL, {"max_rounds": 1, "hard_cap": 2, "clarify": False})
        with h.StubBins(_full_stubs(gaps=True, needs_web=True), patch_path=False) as sb:
            r = h.run_scry(["--json", "--no-anim", "--config", cfg, "what is X?"],
                           input="", env=sb.env)
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        data = json.loads(r.stdout)
        round2 = [x["model"] for x in data["rounds"][1]["responses"] if x["ok"]]
        self.assertNotIn("deepseek", round2)
        self.assertIn("claude-opus", round2)


class TestResearchCliDryRun(unittest.TestCase):
    def test_dry_run_shows_judge_and_proposers(self):
        cfg = _write_cfg(CLAUDE_ONLY, {"max_rounds": 1, "hard_cap": 1, "clarify": False})
        r = h.run_scry(["--dry-run", "--config", cfg, "what is X?"], input="")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr!r}\nstdout={r.stdout!r}")
        self.assertIn("mode=research", r.stdout)
        self.assertIn("PROPOSER", r.stdout)
        # Research's per-round judge is the REFLECT stage (web-off), not "JUDGE".
        self.assertIn("REFLECT", r.stdout)
        self.assertIn("AGGREGATOR", r.stdout)


if __name__ == "__main__":
    unittest.main()
