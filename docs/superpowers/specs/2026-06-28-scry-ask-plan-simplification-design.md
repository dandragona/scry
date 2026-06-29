# scry simplification: collapse to "ask" + "plan"

- **Date:** 2026-06-28
- **Status:** Design approved (pending spec review)
- **Branch context:** `beta-readiness-review`

## 1. Problem

scry has accreted five+ overlapping user-facing surfaces. A newcomer must internalize
three query modes (`research`/`fusion`/`synthesize`), a verb-vs-flag split (`plan`/`web`/`init`
are positional verbs but the query mode is a `--flag`), 31 CLI flags (with three doubled
boolean pairs and three overloaded flags), and a parallel "capability + mode" double-picker
in the web UI. The mode distinction is the worst offender: the code itself treats `fusion`
and `synthesize` as behaviorally near-identical (both single-shot, both emit one final
answer; `synthesize` merely drops one judge call), and there is a live contradiction in the
defaults — `DEFAULT_CONFIG["mode"] = "research"` (`scry:310`) but `scry init` writes
`"mode": "fusion"` (`scry:2170`), so "research by default" stops being true the moment a user
follows the quickstart.

## 2. Goal

Reduce scry to **two user-facing things**:

- `scry "X"` — the default (and only) query behavior: today's deep-research pipeline. It is
  no longer framed as "a mode"; it is just what scry does.
- `scry plan "X"` — interview → **research** → per-panelist plan draft → fuse.

Plan becomes "research plus a planning phase": after the clarifying interview, run the
research pipeline once, then have each panelist draft an implementation plan from the shared,
web-grounded research synthesis, and fuse those drafts into one plan.

Pair the mode collapse with a CLI flag cleanup and a matching web-UI collapse so the surface
actually *feels* simpler (the mode collapse alone does not deliver that if the flag zoo stays).

## 3. Non-goals (explicitly out of scope here)

- A fast single-shot escape hatch (`--quick`/`--depth 0`). **Decided against** — research is
  the only query path. Quick questions pay the full loop cost by design.
- A plan-without-research path (`--no-research`). **Decided against** — plan *always* runs
  research.
- Making research checkpointable/resumable inside `--step`. Deferred (see §5.6).
- Cost ceilings (`--max-usd`). Out of scope (prior product decision).

## 4. Locked decisions (from design Q&A)

| Decision | Choice |
|---|---|
| Query surface | `scry "X"` (research, default) + `scry plan "X"` |
| `fusion` / `synthesize` modes | Removed as user-facing; `scry_run` stays as internal plumbing |
| Plan interview | **Kept**, and its answers feed the research phase |
| Plan always runs research? | **Yes**, no skip |
| Fast escape for `scry "X"`? | **No** |
| `--step` long research block | **Stream progress via `log`** (v1); no new protocol state |
| Spec scope | Core restructure + flag cleanup + web-UI collapse |

## 5. Design

### 5.1 Command surface (after)

```
scry "X"            → research pipeline (clarify → brief → gap rounds → fused answer)
scry plan "X"       → interview → research → draft → fuse → write files
scry web            → local web UI            (unchanged behavior; UI collapsed, see §5.5)
scry init           → setup wizard            (stops pinning a mode, see §5.3)
scry update         → self-update             (unchanged)
scry last | log [N] → history                 (unchanged)
```

`--mode` is removed entirely. See §5.3.

### 5.2 Plan pipeline (the core change)

```
interview   →   research   →   draft        →   fuse          →   write
(keep)          (NEW)          (exists)         (exists)          (exists)
```

1. **Interview** — `plan_step` loop is unchanged: panel proposes clarifying questions →
   judge dedups → user answers (interactive, or `--step` JSON for the skill/web). Checkpointed
   to `~/.scry` for `--resume`. Loop ends on `ready` / `done` / `--max-rounds`.

