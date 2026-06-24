# Deep Research mode (the new default `scry`)

**Date:** 2026-06-23
**Status:** Approved (design)
**Scope:** `scry` CLI — turn the bare `scry "<prompt>"` flow from a single-shot
Mixture-of-Agents (MoA) "Fusion" into an **iterative, gap-driven deep-research
pipeline** that uses the whole heterogeneous panel, still fuses to one prose answer,
and can optionally ground in a local repo. `scry plan` is unchanged. Single-shot
Fusion stays reachable as a fast escape hatch (`--mode fusion`).

## Motivation

Today the regular flow (`scry_run`, `scry:1061-1224`) is **single-shot**: the panel
fans out once → the judge compares (5-field JSON) → the aggregator fuses one answer.
There is no orchestrator-level loop that reads the panel's findings, identifies what's
missing, and searches again. That iterative *search → read → find gaps → search again*
loop is exactly what separates "deep research" from "ask five models once and merge".

The web research into SOTA deep-research systems (Anthropic's multi-agent research
system, OpenAI/Gemini/Perplexity Deep Research, GPT-Researcher/STORM/LangGraph Open
Deep Research) found they all implement one spine:

> **scope intent → plan/decompose → parallel search in isolated contexts → reflect &
> name the gaps → loop on the gaps → synthesize a written answer**, with a hybrid stop
> (no-new-gaps signal + hard cap), diminishing returns at ~3–5 rounds, workers returning
> *condensed* learnings, and the writer a *separate* role from the researchers.

