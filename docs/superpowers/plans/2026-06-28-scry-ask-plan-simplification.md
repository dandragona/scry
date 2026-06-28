# scry "ask + plan" Simplification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse scry's user-facing surface to two things — `scry "X"` (deep research, the only query path) and `scry plan "X"` (interview → research → per-panelist draft → fuse) — plus a matching CLI flag cleanup, web-UI collapse, and docs rewrite.

**Architecture:** Delete the user-facing `--mode` selector and the `fusion`/`synthesize` query modes; bare `scry` always runs `do_research`. `scry_run` stays as internal plumbing (plan finalize and the web still call it with `"fusion"`). Plan gains a research phase by inserting a `research_run(...)` call into `_plan_step_finalize` before the existing `PLAN_DRAFTER_SYSTEM` draft fan-out, feeding the research synthesis to the drafters instead of the bare Q&A transcript. No backward compatibility for removed flags/modes.

**Tech Stack:** Single-file stdlib Python CLI (`./scry`, ~4628 lines), optional FastAPI/uvicorn web package (`scry_web/`, vanilla-JS SPA in `scry_web/static/`), stdlib `unittest` test suite (hermetic, provider CLIs stubbed via `tests/_harness.py`).

## Global Constraints

- **No backward compatibility.** `--mode` and the values `research`/`fusion`/`synthesize` are deleted outright; old flags (`--no-map`/`--no-repo`/`--no-out`/`--max-rounds`/`--force`) are removed with **no aliases**. A leftover `mode` key in a config is ignored (no warning).
- **DeepSeek + GLM stay** in the default panel. Do **not** trim the panel. Messaging: you don't *need* API keys to start, but scry fully *supports* them (DeepSeek/GLM are essential members).
- **`scry_run` is internal plumbing** — do NOT add a `mode="plan"`/`mode="research"` branch to it. Compose `research_run` + `scry_run("fusion", …)`.
- **Plan always runs research** (no `--no-research`); **no fast single-shot escape** (`--quick`/`--depth 0` rejected).
- Plan's research phase reuses standard research defaults (`research.max_rounds`=3, `research.hard_cap`=5); it exposes no separate knob. Plan's only tunable round count is `--interview-rounds` (`plan.max_rounds`).
- `--step` envelope contract is unchanged (same `questions`/`ready`/`done`/`error` statuses); finalize just runs longer. Stream research progress via the existing `log` callback (no new envelope state).
- Tests stay hermetic: run with `python3 -m unittest discover -s tests`. Never call a real model CLI — use `tests/_harness.py` stubs.
- Stdlib-only for `./scry` (no new deps). Match existing code style (the role-branching stubs key on **disjoint system-prompt anchor substrings** — keep anchors unique).

## Key existing anchors (verified)

