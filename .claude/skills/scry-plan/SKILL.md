---
name: scry-plan
description: Run scry's full panel-driven planning mode from inside Claude Code. Fans a planning request out to every AI model the user pays for, has them interview the user with clarifying questions (relayed to the user as native question cards), then drafts and fuses one repo-grounded, structured implementation plan. Use when the user types /scry-plan, asks to "plan X with scry", or wants the full multi-model scry planning interview without leaving Claude Code. Wraps the local `scry plan --step` headless protocol.
license: MIT
metadata:
  author: scry
  homepage: https://github.com/dandragona/scry
---

`/scry-plan <request>` runs scry's interactive planning mode, but with **you** (Claude) as
the interview UI: each round the panel proposes clarifying questions, you relay them to the
user as `AskUserQuestion` cards, feed their answers back, and loop until the panel is
confident — then scry drafts and fuses one structured Markdown plan grounded in the repo.

You drive it through scry's headless **`--step` protocol**: each `scry plan … --step --json`
call reads an optional answers payload on stdin and prints exactly one JSON envelope. State
is carried between calls by scry itself (resume checkpoints) — you just pass the `id` back.

## The loop

1. **Start** — in the project directory (so the panel reads the repo), with `</dev/null` so
   it doesn't read stray stdin:
   ```bash
   scry plan "<the user's request, verbatim>" --step --json --no-anim </dev/null
   ```
2. **Read the JSON envelope** on stdout and branch on `status`:
   - **`questions`** → relay `questions` to the user (see *Asking* below), collect answers,
     then call the next step with the answers payload on stdin:
     ```bash
     scry plan --resume=<id> --step --json --no-anim <<'SCRY_ANSWERS'
     {"answers":[{"q":"<question text, verbatim>","a":"<user's answer>"}]}
     SCRY_ANSWERS
     ```
     Repeat from step 2 with the new envelope.
   - **`ready`** → the panel is confident. Draft the plan:
     ```bash
     scry plan --resume=<id> --step --json --no-anim <<'SCRY_DONE'
     {"done":true}
     SCRY_DONE
     ```
   - **`done`** → finished. Relay `final` (the Markdown plan) to the user, and mention the
     written `plan_path` and `diagnostics_path`. Stop.
   - **`error`** → surface `error` to the user (see *Failures*). Stop.
3. **User wants to stop early** — if at any `questions` round the user says they're done /
   "just plan it now", send `{"done":true}` instead of more answers to draft immediately.

`id` is the same value every call after the first — read it from the first envelope and reuse it.

## Asking the questions (`status: questions`)

Each entry in `questions` is `{"q": "...", "why": "...", "options": ["...", ...]}` (`why`
and `options` may be absent). Relay them to the user:

- **With `options`** → use `AskUserQuestion`. Map each option string to a choice
  (`label` = the option text, trimmed to a few words; put any nuance in `description`).
  `AskUserQuestion` adds an "Other" free-text choice automatically. Put `why` in the
  question text or the option descriptions. Derive a short `header` (≤12 chars) from the topic.
- **Without `options`** (free-text) → `AskUserQuestion` requires ≥2 options, so instead just
  ask the question in plain chat and use the user's next reply as the answer.
- **Batch up to 4** option-questions per `AskUserQuestion` call; if a round has more, make
  several calls.
- Record each answer as `{"q": <the exact question text from scry>, "a": <the user's answer>}`.
  Match scry's `q` text verbatim so it lines up with the round.

Build the `answers` JSON carefully (valid JSON, quotes escaped). For long or multi-line
answers, write the payload to a temp file and redirect (`< /tmp/scry-answers.json`) instead
of a heredoc.

## Notes

- **`--no-anim` is mandatory on every call** — the scrying-orb animation emits hundreds of
  cursor/redraw escapes that burn tokens for nothing when captured into a tool result.
- **Run every call from the user's project directory** so the panel gets read-only repo
  access and the plan/diagnostics files land next to their code. Add `--no-repo` only if the
  user asks not to share repo contents with the panel.
- **Use a generous Bash timeout — at least `300000` ms (5 min) per call.** Each interview
  round consults the whole panel; the final `{"done":true}` draft is web-on and long-form
  (slower still).
- **This makes real model calls that bill the user's subscriptions.** The user invoking
  `/scry-plan` *is* the authorization — run it; don't ask again. (Each round and the draft
  are separate paid calls — that's expected.)
- **The plan + a human-readable diagnostics file are written by default** (paths returned in
  the `done` envelope). Pass `--no-out` on the `{"done":true}` call only if the user doesn't
  want files on disk.
- **`scry: command not found`** → scry isn't installed. Point them at:
  `curl -fsSL https://raw.githubusercontent.com/dandragona/scry/main/install.sh | sh`
- **Failures** (`status: error`, e.g. a provider not logged in) → surface scry's `error` and
  suggest `scry --check`, which verifies every provider CLI is installed and logged in
  **without making any paid calls**.
- **`/scry-plan` with no request** → ask what they want to plan, or offer to plan the task
  currently in context.
