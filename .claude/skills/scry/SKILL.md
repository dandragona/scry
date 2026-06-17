---
name: scry
description: Consult scry — fan one prompt out to every AI model the user pays for (Claude, Codex, Gemini, Kimi, DeepSeek), have a judge model compare the answers, and return one fused, consensus answer. Use when the user types /scry, asks to "scry" a question, or wants a multi-model / second-opinion / fused answer to something hard. Wraps the local `scry` CLI and runs it headlessly with the orb animation OFF to stay token-cheap.
license: MIT
metadata:
  author: scry
  homepage: https://github.com/dandragona/scry
---

`/scry <prompt>` asks the user's whole panel of subscription models the same question in
parallel, has a judge model compare their answers, then synthesizes one fused answer. This
skill is a thin wrapper around the local `scry` CLI — your job is to invoke it correctly and
relay the result.

## How to run it

Pass the user's prompt to `scry` over **stdin** (a quoted heredoc sidesteps every
shell-quoting pitfall), and **always** disable the animation and progress chatter:

```bash
scry --no-anim --quiet <<'SCRY_PROMPT'
<the user's question, verbatim>
SCRY_PROMPT
```

- **`--no-anim` is mandatory from this skill — never omit it.** The "scrying orb" progress
  animation repaints itself many times per second by emitting cursor-up + redraw escape
  sequences. Captured into a tool result, that is hundreds of lines of terminal-control
  garbage that burn tokens for zero value. `--no-anim` falls back to plain text.
- **`--quiet`** drops the remaining stderr progress (per-model ✓/✗ lines, the consensus map,
  the cost tally) so the tool output is just the one fused answer on stdout. Keep it on by
  default for the same token reason.
- **Use a generous Bash timeout — at least `300000` ms (5 min).** A real panel run drives
  several model CLIs and takes ~30–90 s (longer with web search), well past the 2-min default.

Then relay scry's stdout (the fused answer) to the user. Don't reprint the prompt, and don't
add a progress spinner of your own.

## When the user wants more than the bare answer

Each variant still keeps `--no-anim`; only drop `--quiet` when you actually need the extra stderr.

- **Consensus map** — where the models agreed, contradicted, and what they all missed. Drop
  `--quiet`, add `--map`: `scry --no-anim --map <<'P' … P`. Use when the user asks "where do
  they disagree?".
- **Structured output** for you to parse — add `--json` (it already implies quiet; the orb is
  off too). Returns `{status, responses, analysis, final}`.
- **No web search** (faster / cheaper) — add `--no-web`.
- **Choose the models** — `--panel 'claude:opus,codex,kimi'`, `--judge 'claude:opus'`,
  `--aggregator 'claude:opus'`.
- **Lighter 2-stage run** (skip the judge) — `--mode synthesize`.
- **See each model's raw answer** — `--show-proposers` (prints to stderr, so drop `--quiet`).

Run `scry --help` for the full flag list.

## Notes

- **This makes real model calls that bill against the user's subscriptions.** The user
  invoking `/scry` *is* the authorization — just run it; don't ask again.
- **`scry: command not found`** → scry isn't installed. Point them at:
  `curl -fsSL https://raw.githubusercontent.com/dandragona/scry/main/install.sh | sh`
- **A run fails** (a provider not logged in, etc.) → surface scry's error and suggest
  `scry --check`, which verifies every provider CLI is installed and logged in **without
  making any paid calls**.
- **`/scry` with no prompt** → ask what they want to scry, or offer to scry the question
  currently in context.