- Dispatch / argparse: `scry:4285-4628`. Mode resolution: `scry:4476`. Bare-query research vs scry_run branch: `scry:4508-4552`. Plan dispatch: `scry:4434-4468`.
- `scry_run(cfg, prompt, mode, settings, log, …, aggregator_system, cwd, cli_overrides, run_overlay, panel_system, scrub_fuse_cwd)`: `scry:1523`.
- `research_run(cfg, prompt, settings, log, stream_final, on_stream_start, cwd, cli_overrides, answers, repo_summary, depth_override, cap_override, seed_meters)`: `scry:1711`.
- `_plan_step_finalize(...)`: `scry:3900-3940` (calls `scry_run(cfg, render_plan_prompt(request, transcript), "fusion", …, panel_system=PLAN_DRAFTER_SYSTEM, aggregator_system=PLAN_SYNTH_SYSTEM, scrub_fuse_cwd=True)`).
- `plan_step(...)`: `scry:3941-4040`. `do_plan_step(...)`: `scry:4043-4077`. `do_plan(...)`: `scry:3670-3899`.
- `do_research(...)`: `scry:4164-4267`. `_check_known_providers(ap, cfg, mode)`: `scry:4270-4282`.
- `render_plan_diagnostics(...)`: `scry:3327`; timeline build around `scry:3336-3446`; `_plan_result_fields` stamps `wall_seconds` from `started_at` at `scry:3601-3622`.
- System prompts: `MOA_AGGREGATOR_SYSTEM` `scry:60`, `JUDGE_SYSTEM` `scry:74`, `PLAN_INTERVIEWER_SYSTEM` `scry:92`, `PLAN_QUESTION_JUDGE_SYSTEM` `scry:112`, `PLAN_SYNTH_SYSTEM` `scry:129`, `PLAN_DRAFTER_SYSTEM` `scry:160`, `RESEARCH_BRIEF_SYSTEM` `scry:188`, `RESEARCH_PANEL_SYSTEM` `scry:206`, `RESEARCH_JUDGE_SYSTEM` `scry:223`, `RESEARCH_SYNTH_SYSTEM` `scry:246`.
- Defaults: `DEFAULT_SETTINGS` `scry:272`, `DEFAULT_PHASES` `scry:287`, `DEFAULT_CONFIG` (`"mode":"research"`) `scry:309-310`. `do_init` writes `"mode":"fusion"` at `scry:2170`. `do_check` `scry:2264` (call-count `scry:2328-2333`). `do_dry_run` `scry:1830`.
- Test-stub anchors: dedup `'deduplicating'`, plan synth `'plan drafts'`, interviewer `'scope a task'`, judge `'impartial judge'`, research brief `'research brief'`, research referee `'research referee'`, research synth `'research synthesis'`, research panel `'deep research analyst'`. Test hooks: `SCRY_SYSDUMP` (append every system prompt), `SCRY_CWDDUMP` (stage⇥cwd).
- Web: capability validated in `scry_web/api.py:114-116` (`scry`/`plan`/`research`). `scry_web/engine.py` `run_scry_sync` (`:164`), `run_research_sync` (`:176`, fusion+`RESEARCH_FRAMING`+web-on, tags `mode=research`), `provider_readiness(cfg, mode)` (`:254`). `scry_web/state.py:24-30` puts `mode` in `/api/status`. `scry_web/runs.py` routes capability→engine (`:74-102`, plan at `:61/141/151`). Frontend: `scry_web/static/{index.html,app.js,ui.js,api.js}`.

---

## Task 1: Remove the user-facing `--mode` selector; bare `scry` always researches

**Files:**
- Modify: `scry` (argparse `~4319-4322`; dispatch `~4470-4552`; `_check_known_providers` `~4270-4282`; `do_init` `~2170`)
- Test: `tests/test_cli_e2e.py`, `tests/test_config.py` (add cases); update any test passing `--mode`.

**Interfaces:**
- Produces: bare `scry "<q>"` and `scry "<q>" --json` run `do_research`. `--mode` no longer exists (argparse rejects it, exit 2). `_check_known_providers(ap, cfg)` — `mode` param removed; judge always required.
- Consumes: `do_research(cfg, prompt, settings, args, cli)` (unchanged), `scry_run` (kept, still used by plan/web).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_e2e.py  (add)
from tests._harness import load_scry, run_scry, StubBins, claude_research, default_stubs, CONFIG_JSON

def test_mode_flag_is_removed():
    r = run_scry(["--mode", "fusion", "hello"])
    assert r.returncode == 2
    assert "--mode" in (r.stderr or "")  # argparse "unrecognized arguments: --mode"

def test_bare_scry_runs_research_pipeline():
    stubs = dict(default_stubs())
    stubs["claude"] = claude_research(findings="F", fused="RESEARCH ANSWER")
    with StubBins(stubs) as b:
        # single-claude panel config so the research stub plays every role
        r = run_scry(["--config", str(CONFIG_JSON), "--panel", "claude", "--no-clarify",
                      "--json", "what is x"], env=b.env)
    assert r.returncode == 0, r.stderr
    import json; out = json.loads(r.stdout)
    assert out["final"] == "RESEARCH ANSWER"
    assert "rounds" in out  # research-shaped result, not a single-shot fusion result
