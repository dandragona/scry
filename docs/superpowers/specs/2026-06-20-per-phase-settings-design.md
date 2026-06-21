# Per-phase settings overrides

**Date:** 2026-06-20
**Status:** Approved (design)
**Scope:** `scry` CLI — replace the plan final-draft *scaling* knobs with explicit,
per-phase configuration that every pipeline stage can use.

## Motivation

Today the plan final draft gets a bigger budget by *multiplying* the base:
`max_tool_calls 8 × final_tool_call_scale 3 = 24` turns, `timeout 420 × final_timeout_scale 5 = 2100s`.
The multipliers are opaque (`24`/`2100` are derived products, not values you can read),
and the only stage you can meaningfully tune is the final draft. There is no way to,
say, run the **judge** with web off or fewer tool calls.

This design removes scaling entirely and lets **each pipeline phase declare the exact
values it wants**, inheriting global defaults for anything it doesn't override.

## Goals

- Each phase can override `web_tools`, `max_tool_calls`, `timeout`, `effort`, and
  `max_output_tokens`.
- Replace `final_tool_call_scale` / `final_timeout_scale` with explicit absolute values.
- Shipped defaults **reproduce today's behavior exactly** — a representation change, not
  a behavior change.
- Keep the config DRY: a phase only declares what differs from the global `settings`.

## Non-goals

- Per-proposer (per-panel-member) overrides. Panel is configured as one group.
- New behavior for normal runs out of the box (defaults are unchanged).
- A CLI flag per phase. CLI flags stay global (they overlay every phase — see Precedence).

## Config shape

`settings` holds the global defaults. A new sibling top-level `phases` block holds a
partial override per phase. `load_config` backfills `phases` from defaults exactly like
it already does for `settings` and `plan`.

```jsonc
{
  "settings": {
    "web_tools": true,
    "max_tool_calls": 8,
    "timeout": 420,
    "effort": null,
    "max_output_tokens": null,
    "save_history": true
  },
  "phases": {
    "panel":     {},                                  // inherits all globals (web on, 8 turns, 420s)
    "judge":     {},                                  // e.g. { "web_tools": false, "max_tool_calls": 4 }
    "synthesis": { "web_tools": false },              // Fusion: the fused answer uses no web
    "interview": { "web_tools": false },              // plan clarifying rounds: web-off, fail-fast
    "final":     { "max_tool_calls": 24, "timeout": 2100 }  // plan deep draft budget
  }
}
```

`save_history` is global only (not a per-phase concept).

## Phase taxonomy

There are four call sites that consume settings, plus one overlay:

| Phase       | Where it applies                                              | Default override        |
|-------------|--------------------------------------------------------------|-------------------------|
| `panel`     | each proposer in a `scry_run`                                 | — (inherits globals)    |
| `judge`     | the judge call in a `scry_run` (fusion mode)                 | — (inherits globals)    |
| `synthesis` | the fused-answer call in a `scry_run`                         | `web_tools: false`      |
| `interview` | the `gather_questions` / `dedup_questions` calls (plan)       | `web_tools: false`      |
| `final`     | **overlay** applied across the plan final-draft `scry_run`'s panel/judge/synthesis stages | `max_tool_calls: 24, timeout: 2100` |

`final` is not a peer stage: the plan final draft is itself a full
`scry_run("fusion", …)` (panel → judge → synthesis) on the enriched request. Its
elevated budget is expressed as a per-run *overlay* layered on top of those three
stages' phase configs for that one run.

## Resolution and precedence

A stage's effective settings are a shallow merge, lowest priority first:

```
built-in DEFAULT_SETTINGS  →  config settings  →  phases[stage]  →  run_overlay (phases.final, plan draft only)  →  explicit CLI flags
```

i.e. **CLI flag > run overlay (`final`) > phase override > global setting > built-in default.**

- `web_tools` is read from the resolved settings (`eff["web_tools"]`), replacing both the
  global `web` flag threaded through `scry_run` and the hardcoded `web=False` for synthesis.
- `timeout` is read directly from the resolved settings — no scaling.

### Helper

```python
def _phase_settings(settings, phases, phase, run_overlay=None, cli_overrides=None):
    """Effective settings for one phase: a base `settings` dict, then the phase
    override, then an optional per-run overlay (phases.final for the plan draft), then
    explicit CLI flags. Later wins."""
    return {**settings,
            **(phases or {}).get(phase, {}),
            **(run_overlay or {}),
            **(cli_overrides or {})}
```

It takes the *base* `settings` (not `cfg`) because `scry_run` already receives a
`settings` dict as a parameter — tests and the plan draft pass their own. `cli_overrides`
contains only the flags the user explicitly passed (so unset flags never clobber a phase
value).

## Code changes (`scry`)

1. **Constants.** Add `DEFAULT_PHASES` (the table above) and reference it in
   `DEFAULT_CONFIG["phases"]`.
2. **`load_config`.** Backfill `cfg["phases"]` from `DEFAULT_CONFIG["phases"]`, merging
   per-phase (`{**default_phase, **user_phase}` for each known phase) so a partial phase
   override keeps the other phase defaults — mirroring the existing `settings`/`plan` backfill.
