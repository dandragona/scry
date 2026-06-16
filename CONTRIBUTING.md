# Contributing to scry

Thanks for your interest! `scry` is a small, deliberately minimal tool. A few
hard constraints keep it that way — please read these before opening a PR.

## Non-negotiables

1. **Standard library only — no new dependencies, ever.** `scry` and `scry-eval`
   run on stock Python 3.9+ with zero `pip install`. That "one file, nothing to
   install" property is half the point of the project. A PR that adds `requests`,
   `rich`, `httpx`, etc. will be declined no matter how nice it is. (Need HTTP?
   `urllib`. Need color? we already have a tiny HSV helper in `ScryingOrb`.)
2. **Two executable scripts, no package.** `scry` (the CLI) and `scry-eval` (the
   harness) are single-file executables with no `.py` extension. Keep it that way.
3. **Providers are data, not code.** New CLIs (a new panel member, a new judge)
   should be added as a provider record in `config.json` — mirror the existing
   `claude` / `codex` / `agy` records — not as a special case in `scry`. The
   `cmd` / `model_flag` / `capture` / `caps` schema is meant to absorb new CLIs.
4. **Money safety.** Every real run spends subscription credit. Anything that
   could trigger a paid call must be guarded, and CI must never spend (see below).

## Before you open a PR

- `python3 -m py_compile scry scry-eval` — must pass.
- `python3 -m unittest discover -s tests -p 'test_*.py'` — the full suite must pass
  (stdlib only, runs against stub CLIs — $0). See `tests/README.md`.
- `sh tests/smoke.sh` — the end-to-end CLI smoke test must pass.
- `./scry --dry-run "test"` — eyeball that command construction is unchanged.
- `./scry --check` — should still pass on your machine.
- If you touched provider flag wiring (`render_call` / the `caps` blocks), include
  the relevant `--dry-run` output in the PR so reviewers can see the argv diff, and
  extend `tests/test_render_call.py` / `tests/test_dry_run.py` to cover it.
- Keep the README's flag table and `config.json` in sync with any new flag.

## Testing without spending

CI runs against **stub** `claude` / `codex` / `agy` / `kimi` binaries on `PATH` that
echo canned output (and monkeypatched fakes for async internals), so the whole CLI —
`--dry-run`, `--check`, `init`, `update`, `call_cli`, the `scry_run` pipeline — is
exercised end-to-end for $0. The stub helpers live in `tests/_harness.py`; **never**
add a test that calls a real model CLI. Real evals (`scry-eval`) cost money and need
live subscriptions — run those locally and paste the `results.json` summary if your
change affects scoring.

## Scope

`scry` replicates OpenRouter's Fusion pipeline locally. Features that keep it a
small, honest, single-user orchestrator are welcome; turning it into a hosted
service, a proxy, or a multi-tenant product is out of scope.