```

- [ ] **Step 2: Run, verify they fail** — `python3 -m unittest tests.test_cli_e2e -v` (expect: `--mode` currently accepted / research not forced).

- [ ] **Step 3: Implement**
  - Delete the `ap.add_argument("--mode", …)` block (`scry:4319-4322`).
  - At `scry:4476` delete `mode = args.mode or cfg.get("mode", "research")` and the research-only-flag warning block (`scry:4478-4485`) — those flags now always apply.
  - Delete the dispatch branch: keep `do_research(...)`, remove the `if mode == "research": … else <orb + scry_run single-shot>` split (`scry:4508-4552` and the trailing single-shot output handling `4567-4610` that is now dead for the query path — verify nothing else uses it). Bare query path becomes: resolve panel/judge/aggregator overrides → settings → `--dry-run`/`--check` → `read_prompt` → `return do_research(cfg, prompt, settings, args, cli)`.
  - `_check_known_providers`: drop the `mode` param and the `if mode != "synthesize"` guard — always append the judge provider. Update both call sites (plan dispatch `~4443`, query `~4477`).
  - `do_init` (`scry:2170`): remove the `"mode": "fusion"` line from the generated config (write no `mode` key).
  - `do_dry_run`/`do_check` still take `mode` — handled in Task 2; for now pass `"research"` literal at their call sites (`4498`, `4502`) to keep them working.

- [ ] **Step 4: Run tests** — `python3 -m unittest tests.test_cli_e2e tests.test_config -v` → PASS. Then grep the suite for `--mode`/`"fusion"`/`"synthesize"` CLI usages and fix any that drove the removed path (`grep -rn -- "--mode" tests/`). `scry_run` direct tests (`tests/test_scry_run.py`) keep using `"fusion"`/`"synthesize"` — leave them.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(scry): remove --mode; bare scry always runs research"`

---

## Task 2: `--check` / `--dry-run` describe the research pipeline only

**Files:**
- Modify: `scry` `do_check` (`~2264-2346`), `do_dry_run` (`~1830-1911`); call sites `~4498/4502`.
- Test: `tests/test_check.py`, `tests/test_dry_run.py`.

**Interfaces:**
- Produces: `do_check(cfg, settings)` and `do_dry_run(cfg, settings, cli_overrides=None)` — `mode` param removed; both render the research pipeline (panel → reflect/judge → synthesis, gap-loop budget).

- [ ] **Step 1: Read the current bodies** — `sed -n '1830,1911p' scry` and `sed -n '2264,2346p' scry` to see the exact mode-branching (call-count at `2328-2333`, dry-run stage rendering).

- [ ] **Step 2: Write/adjust failing tests**

```python
# tests/test_check.py  (adjust the call-count assertion)
def test_check_reports_research_pipeline():
    # with an N-member panel, --check prints a research-shaped estimate, no "fusion"
    ...
    assert "fusion" not in out.lower()
    assert "research" in out.lower() or "rounds" in out.lower()
```

- [ ] **Step 3: Run, verify fail.**

- [ ] **Step 4: Implement** — drop `mode` from both signatures and call sites. In `do_check`, replace the `(2 if mode=="fusion" else 1)` call math with the research estimate (panel × rounds + reflect/round + synthesis; reuse `research.max_rounds`/`hard_cap`). In `do_dry_run`, render the research stages (brief → panel(web) → reflect → [gap rounds ≤ hard_cap] → synth) unconditionally; remove the fusion/synthesize previews.

- [ ] **Step 5: Run** — `python3 -m unittest tests.test_check tests.test_dry_run -v` → PASS.

- [ ] **Step 6: Commit** — `git commit -am "feat(scry): --check/--dry-run describe the research pipeline"`

---

## Task 3: Plan = interview → **research** → draft → fuse

**Files:**
- Modify: `scry` — add `render_plan_prompt_from_research(...)` near `render_plan_prompt` (`~2973`); modify `_plan_step_finalize` (`~3900-3940`); tweak `PLAN_DRAFTER_SYSTEM` (`~160`).
- Test: `tests/test_plan.py`.

