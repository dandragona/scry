"""Tests that scry never orphans a (paid) child CLI on cancellation.

A panel call runs an external model CLI as a subprocess. If the user hits Ctrl-C
(or scry is SIGTERM'd), the in-flight child must be killed — otherwise it keeps
running and billing against the user's subscription. scry starts children in their
own session/process-group and kills the group on every non-normal exit path.
"""
import asyncio
import contextlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class TestChildKilledOnCancel(unittest.TestCase):
    def test_cancelling_call_cli_kills_the_child(self):
        scry = h.load_scry()
        cfg = scry.load_config(str(h.CONFIG_JSON))
        pidfile = os.path.join(tempfile.mkdtemp(prefix="scry-pid-"), "pid")
        # A scry-deepseek stub that records its PID then sleeps well past the test.
        body = (
            "import os, sys, time\n"
            f"open({pidfile!r}, 'w').write(str(os.getpid()))\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        cwd = tempfile.mkdtemp(prefix="scry-cwd-")

        async def go():
            task = asyncio.create_task(scry.call_cli(
                cfg, "deepseek", "", None, "hi", cwd, 0, False, cfg["settings"]))
            # Wait until the child has spawned and written its PID.
            for _ in range(100):
                if os.path.exists(pidfile) and Path(pidfile).read_text().strip():
                    break
                await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        with h.StubBins({"scry-deepseek": h._py(body)}):
            asyncio.run(go())

        pid = int(Path(pidfile).read_text().strip())
        # Give the kill a moment to land, then confirm the child is gone.
        deadline = time.time() + 3.0
        alive = True
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.05)
            except ProcessLookupError:
                alive = False
                break
        if alive:  # best-effort cleanup so a failing run doesn't leave a 30s orphan
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, 9)
        self.assertFalse(alive, "child CLI survived cancellation (orphaned paid run)")


if __name__ == "__main__":
    unittest.main()
