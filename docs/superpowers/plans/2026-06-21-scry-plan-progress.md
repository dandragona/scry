# Headless `scry plan` Pipeline Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface per-stage / per-model pipeline progress on stderr during the headless `scry plan --step` draft and interview rounds, so the `/scry-plan` skill can relay "where it is in the pipeline" to the user live.

**Architecture:** The progress events already exist inside `scry_run` / `gather_questions` / `dedup_questions`, emitted through a `log` callback. The headless `--step` path silences that callback (`log = lambda _m: None`) to keep stdout to one JSON envelope. This plan routes that callback to **stderr** instead (stdout contract unchanged), adds the per-round stage headers the interactive path already prints, and updates the skill doc to run the draft in the background and relay stderr progress.

**Tech Stack:** Python 3 (single-file `scry` CLI), `unittest` (subprocess-driven tests via `tests/_harness.py`), Markdown skill doc.

## Global Constraints

- **stdout contract:** every `--step` call emits **exactly one JSON envelope** on stdout (`questions` / `ready` / `done` / `error`). Progress must NOT go to stdout.
- **Progress channel:** human-readable lines on **stderr** only (`▸ stage…`, `✓ model (Ns)`, `✗ model: err`).
- **No new behavior for normal runs / interactive TUI** — those already show progress via the orb + stderr `log`.
- **Out of scope (do not implement here):** heartbeat/elapsed ticker, NDJSON stdout events, sidecar progress file, any `--no-progress` opt-out.
- **Test runner:** `python3 -m unittest discover -s tests` (hermetic; ~525 tests must stay green).
- Reuse the existing event strings verbatim — do not invent new wording. The panel header is `▸ panel: N proposers — …`; synthesis is `▸ synthesis: writing the fused answer…`.

---

### Task 1: Route headless `--step` progress to stderr

**Files:**
- Modify: `scry` — `do_plan_step` (`scry:2924`, and the gather/dedup block `scry:2980-2987`), `_plan_step_finalize` (signature `scry:2868-2872`, scry_run call `scry:2877-2881`), and the finalize call site (`scry:2972-2975`).
- Test: `tests/test_plan.py` — add two tests to `class PlanStepSubprocessTest` (after `test_done_drafts_and_writes_files`, ~`tests/test_plan.py:817`).

**Interfaces:**
- Consumes: `scry_run(cfg, prompt, mode, settings, log, …, panel_system=…)` — `log` is the 5th positional arg, a `Callable[[str], None]`. `gather_questions(…, log, …)` / `dedup_questions(…, log, …)` take `log` as their 5th positional arg.
- Produces: `_plan_step_finalize(..., round_no, log=None, cli_overrides=None)` — new `log` keyword param; when `None` it falls back to a stderr writer. `do_plan_step`'s local `log` becomes a stderr-writing function (was a no-op).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_plan.py` inside `class PlanStepSubprocessTest` (mirrors the existing `test_done_drafts_and_writes_files` temp-dir pattern so the draft's plan files are captured and cleaned up):

```python
    # ----- the draft streams pipeline progress to stderr -------------------- #
    def test_done_draft_emits_progress_to_stderr(self):
        d = tempfile.mkdtemp(prefix="scry-step-prog-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        env = self._env(h.claude_plan(rounds_before_ready=1))
        rec, cp = self._run(self._start_args(), json.dumps({"done": True}),
                            env, cwd=d)
        self.assertEqual(rec["status"], "done")
        # Pipeline progress lands on stderr…
        self.assertIn("panel", cp.stderr)
        self.assertIn("synthesis", cp.stderr)
        # …and never pollutes stdout, which stays exactly one JSON envelope.
        self.assertNotIn("▸", cp.stdout)              # the ▸ stage marker
        self.assertEqual(cp.stdout.strip().count("\n"), 0)  # single line

    # ----- each interview round shows panel/judge activity on stderr -------- #
    def test_question_round_emits_progress_to_stderr(self):
        env = self._env(h.claude_plan(rounds_before_ready=1))
        rec, cp = self._run(self._start_args(), "", env)
        self.assertEqual(rec["status"], "questions")
        self.assertIn("gathering clarifying questions", cp.stderr)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_plan.PlanStepSubprocessTest.test_done_draft_emits_progress_to_stderr tests.test_plan.PlanStepSubprocessTest.test_question_round_emits_progress_to_stderr -v`
Expected: FAIL — `cp.stderr` is empty (the no-op `log` drops every event), so `assertIn("panel", cp.stderr)` and `assertIn("gathering clarifying questions", cp.stderr)` fail.

