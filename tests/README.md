# scry test suite

Stdlib `unittest` only — **no dependencies, nothing to `pip install`** (same hard
constraint as `scry` itself). Every test runs against **stub** model CLIs or
monkeypatched fakes, so the suite **never spends subscription credit**.

## Run everything

```sh
# from the repo root
python3 -m unittest discover -s tests -p 'test_*.py' -v
# plus the end-to-end shell smoke test
sh tests/smoke.sh
```

Run a single module:

```sh
python3 tests/test_render_call.py -v        # direct
python3 -m unittest tests.test_render_call  # via unittest
```

CI (`.github/workflows/ci.yml`) runs both the smoke test and the discover suite on
`ubuntu`/`macos` × Python `3.9`/`3.12`.

## Layout

- `_harness.py` — shared helpers (not a test module). Loads `scry`/`scry-eval` as
  importable modules, fabricates stub provider binaries on `PATH`, drives the real
  `./scry` as a subprocess, and stands up a localhost HTTP server for the
  `scry update` tests. **Money safety lives here:** the stubs only echo canned text.
- `smoke.sh` — end-to-end CLI smoke test against stub binaries (kept from before).
- `test_*.py` — one module per area:
  - **Pure units:** config loading/parsing, `render_call` argv/env, JSON extraction,
    kimi agent-file YAML, stream-event parsing, pipeline helpers, color/version/label
    helpers, the consensus map, the `RuneCircle` splash, the `ScryingOrb` animation.
  - **Integration (stubbed CLIs):** `call_cli`, `stream_call`, the full `scry_run`
    pipeline, `--dry-run`, `--check`, `init`, `update`, and the `main()` CLI surface.
  - **`scry-eval`:** objective grading, verdict/criterion parsing, DRACO aggregation
    and dataset loading, and the async judge/grade/eval drivers (monkeypatched).

## Writing a new test

Start the file with the harness bootstrap, then use `_harness` (imported as `h`):

```python
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
import _harness as h

class TestThing(unittest.TestCase):
    def test_it(self):
        scry = h.load_scry()
        with h.StubBins({"claude": h.claude_json("HI")}):
            ...

if __name__ == "__main__":
    unittest.main()
```

Async SUT code (`call_cli`, `stream_call`, `scry_run`, the eval drivers) uses
`unittest.IsolatedAsyncioTestCase`. For pipeline/eval *logic* (no subprocess),
monkeypatch the module-global function (`scry.call_cli = fake`) and restore it with
`self.addCleanup`. **Never** let a test invoke a real model CLI.
