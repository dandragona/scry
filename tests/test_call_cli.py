"""Tests for scry.call_cli — the per-provider subprocess invocation.

Every test drives the REAL call_cli coroutine against STUB provider binaries
dropped on PATH by h.StubBins (never a real, paid model CLI). cwd is always a
throwaway temp dir so outfile/agentfile temp writes land somewhere disposable.
"""
import copy
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402

# A stub that reports the working directory it actually ran in (capture='text'):
# reads stdin (so stdin-prompt providers don't block) and prints os.getcwd().
PWD_STUB = (
    "#!/usr/bin/env python3\n"
    "import os, sys\n"
    "sys.stdin.read()\n"
    "sys.stdout.write(os.getcwd())\n"
)


class TestCallCli(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scry = h.load_scry()
        self.cfg = self.scry.load_config(str(h.CONFIG_JSON))
        self.cwd = tempfile.mkdtemp(prefix="scry-callcli-cwd-")

    async def _call(self, cfg, provider, model, system, user, *, web=True):
        return await self.scry.call_cli(
            cfg, provider, model, system, user, self.cwd, 0, web,
            cfg["settings"],
        )

    # ---- claude json success ------------------------------------------- #
    async def test_claude_json_success(self):
        with h.StubBins({"claude": h.claude_json("RESULT")}):
            out = await self._call(self.cfg, "claude", "opus", None, "hi")
        self.assertEqual(out, "RESULT")

    # ---- claude error_field -> ProviderError --------------------------- #
    async def test_claude_error_field(self):
        with h.StubBins({"claude": h.claude_json("model said no", is_error=True)}):
            with self.assertRaises(self.scry.ProviderError) as ctx:
                await self._call(self.cfg, "claude", "opus", None, "hi")
        msg = str(ctx.exception)
        self.assertIn("model error", msg)
        # The error_field branch surfaces the result_path text as the detail.
        self.assertIn("model said no", msg)

    # ---- codex outfile capture ----------------------------------------- #
    async def test_codex_outfile_capture(self):
        with h.StubBins({"codex": h.codex_outfile("CDX")}):
            out = await self._call(self.cfg, "codex", "", None, "hi")
        self.assertEqual(out, "CDX")

    # ---- agy: prompt arrives as an ARG, not stdin ---------------------- #
    async def test_agy_text_prompt_as_arg(self):
        with h.StubBins({"agy": h.agy_text("GEM")}):
            out = await self._call(self.cfg, "agy", "Gemini 3.1 Pro (High)",
                                   None, "hi")
        self.assertEqual(out, "GEM")

    # ---- kimi: text capture + temp agent file written/cleaned ---------- #
    async def test_kimi_text_with_agent_file(self):
        with h.StubBins({"kimi-cli": h.kimi_text("KMI")}):
            out = await self._call(self.cfg, "kimi", "k2", None, "hi")
        self.assertEqual(out, "KMI")

    # ---- unknown provider ---------------------------------------------- #
    async def test_unknown_provider(self):
        with self.assertRaises(self.scry.ProviderError) as ctx:
            await self._call(self.cfg, "nope", "", None, "hi")
        self.assertIn("unknown provider", str(ctx.exception))

    # ---- command not found on PATH ------------------------------------- #
    async def test_command_not_found(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["providers"]["claude"]["cmd"] = ["scry-nonexistent-xyz", "-p"]
        # No stub provided for that name, and it's not a real binary.
        with h.StubBins({}):
            with self.assertRaises(self.scry.ProviderError) as ctx:
                await self._call(cfg, "claude", "opus", None, "hi")
        self.assertIn("command not found", str(ctx.exception))

    # ---- timeout -------------------------------------------------------- #
    async def test_timeout(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["settings"]["timeout"] = 1   # per-call timeout is a (phase) setting now
        with h.StubBins({"claude": h.hang(5)}):
            with self.assertRaises(self.scry.ProviderError) as ctx:
                await self._call(cfg, "claude", "opus", None, "hi")
        self.assertIn("timeout", str(ctx.exception))

    # ---- empty output (json capture, exit 0, no text) ------------------ #
    async def test_empty_output(self):
        # A stub that reads stdin and prints just a newline -> strips to "".
        empty_stub = (
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.stdin.read()\n"
            "sys.stdout.write('\\n')\n"
        )
        with h.StubBins({"claude": empty_stub}):
            with self.assertRaises(self.scry.ProviderError) as ctx:
                await self._call(self.cfg, "claude", "opus", None, "hi")
        # capture=json + empty raw -> extract returns "" -> the empty-output branch.
        self.assertIn("empty output", str(ctx.exception))

    # ---- env_unset: ANTHROPIC_API_KEY is popped for the child ---------- #
    async def test_env_unset_anthropic_api_key(self):
        # This claude stub reports whether ANTHROPIC_API_KEY is visible to it.
        probe_stub = (
            "#!/usr/bin/env python3\n"
            "import sys, json, os\n"
            "sys.stdin.read()\n"
            "val = 'SET' if os.environ.get('ANTHROPIC_API_KEY') else 'UNSET'\n"
            "print(json.dumps({'result': val, 'is_error': False}))\n"
        )
        with h.env_vars(ANTHROPIC_API_KEY="sk-should-be-stripped"):
            with h.StubBins({"claude": probe_stub}):
                out = await self._call(self.cfg, "claude", "opus", None, "hi")
        # call_cli pops env_unset keys before spawning the child.
        self.assertEqual(out, "UNSET")

    # ---- codex: system folded into the prompt (system_flag is null) ---- #
    async def test_codex_system_folded_into_prompt(self):
        # A codex stub that echoes its stdin into the -o outfile so we can
        # observe exactly what prompt text the child received.
        echo_stub = (
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "argv = sys.argv[1:]\n"
            "out = None\n"
            "for i, a in enumerate(argv):\n"
            "    if a == '-o' and i + 1 < len(argv):\n"
            "        out = argv[i + 1]\n"
            "data = sys.stdin.read()\n"
            "if out:\n"
            "    open(out, 'w').write(data)\n"
            "else:\n"
            "    sys.stdout.write(data)\n"
        )
        with h.StubBins() as s:
            s.add("codex", echo_stub)
            out = await self._call(self.cfg, "codex", "", "SECRET-SYS", "Q")
        # system is prepended to the user prompt on stdin since codex has no
        # system_flag (folded form: system + "\n\n" + user).
        self.assertIn("SECRET-SYS", out)
        self.assertIn("Q", out)


class TestCwdIsolation(unittest.IsolatedAsyncioTestCase):
    """`scry plan` hands the panel the user's real repo cwd so drafts are
    grounded in the code. A provider that enforces read-only (claude's
    --disallowedTools, codex's --sandbox read-only, kimi's agent file) is safe
    there. A provider with NO read-only mechanism (agy) is NOT — call_cli must
    run it in an isolated throwaway cwd so it can neither read nor mutate the
    project. These tests pin that contract."""

    def setUp(self):
        self.scry = h.load_scry()
        self.cfg = self.scry.load_config(str(h.CONFIG_JSON))
        self.repo = tempfile.mkdtemp(prefix="scry-fake-repo-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)

    async def _ran_in(self, provider):
        """Return the realpath of the cwd `provider` actually executed in when
        call_cli is asked to run it in self.repo."""
        binary = self.cfg["providers"][provider]["cmd"][0]
        with h.StubBins({binary: PWD_STUB}):
            out = await self.scry.call_cli(
                self.cfg, provider, "m", None, "hi", self.repo, 0, False,
                self.cfg["settings"])
        return os.path.realpath(out.strip())

    async def test_unsandboxed_provider_not_run_in_repo_cwd(self):
        # agy has no read-only flag/policy, so it must be isolated from the repo.
        ran_in = await self._ran_in("agy")
        self.assertNotEqual(ran_in, os.path.realpath(self.repo))

    async def test_repo_safe_provider_runs_in_repo_cwd(self):
        # kimi enforces read-only via its agent file, so it may read the repo.
        ran_in = await self._ran_in("kimi")
        self.assertEqual(ran_in, os.path.realpath(self.repo))


if __name__ == "__main__":
    unittest.main()