**Interfaces:**
- Consumes: `research_run(cfg, request, settings, log, cwd=repo_cwd, cli_overrides=…, answers=transcript, seed_meters=meters)` → `{brief, rounds, responses, analysis, final, cost}`; `scry_run("fusion", panel_system=PLAN_DRAFTER_SYSTEM, aggregator_system=PLAN_SYNTH_SYSTEM, scrub_fuse_cwd=True)`.
- Produces: `render_plan_prompt_from_research(request, transcript, research) -> str` — the drafter prompt, built from the **research synthesis** (`research["final"]` + `research["analysis"]`) plus the binding Q&A constraints, NOT the bare transcript.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_plan.py  (add)
import json, os, tempfile
from tests._harness import load_scry

def _plan_combo_stub(...):
    """A claude stub that plays BOTH research roles (brief/referee/synthesis/panel)
    AND plan roles (interviewer/dedup/drafter/judge/plan-synth) by branching on the
    disjoint anchors. Reuse claude_research + claude_plan bodies merged, keyed on the
    same anchor substrings; 'deep research analyst' must return findings, and the
    final-draft panel proposer (no research anchor, no plan anchor) returns a draft."""

def test_plan_drafters_receive_research_not_bare_transcript(tmp_path):
    scry = load_scry()
    sysdump = tmp_path / "sys.txt"
    # drive plan_step to finalize (done=True) with a one-claude panel; assert via
    # SCRY_SYSDUMP that a research anchor (e.g. 'research synthesis') was invoked
    # BEFORE the PLAN_DRAFTER_SYSTEM draft, and that the drafter prompt text carries
    # the research 'final' answer.
    ...
    dumped = sysdump.read_text()
    assert "research synthesis" in dumped          # research phase ran
    assert "draft" in dumped.lower()               # PLAN_DRAFTER_SYSTEM ran after
    assert env["status"] == "done"

def test_plan_cost_includes_research_calls(tmp_path):
    # the finalize envelope's cost reflects research panel+referee+synth calls,
    # not just the draft fan-out.
    ...
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**
  - Add `render_plan_prompt_from_research(request, transcript, research)`: compose the original request + the interview Q&A (as binding constraints) + a "Research findings (use these; do not re-investigate):" block containing `research.get("final","")` and a condensed `research.get("analysis")` (consensus/contradictions/blind_spots). (Open question from spec §9: include fused `final` + `analysis` only, NOT the full per-round evidence, to keep the prompt bounded — chosen here.)
  - In `_plan_step_finalize`, before the existing `scry_run(... "fusion" ...)` draft call: `log("▸ researching the request…")`; `research = asyncio.run(research_run(cfg, request, settings, log, cwd=repo_cwd, cli_overrides=cli_overrides, answers=transcript, seed_meters=meters))`. Wrap in `try/except AllPanelsFailed` → return an `error` envelope (same shape as the existing draft failure).
  - Replace the draft call's prompt `render_plan_prompt(request, transcript)` → `render_plan_prompt_from_research(request, transcript, research)`.
  - Ensure research meters fold into `meters` (so `_plan_result_fields`/cost include them). If `research_run` does not mutate `seed_meters` in place, extend `meters` with `research["cost"]`'s per-call meters (verify by reading `research_run`'s `seed_meters` handling at `scry:1711+`; the `cost_includes_research` test is the gate).
  - `PLAN_DRAFTER_SYSTEM` (`scry:160`): adjust framing from "investigate and draft" to "draft an implementation plan **from the provided research synthesis**; do not re-investigate." Keep any anchor the test stub keys on intact (the drafter is the no-anchor panel proposer in the draft fan-out; do not introduce a substring colliding with a research/plan anchor).

- [ ] **Step 4: Run** — `python3 -m unittest tests.test_plan -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(plan): run research before drafting; drafters plan from the synthesis"`

---

## Task 4: Plan diagnostics gain a research segment

**Files:**
- Modify: `scry` `render_plan_diagnostics` (`~3327-3446`), and wherever per-call meters are tagged with a `round`/stage so research calls are attributable.
- Test: `tests/test_plan.py` (diagnostics assertions).

**Interfaces:**
- Consumes: meters now include research-phase calls (Task 3).
- Produces: the rendered `.diagnostics.md` has a research section/segment in its timeline; `wall_seconds` spans interview + research + draft/fuse.

- [ ] **Step 1: Read** `sed -n '3327,3446p' scry` to see the timeline/bottleneck builder and how meters carry `round`/stage labels.

