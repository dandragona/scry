"""Tests for scry.stream_call (scry lines 534-604) and the _stream_extract parser
it relies on (lines 514-531).

stream_call(cfg, provider, model, system, user, cwd, depth, settings, sink) runs ONE
provider call in streaming mode: it spawns the provider CLI with the provider's
`stream.args`, reads stdout line-by-line, parses each line with _stream_extract, pushes
text deltas into `sink`, and returns {"text": <full answer>, "streamed": <bool>}.
It raises ProviderError when streaming is unavailable or produced nothing.

All subprocesses are stubbed via h.StubBins — no real model CLI is ever invoked.
"""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


def _custom_claude(lines):
    """A claude-style stream stub that reads+discards stdin then prints each of
    `lines` (already-formed dict objects) as a JSON line, flushing, then exits 0.
    Used to drive the result-only / assistant-fallback / empty-stream branches of
    _stream_extract precisely."""
    return h._py(
        "import sys, json\n"
        "sys.stdin.read()\n"
        f"for obj in {lines!r}:\n"
        "    print(json.dumps(obj), flush=True)\n"
    )


class TestStreamCall(unittest.IsolatedAsyncioTestCase):
    def _cfg(self):
        return h.load_scry().load_config(str(h.CONFIG_JSON))

    async def _run(self, cfg, provider, model="opus"):
        """Drive stream_call with a fresh sink list; return (result, sink_chunks)."""
        scry = h.load_scry()
        chunks = []
        cwd = tempfile.mkdtemp(prefix="scry-stream-cwd-")
        res = await scry.stream_call(
            cfg, provider, model, None, "hello user", cwd, 0,
            cfg["settings"], chunks.append)
        return res, chunks

    # --- happy path: per-char deltas + a final result line ------------------ #
    async def test_claude_stream_deltas_joined(self):
        scry = h.load_scry()
        cfg = self._cfg()
        with h.StubBins({"claude": h.claude_stream("streamed ok")}):
            res, chunks = await self._run(cfg, "claude")
        # Every delta char was sinked; joined they reconstruct the text.
        self.assertEqual("".join(chunks), "streamed ok")
        self.assertEqual(res["text"], "streamed ok")
        self.assertTrue(res["streamed"])

    # --- result-only stream: a final result, but NO content_block_delta ----- #
    async def test_result_only_no_deltas(self):
        scry = h.load_scry()
        cfg = self._cfg()
        stub = _custom_claude([{"type": "result", "result": "final"}])
        with h.StubBins({"claude": stub}):
            res, chunks = await self._run(cfg, "claude")
        # final_text drives the answer; no deltas were ever sinked.
        self.assertEqual(res["text"], "final")
        self.assertFalse(res["streamed"])
        self.assertEqual(chunks, [])

    # --- assistant fallback: a full assistant message, no deltas/result ----- #
    async def test_assistant_message_fallback(self):
        scry = h.load_scry()
        cfg = self._cfg()
        stub = _custom_claude([{
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        }])
        with h.StubBins({"claude": stub}):
            res, chunks = await self._run(cfg, "claude")
        self.assertEqual(res["text"], "hello")
        self.assertFalse(res["streamed"])
        self.assertEqual(chunks, [])

    # --- agent_file provider refuses streaming ------------------------------ #
    async def test_agent_file_provider_refuses(self):
        scry = h.load_scry()
        cfg = self._cfg()
        # kimi declares an agent_file policy but no `stream` format. The format
        # gate (lines 540-542) fires FIRST, so we must give kimi a stream format
        # to reach the agent_file gate (lines 543-546) we want to exercise. Giving
        # it a format also lets us prove that an agent_file policy is refused even
        # when a stream format IS present.
        cfg = copy.deepcopy(cfg)
        cfg["providers"]["kimi"]["stream"] = {"format": "claude"}
        with self.assertRaises(scry.ProviderError) as ctx:
            # No stub needed: stream_call raises before spawning a subprocess.
            await self._run(cfg, "kimi")
        self.assertIn("agent_file", str(ctx.exception))

    # --- provider with no stream format refuses ----------------------------- #
    async def test_no_stream_format_refuses(self):
        scry = h.load_scry()
        cfg = self._cfg()
        # codex has no `stream` key at all -> format is falsy.
        with self.assertRaises(scry.ProviderError) as ctx:
            await self._run(cfg, "codex", model="")
        self.assertIn("streaming not supported for this provider", str(ctx.exception))

    # --- empty stream output (exit 0, nothing on stdout) -------------------- #
    async def test_empty_stream_output(self):
        scry = h.load_scry()
        cfg = self._cfg()
        empty = h._py("import sys\nsys.stdin.read()\n")  # reads stdin, prints nothing
        with h.StubBins({"claude": empty}):
            with self.assertRaises(scry.ProviderError) as ctx:
                await self._run(cfg, "claude")
        self.assertIn("empty stream output", str(ctx.exception))

    # --- command not found on PATH ------------------------------------------ #
    async def test_command_not_found(self):
        scry = h.load_scry()
        cfg = copy.deepcopy(self._cfg())
        # Keep the stream format so we pass the format gate, but point cmd at a
        # binary that doesn't exist; shutil.which returns None -> ProviderError.
        cfg["providers"]["claude"]["cmd"] = ["scry-nonexistent-xyz", "-p"]
        with self.assertRaises(scry.ProviderError) as ctx:
            # No StubBins -> the bogus command is genuinely absent from PATH.
            await self._run(cfg, "claude")
        self.assertIn("command not found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