3. **`_phase_settings`** helper (above).
4. **`scry_run`.** Keep the existing `(cfg, prompt, mode, settings, log, …)` signature —
   `settings` stays as the base (10 tests + the main caller pass it positionally). Add two
   optional kwargs: `cli_overrides=None`, `run_overlay=None`. Resolve per stage from the
   base `settings` + `cfg["phases"]`: `panel_eff = _phase_settings(settings, cfg.get("phases",{}), "panel", run_overlay, cli_overrides)`,
   likewise `judge`, `synthesis`. Pass each `eff` and `eff["web_tools"]` into `call_cli`.
   Remove the global `web` flag and the hardcoded synthesis `web=False`. `call_cli`'s own
   signature is unchanged (still takes a `settings` dict + a `web` bool).
5. **Plan interview.** Resolve `interview_eff = _phase_settings(settings, cfg.get("phases",{}), "interview", None, cli)`
   and pass it as the `settings` arg to `gather_questions` / `dedup_questions`, replacing the
   hand-built `isettings` (and `plan.interview_web`).
6. **Plan final draft.** Call `scry_run(cfg, …, settings, …, run_overlay=cfg["phases"]["final"], cli_overrides=cli)`.
   Delete `_final_draft_settings`.
7. **Timeout.** Simplify `_effective_timeout` to `float(settings.get("timeout") or DEFAULT_TIMEOUT)`
   (single arg). Remove `timeout_scale` and the per-provider `timeout` override added earlier.
   `call_cli` and `stream_call` call the simplified helper with their resolved phase settings.
8. **`main`.** Stop mutating a `settings` copy with CLI values. Instead build
   `cli_overrides` containing only explicitly-passed flags (`--no-web` → `web_tools:false`,
   `--effort`, `--max-tool-calls`, `--max-output-tokens`, and a new optional `--timeout`).
   Thread `cli_overrides` into `scry_run` / `do_plan` / `do_plan_step`. `save_history` is
   still read from `cfg["settings"]`.
9. **Diagnostics.** `render_plan_diagnostics` prints the resolved per-phase settings
   (a short web/turns/timeout table per phase) instead of the removed scale knobs.

## Removed knobs (backward compat)

Removed cleanly from code, `config.json`, and `DEFAULT_CONFIG`:
`plan.final_tool_call_scale`, `plan.final_timeout_scale`, the `timeout_scale` setting,
`plan.interview_web`, and the per-provider `timeout` override. These keys, if present in
an existing user config, are silently ignored. Because `DEFAULT_PHASES` reproduces the
prior behavior, no current user sees a behavior change. (Decision: remove cleanly rather
than honor deprecated fallbacks — single-author project, defaults preserve behavior.)

`plan` retains its non-settings knobs: `max_rounds`, `repo_context`.

## Behavior-preservation check

| Stage (default config)         | Today                         | After (resolved)              |
|--------------------------------|-------------------------------|-------------------------------|
| panel (normal run)             | web on, 8 turns, 420s         | web on, 8 turns, 420s         |
| judge (normal run)             | web on, 8 turns, 420s         | web on, 8 turns, 420s         |
| synthesis (normal run)         | web off, 8 turns, 420s        | web off, 8 turns, 420s        |
| interview (plan)               | web off, 8 turns, 420s        | web off, 8 turns, 420s        |
| final draft panel/judge (plan) | web on, 24 turns, 2100s       | web on, 24 turns, 2100s       |
| final draft synthesis (plan)   | web off, 24 turns, 2100s      | web off, 24 turns, 2100s      |

## Testing

- **`_phase_settings`** (new): inheritance from globals; phase override wins over global;
  run_overlay (`final`) wins over phase; CLI overlay wins over everything; unknown phase
  → globals; partial override keeps sibling phase defaults.
- **`load_config`**: `phases` backfilled from defaults; a partial user `phases` (e.g. only
  `judge`) keeps the other phase defaults; `phases` absent → full defaults.
- **`_effective_timeout`**: returns `settings["timeout"]`; falls back to `DEFAULT_TIMEOUT`
  when absent. (Replaces the scale tests; drop the per-provider-override test.)
- **Remove** `_final_draft_settings` tests in `test_plan.py`; replace with a test that the
  plan final draft resolves to the `final` budget (24 / 2100) over the panel/judge/synthesis
  stages.
- **`scry_run` wiring**: synthesis resolves web-off by default; a `phases.synthesis.web_tools:true`
  override turns it on; `--no-web` (CLI) forces web off on every phase including one that set it on.
- Full suite stays green; behavior-preservation table is the acceptance bar.

## Open question resolved

- Q "old knobs": **remove cleanly** (recommended; defaults preserve behavior). Flag on
  review to switch to deprecated-fallback handling instead.

## Docs to update

`README.md` (settings/providers/robustness sections + the Fusion fidelity notes referencing
per-provider timeout), `config.json` (add `phases`, drop per-provider `timeout` + scale notes),
`CHANGELOG.md` (new entry; supersede the just-added unified-timeout / scale entries).