- [ ] **Step 2: Write failing test**

```python
# tests/test_plan.py  (add) — finalize with --out to a tmp dir, read <plan>.diagnostics.md
def test_diagnostics_show_research_segment(tmp_path):
    ...
    diag = (tmp_path / "<plan>.diagnostics.md").read_text()
    assert "research" in diag.lower()          # a research segment is listed
    assert "## timeline" in diag
```

- [ ] **Step 3: Implement** — tag research-phase meters with a recognizable stage (e.g. `m["stage"]="research"` or `m["round"]="research"`) when folding them in (Task 3), and extend the timeline renderer to print a "research" segment between the interview rounds and the final draft. Ensure the bottleneck scan includes research calls.

- [ ] **Step 4: Run** — `python3 -m unittest tests.test_plan -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(plan): diagnostics show the research segment"`

---

## Task 5: Flag cleanup — tri-state values + de-overload (no aliases)

**Files:**
- Modify: `scry` argparse (`~4323-4394`) and all consumers of the changed flags (`do_research`, plan dispatch, `do_init`/`do_update`, the consensus-map gating in `do_research`).
- Test: `tests/test_cli_e2e.py`, `tests/test_plan.py`, `tests/test_research_cli.py`, `tests/test_update.py`, `tests/test_init.py`.

**Interfaces (new flag surface):**
- `--map {auto,on,off}` (default `auto`) replaces `--map`/`--no-map`.
- `--repo {auto,none,<PATH>}` replaces `--repo [PATH]`/`--no-repo` (`auto`=detect cwd repo, `none`=scrubbed, else a path).
- `--out PATH` with `--out -` = stdout, replaces `--out`/`--no-out`.
- `--interview-rounds N` (plan) and `--hard-cap N` (research) replace the overloaded `--max-rounds`.
- `--overwrite` (init) and `--allow-downgrade` (update) replace the overloaded `--force`.
- `--depth N` unchanged. `--no-clarify`, `--no-web`, `--no-save` unchanged (genuine single-sense booleans).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cli_e2e.py (add)
def test_removed_flags_error():
    for flag in ("--no-map", "--no-repo", "--no-out", "--max-rounds", "--force"):
        r = run_scry([flag, "x"]); assert r.returncode == 2, flag

def test_map_tristate_parses():
    for v in ("auto", "on", "off"):
        r = run_scry(["--map", v, "--check", "--config", str(CONFIG_JSON)])
        assert r.returncode in (0, 1)   # parses (check exit), not a parse error (2)