2. **Research (new)** — at finalize, run the research pipeline on the enriched request:
   `research_run(cfg, request, settings, log, answers=<interview transcript>,
   repo_summary=<repo summary if grounded>, cwd=repo_cwd, cli_overrides=...)`.
   The research pipeline's own clarifying-question step is skipped — plan's interview already
   asked the user, so research reuses those answers instead of asking again (no double
   interview). `research_run` accepts pre-supplied `answers` (verified: `scry:1711-1717`).
   Returns `{brief, rounds, responses, analysis, final, cost}`.

3. **Draft** — each panelist drafts a plan with `PLAN_DRAFTER_SYSTEM`, but the prompt is now
   built from the **research synthesis** (`final` + supporting `analysis`/evidence) instead of
   the bare Q&A transcript. This is the quality win: all drafters share one web-grounded
   evidence base instead of each re-investigating ad hoc (the current "half-strength plan
   panel" failure mode).

4. **Fuse** — judge compares the drafts; `PLAN_SYNTH_SYSTEM` aggregator fuses them into one
   structured plan. **This already ships verbatim** as the second half of the existing
   `scry_run("fusion", panel_system=PLAN_DRAFTER_SYSTEM, aggregator_system=PLAN_SYNTH_SYSTEM,
   scrub_fuse_cwd=True)` call.

5. **Write** — `scry-plan-<slug>.md` + `.diagnostics.md` via `_write_plan_files`. Unchanged
   except diagnostics gain a research segment (§5.6).

**Code seam.** The change is localized to `_plan_step_finalize` (`scry:3900`) and the
equivalent path in `do_plan`. Today that function calls
`scry_run(cfg, render_plan_prompt(request, transcript), "fusion", …)`. The new flow:

```
research = asyncio.run(research_run(cfg, request, settings, log,
                                    answers=transcript, repo_summary=repo_summary,
                                    cwd=repo_cwd, cli_overrides=cli_overrides,
                                    seed_meters=meters))
drafter_prompt = render_plan_prompt_from_research(request, transcript, research)
result = asyncio.run(scry_run(cfg, drafter_prompt, "fusion", settings, log,
                              aggregator_system=PLAN_SYNTH_SYSTEM, cwd=repo_cwd,
                              run_overlay=cfg["phases"].get("final"),
                              cli_overrides=cli_overrides,
                              panel_system=PLAN_DRAFTER_SYSTEM, scrub_fuse_cwd=True))
```

- Add a thin orchestrator (call it `plan_run`) that composes `research_run` → draft fan-out →
  fuse. **Do not** add a `mode="plan"` branch inside `scry_run` — keep the generic engine
  untouched so the hermetic test stubs (which route on system-prompt anchor substrings) keep
  working.
- Add `render_plan_prompt_from_research(request, transcript, research)` — builds the drafter
  prompt from the research synthesis. `PLAN_DRAFTER_SYSTEM` should be reviewed/tweaked so the
  drafter is told it is planning *from provided research*, not investigating from scratch.
- Cost meters: thread the interview + research + draft/fuse meters into one tally so
  `cost`/diagnostics reflect the full pipeline (`research_run` accepts `seed_meters`).

### 5.3 Mode removal

No backward compatibility (see §6) — modes are deleted outright, not deprecated.

- Remove `research`/`fusion`/`synthesize` entirely — from `--mode`, the help, the config
  schema, and the conceptual surface. The `--mode` flag is deleted. The query path always runs
  the research pipeline.
- Delete the `mode` resolution at `scry:4476`; there is nothing to resolve. A leftover `mode`
  key in an existing config is simply ignored (not honored, no warning).
- `scry init` stops writing `"mode": "fusion"` (`scry:2170`) and writes no `mode` key. This
  also resolves the `scry:310` vs `scry:2170` contradiction.
- **Known consequence:** the author's global config pins `mode: fusion`; that key is now inert
  and should be deleted from the config. Accepted.

### 5.4 Flag cleanup

Tri-state the three doubled boolean pairs into one value-flag each (old spellings removed, no
aliases — see §6):

| Today | After |
|---|---|
| `--map` / `--no-map` (`scry:4351-4353`) | `--map {auto\|on\|off}` (default `auto`) |
| `--repo [PATH]` / `--no-repo` (`scry:4328`, `4386`) | `--repo {auto\|none\|PATH}` |
| `--out PATH` / `--no-out` (`scry:4356-4360`) | `--out PATH`; `--out -` = stdout (Unix convention) |

De-overload flags that mean different things per verb (split into verb-specific names; old
names removed):

| Overloaded flag | Split |
|---|---|
| `--max-rounds` (plan = interview rounds; research = hard cap) | `--interview-rounds` (plan) / `--hard-cap` (research) |
| `--force` (init = overwrite; update = allow downgrade) | `--overwrite` (init) / `--allow-downgrade` (update) |

**Round knobs.** Plan exposes exactly one tunable round count: `--interview-rounds` (max
clarifying-interview rounds, `plan.max_rounds`). Plan's research phase is **not** separately
tunable — it uses the standard research defaults (§9). The two research knobs stay on the
`scry "X"` surface only:

- `--depth N` (`research.max_rounds`, default 3) — the *minimum* number of gap-driven research
  rounds: how deep it digs before it is even allowed to stop.
- `--hard-cap N` (`research.hard_cap`, default 5) — the *absolute ceiling*: past `depth` the
  loop keeps going only while the judge still names gaps, but never beyond this. A runaway stop.

Verb-scope & progressive help:

- Group `--help` by verb. Default help shows prompt usage, the two verbs, and the ~6 everyday
  flags (`--no-web`, `--effort`, `--json`, `--check`, `--no-clarify`, `--repo`). Everything else
  moves to `scry --help-all` / per-verb help (`scry plan --help`).
- Web-only (`--host`/`--port`/`--no-open`) and plan-only (`--step`/`--resume`/`--list`/`--out`)
  flags are documented under their verb, not the global list.
- Demote `--step` (`scry:4362`) out of user-facing help — it is an internal envelope for the
  `/scry-plan` skill and web backend. Move behind an "Advanced/internal" help section or an env
  gate; confirm the skill/web invoke it directly (not via help discovery) before hiding.

### 5.5 Web UI collapse

The web UI currently has the *same* confusion doubled: a "capability" picker (Scry/Plan/
Research) **plus** a `mode` dropdown (fusion/synthesize) for Scry, **plus** advanced fields that
silently no-op depending on capability.

- Replace the capability picker + mode dropdown with **one button group: `Ask` / `Plan`**,
  matching the new two-mode surface. No mode sub-choice.
- `/api/status` stops reporting a separate global `mode`; the server infers the pipeline from
  the verb (`Ask` → research, `Plan` → plan).
- Advanced options: hide fields that don't apply to the selected verb instead of greying them
  out. Keep `effort`, `web_tools`, `timeout` (both); `interview-rounds` shows only for Plan.
- Remove the now-dead fusion/synthesize option from the UI and from any per-run options payload.

### 5.6 Diagnostics

`render_plan_diagnostics` / the rebuilt `wall_seconds` + per-round timeline + bottleneck logic
(`scry:3327`) assume today's stage shape (interview → draft/fuse). Inserting research means:

- Add a **research segment** to the timeline (its rounds + reflect) so the bottleneck analysis
  doesn't mislabel research time as "drafting".
- `started_at` already persists across `--step` calls for true wall-clock; ensure the research
  phase's elapsed is captured within the same `started_at` window.

**`--step` progress (v1).** The finalize "done" step already blocks on draft+fuse and already
threads a `log` callback (`scry:3900` docstring). Adding research makes that block minutes, not
seconds. v1: stream research progress through the existing `log` relay (e.g. "researching… round
2/5") so the skill and web poller show activity rather than appearing hung. No new envelope
state. (Deferred: a `researching` envelope state + resumable research.)

## 6. Migration & backward compatibility

**None — no backward compatibility is provided or needed.**

- `--mode` and the `research`/`fusion`/`synthesize` values are deleted outright. A leftover
  `mode` key in an existing config is ignored.
- Old boolean flags (`--no-map`/`--no-repo`/`--no-out`) and old overloaded names
  (`--max-rounds`/`--force`) are removed with no aliases.
- Other config keys (panel/judge/aggregator/phases) are unchanged and keep working.
- The `/scry-plan` skill's `--step` contract is unchanged (same envelopes), only slower at the
  finalize step; verify the web poller and skill tolerate the longer step.

## 7. Testing

- Update mode tests: bare `scry` and any leftover config `mode` value all resolve to research;
  `--mode` is gone (passing it is a parse error); a stale `mode` key in config is ignored.
- New `plan_run` orchestrator: interview transcript → research (stubbed) → drafters receive the
  research synthesis (assert the drafter prompt contains research output, not the bare
  transcript) → fuse → files written.
- `--step` headless plan: full interview loop then finalize now includes a research phase;
  assert envelopes unchanged and progress is relayed via `log`.
- Flag cleanup: tri-state flags parse all three values; the removed spellings (`--no-map`/
  `--no-repo`/`--no-out`/`--max-rounds`/`--force`) now error; the verb-specific names
  (`--interview-rounds`/`--hard-cap`/`--overwrite`/`--allow-downgrade`) work.
- Diagnostics: a research segment appears in the plan diagnostics; `wall_seconds` spans
  interview + research + draft/fuse.
- Web: `/api/status` no longer exposes `mode`; the two-button surface drives the right pipeline;
  removed fusion/synthesize option is gone from options payloads.
- Run the full hermetic suite (`python3 -m unittest discover -s tests`).

### 7.1 Docs, README & demo gif (part of the e2e work)

Treat this as in-scope for the same e2e pass, not a follow-up. Use full power; cost is not a
constraint.

- Rewrite the README and any docs around the new two-command surface (`scry "X"` +
  `scry plan "X"`); remove every reference to modes and to the removed flags.
- Regenerate the demo gif against the new flow.
- **Messaging:** keep the framing that you don't *need* API keys to start scrying, while making
  clear scry fully **supports** API keys — DeepSeek and GLM are essential panel members. Remove
  any wording that implies scry doesn't support API keys or that the panel should shrink to a
  zero-key set.

## 8. Risks

- **Cost/latency of plan** roughly 2–4×'s vs today (full research loop prepended). Accepted per
  decision; mitigated only by progress visibility.
- **Long blocking `--step` finalize** could read as "hung" if `log` relay isn't wired through
  the skill/web cleanly — primary thing to verify in implementation.
- **Diagnostics drift** — if the research segment isn't added, bottleneck attribution is wrong.
- **PLAN_DRAFTER_SYSTEM framing** — must shift from "investigate and plan" to "plan from the
  provided research" or the research phase is wasted.
- **Hard removal (no aliases)** — deleting `--mode`/old flags outright will break any existing
  script or muscle-memory that uses them; acceptable per the no-backward-compat decision (§6),
  but the README/docs rewrite (§7.1) must land in the same change so users aren't stranded.

## 9. Open questions (to resolve during planning)

- ~~Exact `--depth`/`--hard-cap` defaults for plan's research phase~~ — **Resolved:** plan reuses
  the standard research defaults (`research.max_rounds`=3, `research.hard_cap`=5) and exposes no
  separate knob for them. The only round count a user tunes for plan is `--interview-rounds`
  (§5.4).
- Should `render_plan_prompt_from_research` include the full evidence/rounds, or just the fused
  `final` + `analysis`, to keep the drafter prompt from ballooning?

## 10. Follow-ups (separate work)

- `scry setup` merging `init` + `--check`.