scry's structural advantage is that everyone else clones **one** model into identical
subagents, whereas scry already has **five different frontier models**, a **non-candidate
judge**, and an **aggregator**. So scry already owns the three pieces those frameworks
build laboriously — parallel heterogeneous generators (coverage), a judge that is a
*different* model from the candidates (verification, avoids self-preference bias), and a
synthesizer. The judge's existing output (`consensus / contradictions / partial_coverage
/ unique_insights / blind_spots`, `JUDGE_SYSTEM` at `scry:72`) is **already a
reflection-and-gap detector**. It just isn't wired into a loop.

Two evidence-backed guardrails shaped the design: (1) **do not build symmetric
multi-round debate** — it underperforms self-consistency (conformity / sycophancy /
problem-drift); (2) **factuality comes from verification, not voting** — keep the judge a
different model from the candidates (scry already does this).

The upgrade is therefore *small in surface area, high in leverage*: a brief/plan step, a
bounded gap-driven loop wrapping the existing panel→judge block, and a citation-merge in
synthesis — all reusing the hooks `scry plan` already proved (`panel_system`,
`aggregator_system`, `run_overlay`, `cwd`, the `phases` mechanism).

## Goals

- Make **deep research the default** behavior of bare `scry "<prompt>"` (`mode:
  "research"`). It still fuses to **one prose answer** (the user's chosen output format).
- Implement the **consensus deep-research workflow**, adapted to scry's heterogeneous
  panel:
  1. **Clarify** — a clarifying-question interview before research (reuse `scry plan`'s
     machinery), **always on** in interactive runs.
  2. **Brief** — the judge model turns the prompt (+ clarifying answers + optional repo
     context) into a normalized research brief + 3–6 sub-questions.
  3. **Round 1 — diversity fusion** — all panelists answer the *full* brief (pure MoA),
     web-on, returning **condensed, sourced findings**.
  4. **Reflect** — the (research-tuned) judge emits the 5 fields **plus** targeted
     `open_questions`, each tagged with whether it needs live web.
  5. **Round 2+ — gap-targeted, capability-routed** — re-fan **only the open gaps** to
     **only the capable models**: live-web gaps → claude/codex/kimi (+ Gemini's always-on
     grounding); reasoning gaps → all (incl. DeepSeek).
  6. **Stop** — **adaptive with a generous cap**: default `max_rounds: 3`, early-exit
     when the judge reports no significant new gaps, hard cap `5`. No auto-downgrade of
     "simple" queries (the user wants best quality regardless of cost).
  7. **Synthesize** — the aggregator writes one fused prose answer from the
     **judge-compressed** findings (never the raw transcripts), with a **citation-merge**
     across web-grounded panelists so the answer carries one clean source list.
- **Optional repo grounding** ("both, auto-detect/selectable"): default external-world;
  `--repo [PATH]` (or `research.repo_context: "auto"` detecting a surrounding git repo)
  grounds search-capable panelists in the read-only repo, reusing plan's `cwd`-is-repo
  path (`_provider_repo_safe` at `scry:769`).
- Keep single-shot Fusion available as a fast path: `--mode fusion` (today's behavior)
  and `--mode synthesize` (panel→synth) are **unchanged**.
- Respect panel heterogeneity (controllable web on claude/codex/kimi; DeepSeek
  knowledge-only; Gemini always-grounded) — never route a live-web gap to a no-web model.

## Non-goals

- **Symmetric multi-round debate** between models. Rejected on evidence (underperforms
  self-consistency). Rounds are *asymmetric*: generate → judge-reflect → re-generate on
  gaps. Models never see each other's raw answers to "argue"; only the judge diffs them.
- **A heavy cited report** (sections / confidence tables / appendices). The user chose a
  fused prose answer; citations are woven in / listed, not a structured report. (A
  `--report` format could be added later; out of scope.)
- **RL-trained / end-to-end agentic search.** scry orchestrates closed third-party CLIs;
  all intelligence lives in orchestration + judge prompts (the Gemini/Perplexity
  prompted-scaffold path), not in training.
- **Per-phase *model* selection** (a provider still uses one model across phases — same
  non-goal as the per-provider-top-model design).
- **Headless `--step` research protocol + a `/scry-research` skill.** Deferred follow-up.
  v1 ships interactive deep research + `--json`. (Clarify is interactive-only in v1; in
  non-interactive / `--json` runs it auto-skips, mirroring `do_plan` vs `do_plan_step`.)
- **Fixing the `claude -p` env-bias leak** (inheriting `~/.claude` hooks/CLAUDE.md/MCP).
  Tracked separately; flagged below as a risk that *compounds* across rounds.

## Design

### Command surface

- `cfg["mode"]` default becomes **`"research"`** (`DEFAULT_CONFIG`, `scry:207`).
- `--mode` choices become `{research, fusion, synthesize}` (`scry:3080`); bare `scry
  "<prompt>"` → research; `--mode fusion` → today's single-shot 3-stage; `--mode
  synthesize` → panel→synth.
- New flags on the single argparse parser (`scry:3080-3139`):
  - `--depth N` — target research rounds (overrides `research.max_rounds`).
  - `--max-rounds N` — hard cap (overrides `research.hard_cap`).
  - `--no-clarify` — skip the clarifying interview.
  - `--repo [PATH]` — ground in a read-only repo (no arg = cwd); `--no-repo` forces
    external-world.
- Dispatch: `research` is the **fall-through default** in `main()` (`scry:3193-3309`),
  the slot Fusion occupies today. `init` / `update` / `plan` pseudo-subcommands
  (`scry:3148-3191`) and `last` / `log` (`scry:3064-3070`) are unchanged.

### Config surface

New top-level `research` block in `DEFAULT_CONFIG` (mirrors the documented `plan` block),
plus new `phases`:

```jsonc
"mode": "research",                  // was "fusion"
"research": {
  "clarify": true,                   // clarifying interview on by default
  "max_rounds": 3,                   // target depth (adaptive)
  "hard_cap": 5,                     // absolute ceiling
  "early_exit": true,                // stop when judge finds no significant new gaps
  "sub_questions": 6,                // brief decomposition target (3-6)
  "repo_context": "auto"             // "auto" | "off" ; --repo/--no-repo override
},
"phases": {
  "interview":  { "web_tools": false },                  // reused for clarify (exists)
  "brief":      { "web_tools": false },                  // NEW: planner, cheap, judge model
  "research":   { "web_tools": true, "timeout": 2100 },  // NEW: round fan-out, web-on, high budget
  "reflect":    { "web_tools": true },                   // NEW: research judge (gap-finder)
  "synthesis":  { "web_tools": false }                   // unchanged (keeps streaming)
}
```

Because `load_config` is a **shallow** merge (`scry:413-431`), a user with a custom
`phases`/`research` block must add these keys themselves; the repo `config.json`
reference mirror is updated to show them.

### New prompts (test-anchor-safe)

All live in the prompt block (`scry:52-169`). The test harness routes stubbed CLI calls
by unique substrings in each system prompt (`"impartial judge"`, `"scope a task"`,
`"deduplicating"`, `"plan drafts"`), so each new prompt **must carry a distinct anchor
phrase**, and the new ones are added to the stub router.

- `RESEARCH_BRIEF_SYSTEM` (anchor: **"research brief"**) — judge model. Input: prompt +
  clarifying answers + optional repo summary. Output: JSON `{intent, sub_questions:
  [..3-6..]}`. The prompt-rewriting / north-star step.
- `RESEARCH_PANEL_SYSTEM` (anchor: **"deep research analyst"**) — panel proposers.
  Instruction: investigate thoroughly via web (and repo files when `cwd` is a repo);
  return a **condensed, sourced findings brief** (claims + the URLs/paths that support
  them), *not* a raw dump. The "intelligent filter" rule that keeps judge/aggregator
  context clean.
- `RESEARCH_JUDGE_SYSTEM` (anchor: **"research referee"** — distinct from the fusion
  judge's "impartial judge") — extends the 5-field analysis with
  `open_questions: [{ "question": str, "needs_web": bool }]`. `needs_web` drives routing.
- `RESEARCH_SYNTH_SYSTEM` (anchor: **"research synthesis"**) — aggregator. Fuse the
  accumulated condensed findings + judge analyses into **one prose answer**; merge and
  dedup the cited sources into a single list; weave key citations inline. No raw
  transcripts.

### Orchestration

Refactor the three stages inside `scry_run` (`scry:1094-1207`) into small internal
helpers so both the single-shot and looped paths share them (keeps blast radius low and
existing Fusion behavior byte-for-byte):

- `_run_panel(cfg, members, prompt, web, settings, system, cwd, log) -> responses`
  (the `run_one` + `asyncio.gather` body, `scry:1101-1122`).
- `_run_judge(cfg, judge_system, query, responses, web, settings, log) -> analysis`
  (the judge body, `scry:1135-1152`).
- `_run_synth(cfg, agg_system, query, responses, analysis, ...) -> final`
  (the synthesis body incl. stream/buffered + citation note, `scry:1154-1207`).

`scry_run` (fusion / synthesize) becomes a thin composition of these — unchanged
behavior. Add a new orchestrator:

```python
async def research_run(cfg, prompt, settings, log, stream_final, stop_orb,
                       cwd=None, cli_overrides=None):
    # 0. clarify (interactive only) -> answers      [reuse gather_questions/dedup_questions]
    # 1. brief = _run_judge-style call with RESEARCH_BRIEF_SYSTEM (phase "brief")
    brief = await _make_brief(cfg, prompt, answers, repo_summary, settings, log)
    evidence, analysis, rounds = [], None, []
    members = cfg["panel"]
    target = cli_depth or cfg["research"]["max_rounds"]                     # adaptive target
    cap    = cli_max_rounds or cfg["research"]["hard_cap"]                  # absolute ceiling
    for i in range(cap):
        if i == 0:
            tasks = [(m, brief_prompt(brief)) for m in members]            # all 5, full brief
        else:
            tasks = _route_gaps(analysis["open_questions"], members)       # gaps -> capable models
            if not tasks: break                                           # nothing to chase
        responses = await _run_panel(... RESEARCH_PANEL_SYSTEM ..., phase="research", web=True, cwd=cwd)
        evidence.extend(responses)   # panelists self-condense; no separate summarizer pass
        analysis = await _run_judge(... RESEARCH_JUDGE_SYSTEM ..., evidence, phase="reflect", web=True)
        rounds.append({"responses": responses, "analysis": analysis})
        if i + 1 >= target and not _research_should_continue(analysis, i, target, cap):
            break
    final = await _run_synth(... RESEARCH_SYNTH_SYSTEM ..., query=brief.intent,
                             responses=evidence, analysis=analysis, stream=stream_final)
    return { ...result..., "brief": brief, "rounds": rounds, "final": final }