```
Plus: a plan test using `--interview-rounds 1`, a research test using `--hard-cap 2`, an init test using `--overwrite`, an update test using `--allow-downgrade`, and an `--out -` (stdout) plan test.

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** — rewrite the argparse blocks:
  - `--map`: `choices=["auto","on","off"], default="auto"`. Update the consensus-map gate (`do_research` `~4242`, and any remaining in `main`) to: show if `args.map=="on"` or (`args.map=="auto"` and `sys.stderr.isatty()`); never if `"off"`.
  - `--repo`: `choices`-free string `default="auto"`; in `_resolve_research_repo` and the plan `repo_context` calc, map `auto`→detect/cwd, `none`→scrubbed (None), else treat as PATH. Remove `--no-repo`.
  - `--out`: keep as string; remove `--no-out`. In `_write_plan_files`/`do_plan`/`do_plan_step`, treat `out == "-"` as the old `no_out=True` (stdout only).
  - Replace `--max-rounds` with `--interview-rounds` (plan dispatch → `plan_settings["max_rounds"]`) and `--hard-cap` (research → `cap_override`). Update `do_plan`/`do_plan_step`/`do_research` call args accordingly.
  - Replace `--force` with `--overwrite` (→ `do_init`) and `--allow-downgrade` (→ `do_update`). Update both signatures' call sites.
  - Update `_cli_overrides` and any `args.no_map/args.no_repo/args.no_out/args.max_rounds/args.force` references across the file (grep them: `grep -n "no_map\|no_repo\|no_out\|max_rounds\|args.force" scry`).

- [ ] **Step 4: Run** — `python3 -m unittest discover -s tests -v` (full suite; fix any test still using the old flags).

- [ ] **Step 5: Commit** — `git commit -am "feat(scry): tri-state --map/--repo/--out; split --max-rounds/--force (no aliases)"`

---

## Task 6: Progressive `--help`, verb grouping, demote `--step`

**Files:**
- Modify: `scry` argparse help/epilog (`~4296-4395`); add `--help-all`.
- Test: `tests/test_cli_e2e.py`.

**Interfaces:**
- Default `--help` shows: usage, the two verbs (`scry "X"`, `scry plan "X"`) + utility verbs, and ~6 everyday flags (`--no-web`, `--effort`, `--json`, `--check`, `--no-clarify`, `--repo`). `scry --help-all` shows everything. `--step` is documented under an "advanced/internal" section, not the everyday list.

- [ ] **Step 1: Write failing test**

```python
def test_default_help_is_short_and_help_all_is_full():
    short = run_scry(["--help"]).stdout
    full = run_scry(["--help-all"]).stdout
    assert "--step" not in short and "--host" not in short
    assert "--step" in full and "--host" in full
    assert 'scry plan' in short
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** — argparse lacks native tiered help; implement by (a) putting everyday flags in the default parser with `help=…`, (b) giving rarely-used/verb-specific flags `help=argparse.SUPPRESS`, and (c) adding a `--help-all` action that prints a full, hand-formatted list (or re-parses with all helps un-suppressed). Group the epilog "commands" by verb. Keep `--step` functional but `SUPPRESS`-ed from default help.

- [ ] **Step 4: Run** — `python3 -m unittest tests.test_cli_e2e -v` → PASS. Confirm the `/scry-plan` skill invokes `--step` directly (not via help discovery): `grep -rn "step" .claude/ docs/ 2>/dev/null` — it constructs the argv itself, so hiding from help is safe.

- [ ] **Step 5: Commit** — `git commit -am "feat(scry): progressive --help; demote internal --step"`

---

## Task 7: Web UI collapse — Ask / Plan

**Files:**
- Modify: `scry_web/api.py` (`:114-117` capability validation), `scry_web/engine.py` (`run_scry_sync`/`run_research_sync`/`provider_readiness`/`_fake_result`), `scry_web/state.py` (`:24-30` drop `mode`), `scry_web/runs.py` (capability routing `:61-102`), and the frontend `scry_web/static/{index.html,app.js,ui.js}`.
- Test: `tests/test_web_api.py`, `tests/test_web_engine.py`.