- [ ] **Step 3: Route `do_plan_step`'s `log` to stderr**

In `scry`, in `do_plan_step`, replace this line (`scry:2924`):

```python
    log = lambda _m: None  # noqa: E731 — headless: keep stdout to the JSON envelope
```

with:

```python
    # Headless: stdout carries exactly one JSON envelope, so route human-readable
    # pipeline progress (▸ stage / ✓ model / ✗ model) to stderr instead of dropping
    # it. The /scry-plan skill relays these so the user can see where the (often
    # multi-minute) draft is in the panel → judge → synthesis pipeline.
    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)
```

- [ ] **Step 4: Add the per-round stage headers in `do_plan_step`**

The interactive path prints `▸ panel: gathering…` / `▸ judge: deduplicating…` in `do_plan._round`; the step path calls `gather_questions`/`dedup_questions` directly without them. Add them. Replace this block (`scry:2980-2987`):

```python
    try:
        raw, all_ready = asyncio.run(gather_questions(
            cfg, request, transcript, interview_set, log, meters, depth, cwd))
        if all_ready or not raw:
            deduped = []
        else:
            deduped = asyncio.run(dedup_questions(
                cfg, request, transcript, raw, interview_set, log, meters, depth, cwd))
    finally:
```

with:

```python
    try:
        log("▸ panel: gathering clarifying questions…")
        raw, all_ready = asyncio.run(gather_questions(
            cfg, request, transcript, interview_set, log, meters, depth, cwd))
        if all_ready or not raw:
            deduped = []
        else:
            log("▸ judge: deduplicating questions…")
            deduped = asyncio.run(dedup_questions(
                cfg, request, transcript, raw, interview_set, log, meters, depth, cwd))
    finally:
```

- [ ] **Step 5: Thread `log` into `_plan_step_finalize`**

In `scry`, change the `_plan_step_finalize` signature (`scry:2868-2872`) to accept `log`:

```python
def _plan_step_finalize(cfg: dict, request: str, transcript: list, settings: dict,
                        plan_settings: dict, run_id: str, meters: list, t0: float,
                        repo_cwd: str | None, out: str | None, no_out: bool,
                        no_save: bool, round_no: int, log=None,
                        cli_overrides: dict | None = None) -> int:
```

Immediately after the docstring (before the `try:` at `scry:2876`), add the stderr fallback so the function is safe to call without a `log`:

```python
    if log is None:
        def log(msg: str) -> None:  # default: pipeline progress to stderr
            print(msg, file=sys.stderr, flush=True)
```

Then in the `scry_run(...)` call (`scry:2877-2881`) replace the no-op `lambda _m: None` with `log`:

```python
        result = asyncio.run(scry_run(
            cfg, render_plan_prompt(request, transcript), "fusion",
            settings, log,
            aggregator_system=PLAN_SYNTH_SYSTEM, cwd=repo_cwd,
            run_overlay=cfg["phases"].get("final"), cli_overrides=cli_overrides))
```

- [ ] **Step 6: Pass `do_plan_step`'s `log` into the finalize call**

In `do_plan_step`, update the finalize call site (`scry:2972-2975`) to pass `log=log`:

```python
    if done or interview_done or rounds_done >= max_rounds:
        return _plan_step_finalize(cfg, request, transcript, settings, plan_settings,
                                   run_id, meters, t0, repo_cwd, out, no_out, no_save,
                                   rounds_done, log=log, cli_overrides=cli_overrides)
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `python3 -m unittest tests.test_plan.PlanStepSubprocessTest.test_done_draft_emits_progress_to_stderr tests.test_plan.PlanStepSubprocessTest.test_question_round_emits_progress_to_stderr -v`
Expected: PASS (both).

- [ ] **Step 8: Run the full plan test module to check for regressions**

Run: `python3 -m unittest tests.test_plan -v`
Expected: PASS — all existing `PlanStepSubprocessTest` / `PlanSubprocessTest` tests still green (stdout envelopes unchanged).

- [ ] **Step 9: Commit**

```bash
git add scry tests/test_plan.py
git commit -m "$(cat <<'EOF'
Stream headless scry plan pipeline progress to stderr

The --step draft and interview rounds ran the pipeline with a no-op log,
so the /scry-plan skill saw nothing until the (15-35 min) draft finished.
Route the log callback to stderr and add the per-round stage headers the
interactive path already prints. stdout still carries exactly one JSON
envelope, so the --step protocol and its tests are unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Document the background-relay loop in the `scry-plan` skill

