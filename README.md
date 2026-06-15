# scry — gaze into every model you pay for, see one answer

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![ci](https://github.com/danielmandragona/scry/actions/workflows/ci.yml/badge.svg)](https://github.com/danielmandragona/scry/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![deps](https://img.shields.io/badge/dependencies-zero%20(stdlib)-brightgreen.svg)
![keys](https://img.shields.io/badge/API%20keys-none-brightgreen.svg)

**Local Mixture-of-Agents over the AI subscriptions you already pay for.** `scry` replicates
**OpenRouter's Fusion** capability *without* OpenRouter and *without* API keys: it drives the AI
CLIs you already pay for (Claude Code, OpenAI Codex, Google Antigravity) in headless mode, so
every call bills against your existing subscription. It fans a prompt out to several models —
each with web search — has one model deliberate over their answers, and synthesizes a single
better answer. One stdlib-only Python file; nothing to `pip install`.

```
                 ┌──────────► claude  (opus)  + web ─┐
   your prompt ──┤                                   ├─ PANEL  (parallel, web on)
                 └──────────► codex   (gpt)   + web ─┘
                                    │
                          ┌─────────▼─────────┐
                          │  JUDGE  (web on,    │   →  JSON: consensus, contradictions,
                          │  compares ≠ merges) │      partial_coverage, unique_insights,
                          └─────────┬─────────┘        blind_spots
                                    │
                          ┌─────────▼─────────┐
                          │  SYNTHESIS (no web) │   →  the single fused answer
                          └───────────────────┘
```

This mirrors how OpenRouter Fusion actually works (a productized **Mixture-of-Agents**:
parallel web-enabled panel → judge analysis → synthesis). Verified against OpenRouter's docs; the
synthesis prompt is the canonical MoA "Aggregate-and-Synthesize" template (arXiv:2406.04692).

## Fidelity to OpenRouter Fusion

Two things differ from real Fusion, by necessity:

1. **The panel models** — you choose them (that's the point).
2. **The internal prompts** — OpenRouter never published its panel/judge/synthesis prompts, so
   `scry` uses the canonical MoA aggregator prompt + a faithful reconstruction of the judge.

Everything else is matched:

| Fusion behavior | `scry` |
|---|---|
| Parallel panel | ✅ `asyncio` fan-out |
| `web_search` + `web_fetch` on panel **and** judge, **off** for synthesis | ✅ claude (`WebSearch`/`WebFetch`) + codex (`web_search=live`) + agy (Gemini grounding, on by default; can't be force-disabled) |
| Judge compares → 5-field JSON (consensus/contradictions/partial_coverage/unique_insights/blind_spots) | ✅ |
| Judge defaults to the outer (synthesis) model | ✅ |
| `max_tool_calls` cap (default 8) | ✅ claude (`--max-turns`); ⚠️ no codex equivalent |
| Reasoning effort | ✅ claude (`--effort`) + codex (`model_reasoning_effort`) |
| `{status, analysis, responses}` output + `failure_reason` enum | ✅ `--json` |
| Degraded success when judge fails (responses kept, analysis omitted) | ✅ |
| `temperature`, `max_completion_tokens` | ❌ no CLI equivalent (documented gap) |

## Requirements

- **Python 3.9+** (standard library only — no `pip install`).
- The CLIs you want in the panel, logged in to their subscriptions:
  - **Claude Code** (`claude`) — `/login` (Pro/Max). Proposer + judge + aggregator. Web via `WebSearch`/`WebFetch`.
  - **OpenAI Codex** (`codex`) — `codex login` (ChatGPT). Confirm with `codex login status`. Web via `web_search=live`.
  - **Google Antigravity** (`agy`) — Gemini panel member. Headless via `agy -p "<prompt>"`. Wired in by
    default (`Gemini 3.1 Pro (High)`). See "Adding Google" to change the model. **Live web search works**
    via Gemini grounding (on by default — verified).

## Install

One line (stdlib-only, so there's nothing to build):

```sh
curl -fsSL https://raw.githubusercontent.com/danielmandragona/scry/main/install.sh | sh
```

Or by hand — it's a single file:

```sh
chmod +x scry
ln -s "$PWD/scry" /usr/local/bin/scry   # or copy anywhere on PATH
```

Then confirm your model CLIs are installed and logged in (this spends nothing):

```sh
scry --check
```

## Usage

```sh
scry --check                                # pre-flight: are my CLIs installed + logged in?
scry "Explain why my Postgres query is slow and how to fix it"
cat prompt.txt | scry                       # prompt from stdin
scry --mode synthesize "..."                # lighter 2-stage (skip the judge)
scry --no-web "..."                         # pure generation, no web tools
scry --effort high "..."                    # raise reasoning effort on every stage
scry --json "..." > out.json                # {status, responses, analysis, final}
scry --show-proposers "..."                 # also print each model's raw answer (stderr)
scry --dry-run "..."                        # print the exact commands, run nothing
scry --no-anim "..."                        # plain progress (reduced motion); honors NO_COLOR too
```

### Flags

| Flag | Effect |
|---|---|
| `--mode fusion\|synthesize` | 3-stage (default) vs 2-stage |
| `--no-web` | disable web tools on panel + judge |
| `--effort low\|medium\|high\|xhigh\|max` | reasoning effort, every stage (where supported) |
| `--max-tool-calls N` | cap web tool iterations (Fusion default 8; claude only) |
| `--max-output-tokens N` | cap output tokens (claude only) |
| `--panel`, `--judge`, `--aggregator` | override models, e.g. `--panel "claude:opus,codex,gemini:pro"` |
| `--check` | verify each provider CLI is installed + logged in, then exit (no paid calls) |
| `--no-anim` | disable the scrying-orb animation (reduced motion); also auto-off under `NO_COLOR`/`TERM=dumb` |
| `--json`, `--show-proposers`, `--dry-run`, `--quiet` | output / debugging |

A panel member is `provider[:model]`. Same-model "self-pairing" still improves results — most of
Fusion's lift comes from the synthesis step, not model diversity.

## Configuration

`scry` runs with built-in defaults. To customize, drop a `config.json` in the working directory or
at `~/.config/scry/config.json` (or pass `--config path`). See the bundled [`config.json`](config.json).

- **`settings`** — the Fusion knobs (`web_tools`, `max_tool_calls`, `effort`, `max_output_tokens`).
- **`providers`** — how to drive each CLI: base `cmd`, `model_flag`, capture (`json`+`result_path`
  or `outfile`+`-o`), `system_flag`, `env_unset`, `timeout`, and a **`caps`** block mapping each
  Fusion knob to that CLI's flags (`web_on`/`web_off`, `tool_cap`, `effort`, `max_tokens_env`).
- **`panel`** — `{provider, model, label}` proposers. Defaults to OpenRouter's "Quality" preset
  shape (claude-opus + gpt + gemini-pro); ships with the first two installed.
- **`judge`**, **`aggregator`** — `{provider, model}` per stage (default `claude:opus`).

### Google (Antigravity / `agy`)

Google is wired in via the **Antigravity CLI** (`agy`), already logged into your Google subscription —
no API key. The default panel uses `Gemini 3.1 Pro (High)`, completing the 3-model Quality preset
(Claude Opus + GPT + Gemini Pro).

- **Pick a different Gemini model:** run `agy models` to list them (e.g. `Gemini 3.5 Flash (High)`,
  `Gemini 3.1 Pro (Low)`), then set the `model` on the `agy` panel member in `config.json` to the exact
  name, or per-run: `scry --panel "claude:opus,codex,agy:Gemini 3.5 Flash (High)" "..."`.
- **How it's driven:** `agy` takes the prompt as a CLI *argument* (`-p "<prompt>"`), not stdin, and
  prints plain text — so its provider record uses `"prompt": "arg"`, `"prompt_flag": "-p"`,
  `"capture": "text"`. (`scry` grew a small prompt-as-arg path for exactly this; stdin-based CLIs are
  unchanged.)
- **Web search:** works out of the box via Gemini's built-in grounding (**on by default** — verified:
  `agy` returns live, post-cutoff data with `vertexaisearch…/grounding-api-redirect` citations). There's
  no flag to force or disable it, so `--no-web` does *not* suppress agy's grounding (see Caveats).

## Robustness

- **Parallel** proposers, each with its own `timeout`.
- **Partial-failure tolerant** — a failed/timed-out proposer (including a bad model id, detected via
  `is_error`) is logged to stderr and excluded; the run continues if ≥1 proposer answers. If *all*
  fail you get a Fusion-style `failure_reason` (`all_panels_failed` / `rate_limited` /
  `insufficient_credits`) and exit 1.
- **Clean stdout** — progress on stderr; stdout is just the final answer (or `--json`).
- **No repo bleed-in** — proposers run in a throwaway temp cwd, so they answer as plain models.
- **Recursion guard** — `SCRY_DEPTH` is set in child env.

## Evals — does fusion actually beat the best single model?

Anecdotes ("the fused answer looked better") aren't evidence. `scry-eval` measures the
only claim that justifies fusion's ~4–5× cost: **is the fused answer at least as good as
the *best single model* in the panel?** (Beating the average model is easy and
worthless.) It runs the real `./scry --json` end-to-end over `eval/dataset.json` in two
regimes:

- **Objective** — items with a verifiable answer, scored by code against ground truth
  (no LLM judge → no circularity). Tests accuracy, and whether a wrong/web-less member
  drags fusion down.
- **Subjective** — open-ended items; the fused answer is compared to each solo answer by
  a **blind, order-swapped, family-neutral** judge: for a fused-vs-X matchup only a model
  whose family is neither the synthesizer's (Claude) nor X's may vote, which removes the
  obvious self-enhancement bias. Both A/B orderings run to cancel position bias.

```sh
./scry-eval                 # whole dataset, both regimes → eval/results.json
./scry-eval --only objective
./scry-eval --limit 1       # smoke test (first item of each regime)
```

### Pilot result (N=5 — validates the method + shows direction, NOT a powered result)

| Regime | Result |
|---|---|
| Objective (3 items) | claude, codex, gemini **and** FUSED each 3/3 → fused ≥ best single, but the items were too easy to differentiate (frontier models saturate them — no model erred, so nothing to fix). |
| Subjective (2 items) | Blind, family-neutral judges preferred the **fused** answer over the field **0.92** of the time, and over the *single toughest* solo **0.75–0.88**. |

**How to read it:** encouraging and genuinely bias-controlled (Claude never judged its
own synthesis; order swapped), but *not* conclusive — N is tiny, LLM judges can share
blind spots with the proposers, and a length/comprehensiveness confound likely inflates
the subjective number (synthesized answers are naturally more complete, which is partly
the point and partly what judges over-reward). To make a real claim: add harder objective
items where models actually disagree, grow N for a confidence interval, and add a
length-controlled judge pass.

### DRACO — the benchmark OpenRouter Fusion headlines

[DRACO](https://huggingface.co/datasets/perplexity-ai/draco) (Perplexity, MIT) is the
deep-research benchmark behind OpenRouter's Fusion headline (Fable 5 + GPT-5.5 = 69.0%). 100
tasks across 10 domains, each with a weighted rubric (~39 criteria; some weights **negative**, so
confident-but-wrong loses points). `scry-eval --draco` runs the real `./scry --json` per task and
grades the fused answer (and optionally each solo) with the **official** per-criterion MET/UNMET
method + normalization from the MIT [`rubric`](https://github.com/The-LLM-Data-Company/rubric)
scorer — its system prompt and `clamp(Σ MET·w / Σ max(0,w))` formula reproduced verbatim — driven by
a subscription-CLI grader. No paid API.

```sh
# one-time: fetch the dataset (MIT, ungated)
curl -sL https://huggingface.co/datasets/perplexity-ai/draco/resolve/main/test.jsonl \
  -o eval/draco-test.jsonl

./scry-eval --draco --draco-tasks 1                      # smoke: 1 task, fused only
./scry-eval --draco --draco-stratified --grade-solos     # 10 tasks (1/domain), fused vs each solo
./scry-eval --draco --grader "agy:Gemini 3.1 Pro (High)" # grade with Gemini (closest to DRACO's own judge)
```

Flags: `--draco-tasks N`, `--draco-stratified` (one task/domain), `--grade-solos`, `--grader`
(default `codex` — spares the metered Claude credit), `--criteria-limit K` (smoke only),
`--grader-concurrency`.

**What it measures vs. doesn't.** It scores *our* scry on DRACO's 0–100 scale and compares fused vs
best-solo on the **same** rubric. It does **not** reproduce OpenRouter's 69.0% — different model
builds (we run Opus 4.8 + Codex + Gemini), non-Exa retrieval, and a different judge. DRACO's own
paper notes absolute scores move ±10–25 pts by judge while *rankings* stay stable, so fused-vs-solo
stays meaningful.

**Two honest caveats.** (1) *Contamination* — DRACO tasks are web-research and models can find the
rubric online; OpenRouter blocked the hosting domains, but `scry` only has all-or-nothing `--no-web`.
(2) *Cost* — per task ≈ 1 scry run + ~39 grading calls (×each solo if `--grade-solos`). A 10-task
fused-only stratified run ≈ 10 scry runs + ~400 grading calls; grading all 4 outputs ≈ ~1,600.

**Smoke result:** 1 Academic task, graded 53/53 criteria → fused **84.8/100** (single task, huge
variance — this proves the harness, not a benchmark figure).

## Caveats

- **Anthropic billing (from 2026-06-15).** Programmatic `claude -p` usage now draws from a separate
  **monthly Agent SDK credit** (Pro $20 / Max 5× $100 / Max 20× $200), metered at API rates, no
  rollover; when it's exhausted, Claude calls stop unless you enable usage-credit overflow.
  Interactive Claude Code and claude.ai chat are unaffected. Because `scry` makes several `claude -p`
  calls per run (panel + judge + aggregator) and **web search costs extra**, watch this credit —
  move stages to `codex` (`--judge codex --aggregator codex`) or use Sonnet/`--no-web` to stretch it.
- **Provider ToS.** No vendor explicitly blesses parallel programmatic fan-out across one
  subscription. Personal/local use — your call.
- **Rate limits.** Parallel proposers + judge + aggregator multiply request volume; timeouts +
  partial-failure tolerance absorb transient limits.
- **Antigravity (`agy`).** Auth is the Antigravity app's own Google login (no key, nothing to unset).
  Headless `-p` prints plain text and folds any system prompt into the user text (no system-prompt
  flag). **Web search works via Gemini grounding, on by default** — but there's no flag to toggle it, so
  `--no-web` can't disable agy's grounding (the panel's other members do honor it). `--sandbox` is
  available if you want terminal restrictions. Default `--print-timeout` is 5m; `scry` sets 400s under a
  420s provider timeout.
- **`ANTHROPIC_API_KEY`** is unset for `claude` calls so it never silently bills the Console API.
  Don't run `claude` here under `--bare` (that path requires a key).
- **Cost** ≈ sum of every panel member + judge + synthesis call (like real Fusion), plus web-search
  tool usage.

## Out of scope

`temperature` / `max_completion_tokens` (no CLI equivalent), multi-layer MoA, streaming the final
answer token-by-token.