**Interfaces:**
- Capabilities reduce to `("ask","plan")`. `"ask"` runs the research-style one-shot (today's `run_research_sync`, web-on); `"plan"` runs `plan_step` (which now includes research, inherited from Task 3). The legacy `"scry"`/`"research"` capability strings are removed; no `mode`/fusion/synthesize option is accepted. `/api/status` no longer includes a `mode` field.

- [ ] **Step 1: Write failing tests** (these skip cleanly if FastAPI is absent — follow the existing `test_web_api.py` skip guard)

```python
def test_status_has_no_mode_field(client):
    s = client.get("/api/status").json()
    assert "mode" not in s

def test_capability_is_ask_or_plan(client, conv):
    r = client.post(f"/api/conversations/{conv}/messages",
                    json={"capability": "fusion", "content": "x"})
    assert r.status_code == 400
    ok = client.post(f"/api/conversations/{conv}/messages",
                     json={"capability": "ask", "content": "x"})
    assert ok.status_code in (200, 201)
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement (backend)**
  - `api.py`: `if capability not in ("ask", "plan"): raise HTTPException(400, …)`.
  - `runs.py`: route `capability == "plan"` → plan path (unchanged); everything else (`"ask"`) → `engine.run_research_sync`. Remove the `run_scry_sync` (fusion) path and the `"research"` branch.
  - `engine.py`: keep `run_research_sync` as the "ask" engine; delete `run_scry_sync`'s `mode = options.mode or cfg.get("mode")` knob and the synthesize path; drop `mode` from `provider_readiness` default (always research-shaped). Keep `scry.scry_run(... "fusion" ...)` internally (it's plumbing).
  - `state.py`: remove `"mode"` from the `/api/status` payload (`:30`); call `provider_readiness(cfg)`.
  - `store.py`/`locations.py`: `capability` column stays; no schema change needed (values just become `ask`/`plan`).

- [ ] **Step 4: Implement (frontend)** — read `scry_web/static/app.js`, `ui.js`, `index.html` first. Replace the capability picker + `mode` dropdown with a two-button group **Ask / Plan**. Remove the fusion/synthesize `mode` selector and any `options.mode` sent in the message payload. In the advanced-options panel, hide fields that don't apply to the selected verb (show `interview-rounds` only for Plan; keep `effort`/`web_tools`/`timeout` for both). Map button → `capability: "ask"|"plan"`.

- [ ] **Step 5: Run** — `python3 -m unittest tests.test_web_api tests.test_web_engine -v` → PASS (or skip without FastAPI). If FastAPI is installed locally, also smoke `scry web --no-open` boots.

- [ ] **Step 6: Commit** — `git commit -am "feat(web): collapse to Ask/Plan; drop mode from status"`

---

## Task 8: Docs, README & demo

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, any `docs/*` referencing modes/removed flags, `.env.example` if it mentions modes.
- Demo: `README` gif (regeneration likely needs the user — see note).

- [ ] **Step 1:** `grep -rniE -- "--mode|fusion|synthesize|--no-map|--no-repo|--no-out|--max-rounds|--force" README.md docs/ CHANGELOG.md .env.example` to enumerate every reference.

- [ ] **Step 2:** Rewrite the README around the two-command surface (`scry "X"` + `scry plan "X"`). Remove every `--mode`/fusion/synthesize mention and every removed-flag mention; document the new flags (`--map auto|on|off`, `--repo auto|none|PATH`, `--out -`, `--interview-rounds`, `--hard-cap`, `--overwrite`, `--allow-downgrade`). Describe plan as "interview → research → drafts → fused plan."

- [ ] **Step 3: Messaging** — keep "you don't need API keys to start scrying, and scry fully supports them (DeepSeek + GLM are first-class panel members)." Remove any "no API keys"-absolutist wording and any suggestion to shrink the panel to a zero-key set.

- [ ] **Step 4:** Add a `CHANGELOG.md` entry: the breaking removal of `--mode`/old flags, plan-now-researches, web Ask/Plan.

- [ ] **Step 5: Demo gif** — regenerating requires real model runs + a terminal recorder (asciinema/vhs). **This step likely needs the user** (paid calls + recording tool not available headlessly). Leave a checklist of the commands to record (`scry "…"`, `scry plan "…"`) and flag it for the user; do not fabricate a gif.

- [ ] **Step 6: Commit** — `git commit -am "docs: rewrite for the two-command (ask/plan) surface"`

---

## Final verification

- [ ] Run the full hermetic suite: `python3 -m unittest discover -s tests` → all pass (web tests skip cleanly without FastAPI).
- [ ] `grep -rnE -- "--mode|cfg.get\(.mode" scry scry_web` → no user-facing mode resolution remains (internal `scry_run(... "fusion" ...)` calls are expected and fine).
- [ ] `scry --help` is short; `scry --help-all` is complete; `scry --mode fusion x` exits 2.
- [ ] Use superpowers:requesting-code-review before merging.

## Self-review notes (coverage check)

- Spec §5.1 surface → Task 1, 6. §5.2 plan pipeline → Task 3. §5.3 hard mode removal → Task 1 (+ init). §5.4 flag cleanup + round-knob rename + help → Tasks 5, 6. §5.5 web collapse → Task 7. §5.6 diagnostics + `--step` log progress → Tasks 3 (log), 4 (diagnostics). §6 no-back-compat → Tasks 1, 5 (removed-flag-errors tests). §7 testing → each task's tests + Final verification. §7.1 docs/README/messaging/gif → Task 8. §10 follow-up (`scry setup`) intentionally NOT included.
- Open question (§9 drafter prompt content) resolved in Task 3: fused `final` + condensed `analysis`, not full evidence.
