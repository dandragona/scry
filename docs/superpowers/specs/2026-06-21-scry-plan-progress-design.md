# Headless `scry plan` pipeline progress

**Date:** 2026-06-21
**Status:** Approved (design)
**Scope:** `scry` CLI (`--step` headless path) + the `scry-plan` skill — surface
per-stage / per-model progress during the headless plan draft so an orchestrator
(Claude Code) can relay "where it is in the pipeline" to the user.

## Motivation

`scry plan` drives the full Mixture-of-Agents pipeline for its final draft: the panel
of proposers (in parallel) → judge → synthesis, run in sequence. For a research-heavy
request each stage is web-on and long-form, so the draft legitimately takes 15–35
minutes (the `final` phase per-call timeout is 2100s).

In the headless `--step --json` protocol used by the `/scry-plan` skill, the finalize
draft (`_plan_step_finalize`) runs the pipeline with `log=lambda _m: None` — progress
is **deliberately silenced** to keep stdout to exactly one JSON envelope. The same is
true of the per-round interview calls in `do_plan_step` (`log = lambda _m: None`).

The result: the orchestrator (and therefore the user) sees nothing — an empty output
file — for the entire run, then everything appears at once when the terminal envelope
prints. The user has no insight into which stage is running or whether it is alive.

The progress signals **already exist** inside `scry_run`/`gather_questions`/
`dedup_questions` (`▸ panel: N proposers…`, `✓ claude-opus (180s)`, `✗ label: err`,
`▸ judge: comparing responses…`, `▸ synthesis: writing the fused answer…`). They are
emitted through the `log` callback. The only reason the headless path is blind is that
its `log` callback throws them away.

## Goals

- During a headless `--step` draft, emit the existing stage + per-model progress events
  so an orchestrator can relay them live.
- Same for the per-round interview calls (`panel: gathering questions` /
  `judge: deduplicating`), since each round is also a multi-model call the user waits on.
- Keep the `--step --json` contract intact: **stdout still carries exactly one JSON
  envelope per call.** Progress goes to **stderr**.
- Update the `scry-plan` skill so the orchestrator runs the draft in the background and
  relays stderr progress as it lands — live updates, not a 30-minute silent block.

## Non-goals

- A heartbeat / elapsed-time ticker during a long single-model wait. (Considered and
  declined — stage + per-model events are enough; a timer thread is out of scope.)
- Structured machine-readable progress events (NDJSON on stdout). Rejected: it would
  break the "exactly one envelope" contract and require rewriting the skill's parser and
  the stdout-envelope tests for no real gain — an LLM relays human-readable lines fine.
- Any change to the interactive (TUI) `scry plan`, which already shows progress via the
  scrying orb and stderr `log`.
- A progress sidecar file. Rejected: extra file + polling machinery; the blocking call
  still only returns at the end.

## Design

### Channel contract

For every `--step` call:

- **stdout** = exactly one JSON envelope (`questions` / `ready` / `done` / `error`) —
  **unchanged**. This is the data channel the skill parses.
- **stderr** = human-readable progress lines (`▸ …`, `✓ … (Ns)`, `✗ …`) emitted as the
  pipeline advances. With `--no-anim` (mandatory for the skill) and no orb in step mode,
  stderr contains only these clean progress lines.

### scry change

In `do_plan_step` / `_plan_step_finalize`, replace the no-op `log` with one that writes
to stderr, flushed:

```python
def _progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
```

- Pass `_progress` into `scry_run(...)` in `_plan_step_finalize` (instead of
  `lambda _m: None`) so the draft emits `▸ panel…`, per-proposer `✓/✗`, `▸ judge…`,
  `▸ synthesis…`.
- Use `_progress` for the `gather_questions` / `dedup_questions` calls in `do_plan_step`
  (instead of the local `log = lambda _m: None`) so each interview round shows its
  panel/judge activity.

No change to `scry_run` itself — it already emits these events through its `log`
parameter. No change to stdout in any path.

### Skill change (`scry-plan/SKILL.md`)

Make explicit what the orchestrator must do to get live updates (a foreground Bash call
returns nothing until it exits):

- Run the `{"done":true}` draft call **in the background**.
- While it runs, periodically read the shell's **stderr** and relay a short one-line
  progress note to the user as each stage/model lands (paraphrase the `▸`/`✓` lines;
  don't dump raw lines).
- When the process exits, parse the final **stdout** JSON envelope exactly as today.
- Document the channel split: stdout = one JSON envelope; stderr = progress.
- Keep `--no-anim` mandatory and the generous timeout guidance.

## Testing

- Existing `--step` stdout-envelope tests stay green — stdout is unchanged in every path.
- Add a test asserting the finalize path writes the expected progress markers to
  **stderr** (e.g. a panel marker and the `synthesis` marker), while stdout still
  contains exactly the single `done` (or `error`) envelope. Follow TDD: write the failing
  stderr-capture test first, then make the `log` change.
- Run the suite hermetically: `python3 -m unittest discover -s tests`.

## Risks / notes

- **Other `--step` consumers:** only the `scry-plan` skill is known to drive `--step`.
  Because stdout is unchanged, any consumer parsing stdout is unaffected; stderr progress
  is purely additive.
- **stderr noise:** with `--no-anim` and no orb in step mode, stderr is limited to the
  progress lines, so it stays clean and cheap to relay.
- **No opt-out flag** is added (YAGNI). If a future consumer wants silence, a
  `--no-progress` flag can be added then.
