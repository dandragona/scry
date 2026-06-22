"""Tests for `scry init` — the interactive setup wizard (do_init) and the _ask
helper. The wizard is driven end-to-end via the REAL ./scry as a subprocess
(h.run_scry) with piped answers and --no-anim, writing to a temp --out path; we
then load the written JSON and assert on it. claude/kimi stubs are placed on PATH
(via StubBins(...).env) so the "✓ installed" marks resolve and no real model CLI
is ever invoked.

Prompt order in do_init (after the welcome splash, which is non-interactive with
--no-anim / piped stdin):
  1. Panel members  (comma-separated numbers; 1=claude 2=codex 3=agy 4=kimi)
  2. Judge model    (default = first panel member as provider[:model])
  3. Aggregator     (default = first panel member)
  4. Enable web?    (y/n, default y)
  5. Overwrite?     — ONLY if dest exists and --force not given (default n)
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h


def _stub_env():
    """A PATH env with claude + kimi stubs so the provider 'installed' probe
    (shutil.which) resolves them. We don't ship codex/agy so those show as
    not-found, which is fine — do_init still adds them if selected."""
    return h.StubBins({
        "claude": h.claude_json("CLAUDE"),
        "kimi-cli": h.kimi_text("KIMI"),
    })


class InitWizardSubprocessTest(unittest.TestCase):
    """Drive `./scry init --out <tmp> --no-anim` as a subprocess with piped
    answers, then load + assert the written config.json."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="scry-init-test-")
        self.out = os.path.join(self.tmp, "config.json")
        self._stubs = _stub_env()
        self.env = self._stubs.env

    def tearDown(self):
        import shutil
        # StubBins was created without entering the context manager (we only want
        # its .env, not a process-PATH patch); clean its temp dir up by hand.
        shutil.rmtree(self._stubs.dir, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, stdin, out=None, force=False):
        args = ["init", "--no-anim", "--out", out or self.out]
        if force:
            args.append("--force")
        return h.run_scry(args, input=stdin, env=self.env, timeout=60)

    def _load_out(self, path=None):
        with open(path or self.out) as f:
            return json.load(f)

    # ----- happy path: a valid panel ---------------------------------------- #
    def test_valid_panel_claude_kimi(self):
        # panel "1,5:kimi-k2.6" (claude + kimi@kimi-k2.6), default judge,
        # default aggregator, web "y". Provider order: 1 claude, 2 codex, 3 agy,
        # 4 deepseek, 5 kimi.
        cp = self._run("1,5:kimi-k2.6\n\n\ny\n")
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        cfg = self._load_out()

        provs = [m["provider"] for m in cfg["panel"]]
        self.assertEqual(provs, ["claude", "kimi"])
        self.assertEqual(cfg["panel"][1]["model"], "kimi-k2.6")
        # labels are unique
        labels = [m["label"] for m in cfg["panel"]]
        self.assertEqual(len(set(labels)), len(labels))
        # judge + aggregator default to the first panel member (claude)
        self.assertEqual(cfg["judge"]["provider"], "claude")
        self.assertEqual(cfg["aggregator"]["provider"], "claude")
        self.assertIs(cfg["settings"]["web_tools"], True)
        # mode is fusion + the canonical settings keys are present
        self.assertEqual(cfg["mode"], "fusion")
        for k in ("web_tools", "effort", "max_output_tokens"):
            self.assertIn(k, cfg["settings"])

    # ----- repeated members get unique labels ------------------------------- #
    def test_repeats_get_unique_labels(self):
        # panel "1,1" (claude twice). First claude defaults to model "opus",
        # which == INIT_SUGGEST's suggested model, so its base label is the
        # suggested "claude-opus"; the second repeat gets the "-2" suffix.
        cp = self._run("1,1\n\n\ny\n")
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        cfg = self._load_out()
        provs = [m["provider"] for m in cfg["panel"]]
        self.assertEqual(provs, ["claude", "claude"])
        labels = [m["label"] for m in cfg["panel"]]
        self.assertEqual(labels, ["claude-opus", "claude-opus-2"])

    # ----- web "n" turns web_tools off -------------------------------------- #
    def test_web_no_disables_web_tools(self):
        # panel "1" (claude), default judge, default aggregator, web "n".
        cp = self._run("1\n\n\nn\n")
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        cfg = self._load_out()
        self.assertIs(cfg["settings"]["web_tools"], False)

    # ----- invalid judge provider -> fail-fast, nothing written ------------- #
    def test_invalid_judge_provider_writes_nothing(self):
        bad = os.path.join(self.tmp, "bad.json")
        # panel "1" (claude), judge "nope" (not a configured provider),
        # <enter> aggregator. Validation must fail before any write.
        cp = self._run("1\nnope\n\n", out=bad)
        self.assertNotEqual(cp.returncode, 0)
        self.assertFalse(os.path.exists(bad),
                         "init must not write a config when judge validation fails")

    def test_invalid_aggregator_provider_writes_nothing(self):
        bad = os.path.join(self.tmp, "bad2.json")
        # panel "1" (claude), default judge (claude), aggregator "nope" -> invalid.
        cp = self._run("1\n\nnope\n", out=bad)
        self.assertNotEqual(cp.returncode, 0)
        self.assertFalse(os.path.exists(bad))

    # ----- no panel selected -> fail, nothing written ----------------------- #
    def test_empty_selection_writes_nothing(self):
        bad = os.path.join(self.tmp, "empty.json")
        cp = self._run("\n", out=bad)
        self.assertNotEqual(cp.returncode, 0)
        self.assertFalse(os.path.exists(bad))

    def test_out_of_range_selection_writes_nothing(self):
        bad = os.path.join(self.tmp, "oor.json")
        # "9" is out of range (only 1-5 exist) -> skipped -> empty panel.
        cp = self._run("9\n", out=bad)
        self.assertNotEqual(cp.returncode, 0)
        self.assertFalse(os.path.exists(bad))

    # ----- overwrite guard -------------------------------------------------- #
    def test_overwrite_declined_leaves_file_unchanged(self):
        # Pre-create the destination with sentinel content.
        sentinel = '{"sentinel": true}\n'
        with open(self.out, "w") as f:
            f.write(sentinel)
        # panel "1", default judge, default aggregator, web "y", then "n" to the
        # overwrite prompt (dest exists + no --force).
        cp = self._run("1\n\n\ny\nn\n")
        self.assertEqual(cp.returncode, 1, cp.stderr + cp.stdout)
        with open(self.out) as f:
            self.assertEqual(f.read(), sentinel, "declined overwrite must not touch file")

    def test_overwrite_accepted_writes_config(self):
        with open(self.out, "w") as f:
            f.write('{"sentinel": true}\n')
        # ... web "y", then "y" to overwrite.
        cp = self._run("1\n\n\ny\ny\n")
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        cfg = self._load_out()
        self.assertEqual([m["provider"] for m in cfg["panel"]], ["claude"])
        self.assertNotIn("sentinel", cfg)

    def test_force_overwrites_without_prompt(self):
        with open(self.out, "w") as f:
            f.write('{"sentinel": true}\n')
        # With --force the overwrite prompt is skipped entirely, so only the four
        # wizard answers are piped (panel, judge, aggregator, web).
        cp = self._run("1\n\n\ny\n", force=True)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        cfg = self._load_out()
        self.assertEqual([m["provider"] for m in cfg["panel"]], ["claude"])
        self.assertNotIn("sentinel", cfg)