**Files:**
- Modify: `/Users/danielmandragona/orca/projects/scry/.claude/skills/scry-plan/SKILL.md` (the repo's canonical copy). Note: the installed copy at `~/.claude/skills/scry-plan/SKILL.md` is regenerated by `install.sh` / heal mode — do not hand-edit it.

**Interfaces:**
- Consumes: the stderr progress channel produced by Task 1.
- Produces: skill instructions telling the orchestrator to background the draft and relay stderr progress. (No code; verified by reading.)

- [ ] **Step 1: Add the channel-contract + background-relay note**

In `SKILL.md`, in the `## Notes` section, immediately after the `--no-anim is mandatory` bullet (the first bullet of Notes), insert this new bullet:

```markdown
- **Show progress during the long draft.** stdout carries exactly one JSON envelope;
  scry streams human-readable pipeline progress to **stderr** (`▸ panel: …`,
  `✓ claude-opus (180s)` as each model lands, `▸ judge: …`, `▸ synthesis: …`). A
  *foreground* call returns nothing until it exits, so run the `{"done":true}` draft
  **in the background**, then periodically read the shell's **stderr** and relay a short
  one-line note to the user as each stage/model lands (paraphrase the `▸`/`✓` lines —
  don't dump raw output). When the process exits, parse the final **stdout** JSON
  envelope exactly as before. (The interview-round calls also emit stderr progress, but
  they're fast enough to run foreground.)
```

- [ ] **Step 2: Cross-reference it from the `ready` → draft branch**

In `SKILL.md`, in `## The loop`, the `ready` bullet currently reads:

```markdown
   - **`ready`** → the panel is confident. Draft the plan:
```

Replace it with:

```markdown
   - **`ready`** → the panel is confident. Draft the plan — run this call **in the
     background** and relay its stderr progress to the user (see *Show progress* in Notes):
```

- [ ] **Step 3: Verify the edits read correctly**

Run: `git -C /Users/danielmandragona/orca/projects/scry diff -- .claude/skills/scry-plan/SKILL.md`
Expected: shows the new Notes bullet and the reworded `ready` bullet; no other changes; Markdown still well-formed (no broken code fences).

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/scry-plan/SKILL.md
git commit -m "$(cat <<'EOF'
scry-plan skill: relay draft progress from stderr in the background

Document the stdout(one-envelope)/stderr(progress) channel split and tell
the orchestrator to background the {"done":true} draft and relay stderr
pipeline progress, so the user sees panel → judge → synthesis as it runs.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Full-suite verification

**Files:** none (verification only).

**Interfaces:** none.

- [ ] **Step 1: Run the entire hermetic suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK — all tests pass (≈525), no failures or errors.

- [ ] **Step 2: Manual smoke of the stderr channel (optional but recommended)**

Run (in a throwaway dir, with real providers OR the test stub on PATH):
`scry plan "tiny test" --no-anim --step --json --panel claude:opus --judge claude:opus --aggregator claude:opus --no-out <<< '{"done":true}' 1>/tmp/scry.out 2>/tmp/scry.err`
Expected: `/tmp/scry.out` is a single JSON line (`status":"done"`); `/tmp/scry.err` contains `▸ panel:` and `▸ synthesis:` lines.

- [ ] **Step 3: Finish the branch**

Use the `superpowers:finishing-a-development-branch` skill to choose merge / PR / cleanup for `feat/scry-plan-progress`.

---

## Self-Review

**Spec coverage:**
- "Emit stage + per-model progress during headless draft" → Task 1 Steps 3, 5, 6 + test Step 1.
- "Same for interview rounds (panel gathering / judge dedup)" → Task 1 Step 4 + `test_question_round_emits_progress_to_stderr`.
- "Keep stdout to one JSON envelope" → Global Constraints + the `assertNotIn("▸", cp.stdout)` / single-line assertions in Task 1 Step 1; existing tests in Task 1 Step 8.
- "Update skill to background + relay" → Task 2.
- "Tests: keep stdout tests green, add stderr-capture test, TDD-first" → Task 1 (test-first), Task 3 Step 1.
- Out-of-scope items (heartbeat/NDJSON/sidecar/TUI) → none introduced. ✓

**Placeholder scan:** No TBD/TODO; every code step shows exact old→new text and exact commands. ✓

**Type consistency:** `log` is `Callable[[str], None]` everywhere; `_plan_step_finalize` gains `log=None` with a stderr fallback and is called with `log=log`; `scry_run`'s 5th positional arg receives `log`. Event strings (`▸ panel:`, `▸ synthesis:`, `gathering clarifying questions`) match what `scry_run`/`do_plan` already emit. ✓