```

Two pure helpers (easy to unit-test in isolation):

- `_research_should_continue(analysis, round_idx, target, cap) -> bool` — `False` if
  `round_idx + 1 >= cap`; else `True` while there are unresolved `contradictions`,
  non-empty `partial_coverage`, new `blind_spots`, or `open_questions`. With
  `early_exit`, the loop stops once `target` is reached **and** this returns `False`
  (the "<~2 new sub-questions" convergence rule).
- `_route_gaps(open_questions, panel) -> [(member, prompt)]` — for each open question,
  pick members by capability: `needs_web=True` → web-controllable members
  (claude/codex/kimi) plus Gemini (always grounded); `needs_web=False` → all members
  (incl. DeepSeek). Never sends a live-web gap to a no-web model (the Self-MoA
  weak-proposer trap). Web capability is read from each provider's `caps` /
  known-no-web set, *not* hard-coded by label.

Per-round latency is the **slowest panelist** (synchronous `asyncio.gather`); acceptable
for a small fixed panel, but it's why the default cap is low and `phases.research.timeout`
is generous (2100s), with `SCRY_NO_TIMEOUT` / negative `--timeout` (`_effective_timeout`,
`scry:729-747`) available for truly unbounded runs.

### Repo grounding

- `research.repo_context: "auto"` → if invoked inside a git repo, pass that repo as
  read-only `cwd` (the plan `--repo` path). `--repo PATH` overrides; `--no-repo` /
  `"off"` forces the scrubbed throwaway cwd (today's external-world default).
- Reuse `_provider_repo_safe` (`scry:769`) — read-only-safe providers read the repo;
  unsafe agy is still exiled to a throwaway temp cwd (`scry:818-821`), losing repo
  grounding. Known limitation, carried over from plan; noted in `--check`/dry-run output.

### Error handling & robustness

- **Per-panelist isolation** (`scry:1114`) is kept: one failed proposer is recorded
  `ok:False` and skipped; only an all-fail round raises `AllPanelsFailed`.
- **Light retry for research panel calls** (new, research-only): one retry with short
  backoff on a *transient* `ProviderError` (timeout / empty), not on a model-error. Deep
  research multiplies a single panelist across rounds, so silently dropping a voice every
  round is too costly. (Fusion stays no-retry.)
- **Judge is best-effort** (`scry:1145-1152`): if `RESEARCH_JUDGE_SYSTEM` output won't
  parse, log and treat as "no further gaps" → the loop ends and synthesis proceeds with
  the evidence gathered so far. Research never hard-fails on a bad judge round.

### Output shape

Result dict extends today's (`scry:1210-1221`) with `brief` and `rounds:
[{responses, analysis}]`; `final` stays the fused prose. Default stdout prints `final`;
`--json` emits the full trace (brief + per-round responses/analyses + final + cost);
`--show-proposers` prints each round's proposers. `cost`/`summarize_cost` (`scry:653`)
accumulates across all rounds (claude/deepseek metered only).

## Testing

Mirror `tests/test_plan.py`, `tests/test_scry_run.py`, `tests/test_pipeline_helpers.py`;
run hermetically with `python3 -m unittest discover -s tests`. TDD: failing test first.

- **Pure helpers:** `_research_should_continue` (continue on gaps; stop at hard cap; stop
  after target when no gaps) and `_route_gaps` (web gaps exclude DeepSeek; reasoning gaps
  include all; Gemini always included for web gaps).
- **Brief parsing:** `RESEARCH_BRIEF_SYSTEM` output → `{intent, sub_questions}`; tolerant
  of extra prose around the JSON (reuse `tolerant_json`, `scry:584`).
- **Loop behavior (stubbed CLIs):** early-exit when the stub judge returns no gaps;
  runs to `hard_cap` when it always returns gaps; round-2 fan-out targets only the routed
  subset; evidence accumulates across rounds; a transient panel failure triggers exactly
  one retry then proceeds.
- **Mode dispatch:** bare `scry "q"` → research; `--mode fusion` → unchanged single-shot
  (existing Fusion tests stay green); `--mode synthesize` unchanged.
- **Harness anchors:** extend the stub router with the four new anchor phrases
  (`research brief`, `deep research analyst`, `research referee`, `research synthesis`);
  assert no collision with existing anchors.
- **End-to-end (`--json`, stubbed):** a 2-round research run produces `brief`, two
  `rounds`, and a fused `final`; `--no-clarify` skips the interview; `--repo` sets cwd.

## Risks / notes

- **Token/time cost (~15× chat, per Anthropic).** Five heterogeneous CLIs × multiple
  rounds on *subscription quotas* is the dominant cost. Structural mitigations are baked
  in: gap-targeted re-fan (only open gaps, only capable models), low default cap,
  judge-*compressed* (not raw) context into synthesis. The contradiction-resolution
  payoff concentrates in round 2 — that's why `max_rounds` defaults to 3, not higher.
- **Slow-CLI timeout / wall-clock multiplication.** Each round waits on the slowest
  panelist; rounds multiply it. Mitigated by the generous `phases.research` timeout, the
  `SCRY_NO_TIMEOUT` hatch, the low cap, and per-panelist isolation (a straggler that
  times out is dropped, not fatal).
- **Heterogeneous web capability.** DeepSeek has no web; Gemini's grounding is always-on
  and uncontrollable; only claude/codex/kimi are controllable live searchers. The loop
  must read capability from config, not labels, and route accordingly.
- **Hallucination control = verification, not voting.** The guardrail is structural and
  already present: a judge that is a *different* model from the candidates, driving
  contradiction resolution and gap-filling each round. Do **not** rely on per-model
  self-reported confidence (browsing inflates overconfidence).
- **Env-bias leakage compounds.** `claude -p` inherits the full `~/.claude` env
  (hooks/CLAUDE.md/MCP), biasing drafts; across multiple research rounds this compounds.
  Out of scope here, but worth isolating before iteration multiplies its effect.
- **Shallow config merge.** Users with custom `phases`/`panel`/`research` blocks must add
  the new keys themselves (no deep backfill) — call this out in the README/CHANGELOG.