class InitDestinationTest(unittest.TestCase):
    """Where `scry init` writes when --out is NOT given: the global
    ~/.config/scry/config.json by default, or ./scry.config.json with --local.
    HOME + cwd are sandboxed to temp dirs so the real ones are never touched."""

    def setUp(self):
        import tempfile
        self.home = tempfile.mkdtemp(prefix="scry-init-home-")
        self.proj = tempfile.mkdtemp(prefix="scry-init-proj-")
        self._stubs = _stub_env()
        self.env = self._stubs.env
        self.env["HOME"] = self.home
        self.global_cfg = os.path.join(self.home, ".config", "scry", "config.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._stubs.dir, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.proj, ignore_errors=True)

    # panel "1" (claude), <enter> judge, <enter> aggregator, "y" web, <enter> to
    # accept the default "Write config to" path (only prompted when --out is absent).
    _ANSWERS = "1\n\n\ny\n\n"

    def test_default_writes_global_config(self):
        cp = h.run_scry(["init", "--no-anim"], input=self._ANSWERS,
                        env=self.env, cwd=self.proj, timeout=60)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        self.assertTrue(os.path.exists(self.global_cfg),
                        "init with no --out must write the global ~/.config/scry/config.json")
        with open(self.global_cfg) as f:
            cfg = json.load(f)
        self.assertEqual([m["provider"] for m in cfg["panel"]], ["claude"])
        # Nothing was dropped into the working directory.
        self.assertFalse(os.path.exists(os.path.join(self.proj, "scry.config.json")))
        self.assertFalse(os.path.exists(os.path.join(self.proj, "config.json")))

    def test_local_writes_project_config_not_global(self):
        cp = h.run_scry(["init", "--no-anim", "--local"], input=self._ANSWERS,
                        env=self.env, cwd=self.proj, timeout=60)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        local_cfg = os.path.join(self.proj, "scry.config.json")
        self.assertTrue(os.path.exists(local_cfg),
                        "init --local must write ./scry.config.json in the cwd")
        with open(local_cfg) as f:
            cfg = json.load(f)
        self.assertEqual([m["provider"] for m in cfg["panel"]], ["claude"])
        # The global config must be left untouched.
        self.assertFalse(os.path.exists(self.global_cfg),
                         "init --local must not write the global config")

    def test_out_overrides_local_flag(self):
        # An explicit --out wins even when --local is also passed (no path prompt).
        explicit = os.path.join(self.proj, "sub", "picked.json")
        cp = h.run_scry(["init", "--no-anim", "--local", "--out", explicit],
                        input="1\n\n\ny\n", env=self.env, cwd=self.proj, timeout=60)
        self.assertEqual(cp.returncode, 0, cp.stderr + cp.stdout)
        self.assertTrue(os.path.exists(explicit))
        self.assertFalse(os.path.exists(os.path.join(self.proj, "scry.config.json")))
        self.assertFalse(os.path.exists(self.global_cfg))


class AskUnitTest(unittest.TestCase):
    """Direct unit test of scry._ask (prompt helper)."""

    def setUp(self):
        self.scry = h.load_scry()

    def test_eof_returns_default(self):
        import builtins
        def boom(_prompt):
            raise EOFError()
        orig = builtins.input
        builtins.input = boom
        self.addCleanup(setattr, builtins, "input", orig)
        self.assertEqual(self.scry._ask("Q", "the-default"), "the-default")

    def test_eof_no_default_returns_empty(self):
        import builtins
        def boom(_prompt):
            raise EOFError()
        orig = builtins.input
        builtins.input = boom
        self.addCleanup(setattr, builtins, "input", orig)
        self.assertEqual(self.scry._ask("Q"), "")

    def test_strips_whitespace(self):
        import builtins
        orig = builtins.input
        builtins.input = lambda _prompt: "  x  "
        self.addCleanup(setattr, builtins, "input", orig)
        self.assertEqual(self.scry._ask("Q", "def"), "x")

    def test_empty_input_returns_default(self):
        import builtins
        orig = builtins.input
        builtins.input = lambda _prompt: "   "
        self.addCleanup(setattr, builtins, "input", orig)
        self.assertEqual(self.scry._ask("Q", "def"), "def")


if __name__ == "__main__":
    unittest.main()
