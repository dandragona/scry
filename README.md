# scry — gaze into every model you pay for, see one answer

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![ci](https://github.com/dandragona/scry/actions/workflows/ci.yml/badge.svg)](https://github.com/dandragona/scry/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![deps](https://img.shields.io/badge/dependencies-zero%20(stdlib)-brightgreen.svg)
![auth](https://img.shields.io/badge/auth-your%20AI%20subscriptions-brightgreen.svg)

**Local Mixture-of-Agents over the AI subscriptions you already pay for.** `scry` replicates
**OpenRouter's Fusion** capability *without* OpenRouter: it drives the AI CLIs you already
subscribe to (Claude Code, OpenAI Codex, Google Antigravity, Moonshot Kimi) in headless mode, so
every call bills against your existing subscription rather than a metered API. It fans a prompt
out to several models — each with web search — has one model deliberate over their answers, and
synthesizes a single better answer. One stdlib-only Python file; nothing to `pip install`.

<p align="center">
  <img src="docs/demo.gif" alt="scry in action: a panel of models race, a judge compares them, the fused answer streams in, then a consensus map of where they agreed and clashed" width="720">
</p>

<sub>A real run — a 5-model panel (claude · codex · gemini · kimi · deepseek): the orb gazes while the panel races, the fused answer streams in token-by-token, then a consensus map shows where the models agreed, contradicted, and what they all missed — capped by a live cost tally.</sub>

```
                 ┌──────────► claude  (opus)  + web ─┐
   your prompt ──┤                                   ├─ PANEL  (parallel, web on)
                 └──────────► codex   (gpt)   + web ─┘
                                    │
                          ┌─────────▼─────────┐
                          │  JUDGE  (no web,    │   →  JSON: consensus, contradictions,
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

## Quickstart

Zero → first answer, four commands:

```sh
curl -fsSL https://raw.githubusercontent.com/dandragona/scry/main/install.sh | sh   # install
scry init                                  # pick which model CLIs to fan out to → writes your panel
scry --check                               # pre-flight: are those CLIs installed + logged in? (free)
scry "why is my Postgres query slow, and how do I fix it?"   # ask — deep research by default
```

> **Tip:** bare `scry "..."` runs the deep-research loop (clarify → multi-round panel → fused report),
> which is thorough but slow and costs more. Want a fast single-shot answer instead?
> `scry --mode fusion "..."`.

If `~/.local/bin` isn't on your `PATH`, the installer prints the exact `export PATH=…` line to add.
See [Install](#install) for hand-install and [Uninstall](#uninstall) to remove it.

## Fidelity to OpenRouter Fusion

Two things differ from real Fusion, by necessity:

1. **The panel models** — you choose them (that's the point).
2. **The internal prompts** — OpenRouter never published its panel/judge/synthesis prompts, so
   `scry` uses the canonical MoA aggregator prompt + a faithful reconstruction of the judge.

Everything else is matched:

| Fusion behavior | `scry` |
|---|---|
| Parallel panel | ✅ `asyncio` fan-out |
| `web_search` + `web_fetch` on panel **and** judge, **off** for synthesis | ✅ claude (`WebSearch`/`WebFetch`) + codex (`web_search=live`) + kimi (`SearchWeb`/`FetchURL`, toggled via the generated agent file — honors `--no-web`) + agy (Gemini grounding, on by default; can't be force-disabled) |
| Judge compares → 5-field JSON (consensus/contradictions/partial_coverage/unique_insights/blind_spots) | ✅ |
| Judge defaults to the outer (synthesis) model | ✅ |
| `max_tool_calls` cap (Fusion default 8) | ⚠️ claude (`--max-turns`) when set; scry defaults to **uncapped** (bounded by `timeout`); no codex equivalent |
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
  - **Moonshot Kimi** (`kimi-cli`) — `kimi-cli login` (Kimi Code OAuth — reuses your membership, **no API key**).
    Headless via `kimi-cli --quiet`. Uses your account's default model (the Kimi Code membership exposes
    `kimi-for-coding`). Web via `SearchWeb`/`FetchURL`. See "Adding Moonshot" below.

> New? Run **`scry init`** — an interactive setup wizard that detects which of these CLIs you have and
> writes your default panel to the global `~/.config/scry/config.json`, so `scry` works from any
> directory. (Need a different panel for one project? `scry init --local` writes a `./scry.config.json`
> that overrides the global config when you run `scry` from there.)

## Install

One line (stdlib-only, so there's nothing to build):

```sh
curl -fsSL https://raw.githubusercontent.com/dandragona/scry/main/install.sh | sh
```

It installs into a **user-owned** directory — `~/.local/bin` by default, like `rustup`/`uv`/`pipx`
— so **no `sudo`** is ever needed to install, update, or run it. If that dir isn't on your `PATH`,
the installer prints the exact `export PATH=…` line to add (it never edits your shell files).
Pick a different location with `INSTALL_DIR=~/bin`.

Or by hand — it's a single file (keep it somewhere you own, e.g. `~/.local/bin`):

```sh
mkdir -p ~/.local/bin
install -m 755 scry ~/.local/bin/scry          # 755 so the interpreter can read it
install -m 755 scry-deepseek ~/.local/bin/      # only needed for the DeepSeek provider
install -m 755 scry-glm ~/.local/bin/           # only needed for the GLM (Z.ai) provider
cp -R scry_web ~/.local/bin/                     # only needed for the `scry web` UI
```

(The web UI is the only multi-file piece — copy the `scry_web/` package next to the
binary, or just use the one-line installer, which does it for you.)

> Avoid `sudo`-installing into `/usr/local/bin`: a root-owned scry then needs `sudo` to update,
> and (installed mode 711) can even be unreadable by your user. Keep it user-owned.

Then set up your panel and confirm your CLIs are logged in (both spend nothing):

```sh
scry init       # interactive: pick which CLIs to use → writes your default panel
scry --check    # pre-flight: are those CLIs installed + logged in?
```

### Updating

```sh
scry update     # the one command that keeps your whole install current
```

**`scry update` is the canonical way to update — you never need to reinstall.** It
downloads the newest `scry` from GitHub, verifies it's complete and valid (length,
entry point, and that it compiles), and **atomically** replaces your installed copy —
keeping it executable and forcing it world-readable (so an older, broken root-owned
install self-heals). It won't install a truncated download or an older version (pass
`--force` to downgrade). For a user-owned install (the default) no `sudo` is needed;
if scry lives in a system directory it prints the exact elevated command.

It then **refreshes the rest of the install in lockstep** (best-effort, from one repo
tarball): the API-key adapters (`scry-deepseek`, `scry-glm`), the web UI package
(`scry_web/`), and the `/scry` + `/scry-plan` Claude skills — but only the ones you
already have, so a pure-CLI install stays lean. Honors `SCRY_REPO` / `SCRY_REF` (shared
with the installer), `SCRY_UPDATE_URL` (a full single-file URL override for the core
self-update), and `SCRY_WEB_TARBALL` (the source archive for the rest). A symlinked dev
checkout is left alone — update it with `git pull`.

`scry init` is optional — `scry` runs with built-in defaults — but it's the fastest way to
compose a panel from the subscriptions you actually have (Kimi included).

### Uninstall

There's no uninstaller — scry only drops files into user-owned directories, so removing it is
just deleting them. The one-line installer (default `INSTALL_DIR=~/.local/bin`) creates:

```sh
# the binary, the API-key adapters, and the vendored web UI package (next to the binary)
rm -f  ~/.local/bin/scry ~/.local/bin/scry-deepseek ~/.local/bin/scry-glm
rm -rf ~/.local/bin/scry_web
# the Claude Code skills (honors CLAUDE_CONFIG_DIR if you set it)
rm -rf ~/.claude/skills/scry ~/.claude/skills/scry-plan
# your config + saved env keys + web UI database (delete only if you don't want them back)
rm -rf ~/.config/scry
# CLI run history (transcripts + index), if you enabled it
rm -rf ~/.scry
# the optional web UI deps the installer pip-installed (skip if other tools need them)
pip uninstall fastapi uvicorn python-multipart
```

(If you set a custom `INSTALL_DIR`, look there instead of `~/.local/bin`. `scry-eval` is **not**
installed by the installer — remove it only if you copied it onto your PATH by hand. Per-project
web history lives in each project's `<repo>/.scry/web/history.db` — remove it per repo.)

### Claude Code skill (`/scry`)

scry ships a **[Claude Code](https://claude.com/claude-code) skill**, so you can consult the
panel without leaving your editor:

```
/scry why is my Postgres query slow, and how do I fix it?
```

The installer drops it into `~/.claude/skills/scry/` (honors `CLAUDE_CONFIG_DIR`), so the
`/scry` command is available the next time Claude Code starts. The skill runs scry headlessly
with **`--no-anim --quiet`** — the scrying orb is for humans; an animation captured into an
agent's tool output is just hundreds of lines of escape codes burning tokens, so the skill
turns it off and returns only the fused answer. Ask for "where do they disagree?" and it adds
the consensus map. (Source: [`.claude/skills/scry/SKILL.md`](.claude/skills/scry/SKILL.md).)

A second skill, **`/scry-plan <request>`**, runs the full panel-driven planning mode (below)
from inside Claude Code: the panel's clarifying questions are relayed to you as native
question cards, your answers feed back round by round, and scry drafts a repo-grounded plan +
diagnostics. It drives scry's headless `scry plan --step` JSON protocol (one round per call,
state carried via resume checkpoints).
(Source: [`.claude/skills/scry-plan/SKILL.md`](.claude/skills/scry-plan/SKILL.md).)

## Usage

```sh
scry init                                   # interactive setup: choose CLIs → write your panel
scry update                                 # upgrade scry to the latest build, in place
scry --check                                # pre-flight: are my CLIs installed + logged in?
scry "Explain why my Postgres query is slow and how to fix it"   # deep research (default; see below)
cat prompt.txt | scry                       # prompt from stdin
scry "..." --no-clarify                     # skip the clarifying interview
scry "..." --depth 4                        # target more research rounds (default 3, cap 5)
scry "..." --repo                            # ground the panel read-only in the current git repo
scry plan "add rate limiting to my API"     # interactive, panel-driven planning (see below)
scry --mode fusion "..."                    # single-shot 3-stage panel/judge/synthesis (fast)
scry --mode synthesize "..."                # lighter 2-stage (skip the judge)
scry --no-web "..."                         # pure generation, no web tools
scry --effort high "..."                    # raise reasoning effort on every stage
scry --json "..." > out.json                # research: {brief, rounds, responses, analysis, final, cost}
scry --show-proposers "..."                 # also print each model's raw answer (stderr)
scry --dry-run "..."                        # print the exact commands, run nothing
scry --no-anim "..."                        # plain progress (reduced motion); honors NO_COLOR too

scry last                                   # re-print the most recent answer (pipeable on stdout)
scry log                                    # list recent runs with cost + pass/fail
scry log 50                                 # ...the last 50
```

### Flags

| Flag | Effect |
|---|---|
| `--mode research\|fusion\|synthesize` | iterative deep research (default) vs single-shot 3-stage vs 2-stage |
| `--depth N` | (research) target research rounds (overrides `research.max_rounds`) |
| `--max-rounds N` | (research) hard cap on rounds (overrides `research.hard_cap`); (plan) clarifying-round cap |
| `--no-clarify` | (research) skip the clarifying-question interview |
| `--repo [PATH]` / `--no-repo` | (research/plan) ground the panel read-only in a repo (no arg = cwd) / force the external-world default |
| `--no-web` | disable web tools on the panel (judge/synthesis are already web-off) |
| `--effort low\|medium\|high\|xhigh\|max` | reasoning effort, every stage (where supported) |
| `--max-tool-calls N` | cap web tool iterations (Fusion default 8; claude only) |
| `--max-output-tokens N` | cap output tokens (claude only) |
| `--panel`, `--judge`, `--aggregator` | override models, e.g. `--panel "claude:opus,codex,agy:Gemini 3.1 Pro (High),kimi"` |
| `--check` | verify each provider CLI is installed + logged in, then exit (no paid calls) |
| `--no-anim` | disable the scrying-orb animation (reduced motion); also auto-off under `NO_COLOR`/`TERM=dumb` |
| `--no-save` | don't record this run to `~/.scry` (see [Cost & run history](#cost--run-history)) |
| `--json`, `--show-proposers`, `--dry-run`, `--quiet` | output / debugging |

A panel member is `provider[:model]`. Same-model "self-pairing" still improves results — most of
Fusion's lift comes from the synthesis step, not model diversity.

### Deep Research mode (the default `scry "<prompt>"`)

Bare `scry "<prompt>"` runs an **iterative, gap-driven deep-research loop** over the whole panel,
not a single fan-out. The structural advantage over other deep-research tools: they clone *one*
model into identical sub-agents, whereas scry already has **five different frontier models**, a
**non-candidate judge** (verification, not self-preference), and a **synthesizer**. The loop:

1. **Clarify** — an interactive clarifying-question interview (the whole panel proposes questions,
   the judge dedups them, you answer one at a time), reusing `scry plan`'s machinery. Interactive
   only — skipped automatically under `--json`, a non-tty stdin, or `--no-clarify`.
2. **Brief** — the judge model normalizes your prompt (+ clarifying answers + optional repo
   context) into a precise intent and 3–6 sub-questions.
3. **Round 1 — diversity fusion** — every panelist investigates the full brief web-on and returns a
   **condensed, sourced findings brief** (claims + the URLs/paths that support them), not a raw dump.
4. **Reflect** — a research-tuned judge compares the findings and emits the 5-field analysis **plus**
   targeted `open_questions`, each tagged whether it needs a live web search.
5. **Rounds 2+ — gap-targeted, capability-routed** — only the open gaps are re-fanned, and only to
   the capable models: live-web gaps go to the web-capable searchers (claude/codex/kimi + Gemini's
   always-on grounding), reasoning gaps to everyone (incl. the no-web DeepSeek). A live-web gap is
   never routed to a no-web model.
6. **Stop** — adaptive with a generous cap: target `research.max_rounds` (default 3), early-exit
   when the judge finds no significant new gaps, hard cap `research.hard_cap` (5).
7. **Synthesize** — the aggregator writes **one fused prose answer** from the judge-compressed
   findings (never the raw transcripts), merging the cited sources into a single clean list.

```sh
scry "what's the best embedding model for code search in 2026, and why?"   # full deep research
scry "..." --no-clarify                     # skip the interview, go straight to research
scry "..." --depth 4 --max-rounds 6         # deeper: target 4 rounds, hard cap 6
scry "..." --repo                            # ground the search-capable panel in the current git repo
scry "..." --repo ../other-project           # ...or a specific repo path
scry "..." --no-repo                         # force external-world (ignore an auto-detected repo)
scry "..." --json                            # {brief, rounds:[{responses, analysis}], final, cost}
scry --mode fusion "..."                     # opt out: today's fast single-shot pipeline
```

**Repo grounding.** `research.repo_context: "auto"` (the default) grounds the search-capable panel
**read-only** in a surrounding git repo when one is detected, so research can draw on your code as
well as the web; `--repo [PATH]` forces it (no arg = cwd), `--no-repo` forces the scrubbed,
external-world cwd. Read-only-unsafe providers (e.g. agy) are isolated from the repo, as in `scry
plan`. Note this sends repo contents to your panel models.

**Cost.** Deep research is **deliberately expensive** — five heterogeneous CLIs across multiple
rounds on your subscription quotas. The structural mitigations are baked in (gap-targeted re-fan of
only the open gaps to only the capable models; a low default round count; the judge compresses
findings before synthesis), but if you want one fast pass, use `--mode fusion`. Tune depth in the
`research` block (`max_rounds`/`hard_cap`/`early_exit`) and per-round budgets in `phases.research`
(web-on, `timeout: 2100` by default).

> **Existing configs:** a config that pins `"mode": "fusion"` keeps getting the single-shot pipeline.
> Set `"mode": "research"` in your `~/.config/scry/config.json` (or re-run `scry init --force`) to
> make deep research your default.

### Plan mode (`scry plan "<request>"`)

An interactive mode for producing the **best possible implementation plan** — like a "grill-me"
interrogation, but driven by the whole panel instead of one model. It is deliberately expensive
(several panel fan-outs); that's the point.

Each round, the **whole panel** proposes clarifying questions; the **judge deduplicates** them
(merging duplicates and dropping already-answered ones, but surfacing *every* distinct question — no
ranking); they're asked **one at a time**. Your answers accumulate, and rounds repeat until the
panel is confident — or you type `done` — then the normal fusion pipeline (panel → judge → synthesis)
drafts and fuses one structured Markdown plan (`## Context / Approach / Key files / Steps /
Verification / Risks`).

```sh
scry plan "add rate limiting to my API"     # interactive: answer questions one at a time
scry plan "..." --out plan.md               # write the plan to a chosen path (default: ./scry-plan-<id>.md)
scry plan "..." --no-out                    # don't write any files; print to stdout only
scry plan "..." --max-rounds 3              # cap the clarifying rounds (default 5)
scry plan "..." --json                      # {mode:"plan", transcript, rounds, final, ...}
scry plan --resume                          # continue the most recent interrupted session
scry plan --resume=<id>                     # continue a specific session by id
scry plan --list                            # list unfinished, resumable sessions (free; no models)
scry plan "..." --no-repo                   # don't let the panel read the current directory
```

The plan prints to stdout (pipeable) and is saved to history — `scry last` re-prints it. Progress and
the clarifying questions go to stderr, so a redirected/piped stdout stays clean. By default it also writes
two files next to where you run it: the plan (`./scry-plan-<id>.md`, or your `--out PATH`) and a
human-readable **diagnostics file** alongside it (`<plan>.diagnostics.md`) — a per-stage table of which
models ran, their status/timing/cost, **any failures** (e.g. a panel member that errored), the settings the
run used, and the judge's consensus map. Pass `--no-out` to skip both and print to stdout only. The
clarifying-question rounds run with web tools **off** by default (they're about your intent, not external
facts; configurable via `phases.interview`); the final plan drafting uses web per your normal settings.
Tune `max_rounds` / `repo_context` in the top-level `plan` block, and per-phase budgets (the interview and
the final draft) in the `phases` block (`phases.interview`, `phases.final`).

**Repo-aware by default.** Unlike a normal `scry` run (which fans out in a scrubbed temp cwd), `scry plan`
gives the panel **read-only** access to the directory you launch it in, so the plan is grounded in your
actual code. Mutating tools stay disabled; pass `--no-repo` (or set `plan.repo_context: false`) to opt out.
Note this sends repo contents to your panel models — the same exposure as running `claude`/`codex` in the
repo directly.

**Patient final draft.** The interview rounds stay fast (web-off, fail-fast via `phases.interview`), but the
final web-researched draft gets a longer timeout ceiling via `phases.final` (default `timeout: 2100`): it's
web-on and reads your repo, so it can run long. Tool-call turns are **uncapped by default**, so a deep draft
can't be cut off mid-research (claude would otherwise surface that as `model error: exit 1`). The `final`
budget applies only to the final draft (layered across its panel/judge/synthesis) and never slows the fast
interview calls. Both are plain ceilings, so they don't slow a call that finishes early.

**Resumable.** Every plan run checkpoints its transcript after each answer; if one is interrupted (Ctrl-C,
a crash, a failed draft), `scry plan --resume` continues it from where it stopped — replaying your answers,
skipping completed rounds, and going straight to the pending step. `scry plan --list` shows the unfinished
sessions waiting to be resumed — each with its id (usable verbatim as `--resume=<id>`), last-updated time,
the round it reached, how many questions you answered, and the original request. It makes no model calls.

| Flag | Effect (with `plan`) |
|---|---|
| `--max-rounds N` | cap interactive clarifying rounds (default 5; the hard backstop on cost) |
| `--out PATH` | write the Markdown plan to `PATH` (default `./scry-plan-<id>.md`); a `<plan>.diagnostics.md` is written alongside |
| `--no-out` | don't write the plan or diagnostics files; print to stdout only |
| `--resume[=<id>]` | continue the most recent (or a specific) unfinished planning session |
| `--list` | list unfinished, resumable sessions (free — reads history, runs no models) |
| `--no-repo` | don't give the panel read-only access to the current directory |
| `--panel`, `--judge`, `--aggregator`, `--effort`, `--no-web`, `--json`, `--no-save` | as for a normal run |

## Web UI (`scry web`)

A local, single-user web app that puts the full panel — one-shot **scry**, the interactive
**plan** interview, and web-on **deep research** — behind a sleek browser UI, so you can
use scry *outside any project directory* (the thing you'd otherwise open gemini/claude/chatgpt
for). It reuses scry's Python internals **in-process** (no extra `scry` subprocess) and never
sends your prompts anywhere except the model CLIs scry already drives.

```sh
scry web                 # → http://127.0.0.1:8765 (opens your browser)
scry web --port 9000     # pick a port
scry web --no-open       # don't auto-open a browser
scry web --host 127.0.0.1   # localhost-only by default (see security note below)
```

What you get:

- **Sidebar + chat** — a *Scratchpad* for ad-hoc, contextless questions, plus **Workspaces**
  (managed scry project scaffolds) and any **Project** directory you open. Pick a capability
  (scry / plan / research) per message; multi-turn context carries between messages.
- **Interactive plan interview** — the same clarifying-question loop as `/scry-plan`, as cards
  in the browser. Answer, the panel converges, then it drafts and fuses the Markdown plan.
- **Full structured results** — the fused answer, an expandable per-model panel, the judge's
  consensus map, and a cost tally. Plans/reports are written to disk (in place for projects,
  under `~/.config/scry/runs/` for scratchpad sessions) with copy/download/reveal.
- **History** — every conversation + run is persisted in SQLite (per-workspace/project; the
  scratchpad and the locations registry live in `~/.config/scry/web/web.db`). Completed runs
  are also mirrored to the CLI's `~/.scry` log (`scry last` / `scry log`).
- **Promote to project** — turn a scratchpad session into a real, **CLI-openable** scry project
  (new dir + `git init` + `scry.config.json`), carrying its history and artifacts with it.

**Install.** Nothing extra to do — **`scry web` comes with the standard install**. The
one-line installer vendors the `scry_web/` package next to the binary and installs its
only third-party deps (FastAPI + uvicorn; the core CLI stays stdlib-only,
zero-dependency), and `scry update` keeps both current. So after `install.sh` you just
run `scry web` from anywhere. If you ever need to add the deps by hand (e.g. you set
`SCRY_NO_WEB=1`):

```sh
pip install fastapi 'uvicorn[standard]' python-multipart
```

The frontend ships **pre-built and vendored** (`scry_web/static/`) — no Node/npm needed to run.
To rebuild it after editing, see `scry_web/static/` (plain ES modules, no build step required).

**Security.** The server is single-user, **unauthenticated**, and bound to **localhost** by
default. A Host/Origin-validation middleware rejects DNS-rebinding / cross-origin requests.
Credentials are reused from your existing scry config/env — there's no key entry in the UI;
if no providers are configured the UI directs you to run `scry init` first. Don't bind it to a
routable interface (`--host 0.0.0.0`) without adding your own auth.

## Configuration

`scry` runs with built-in defaults. The config is about which subscription CLIs you have and how you
like to fuse them — a property of your machine, not any repo — so it lives **once per computer** at
`~/.config/scry/config.json` (run `scry init` to generate it). A project that genuinely needs a
different panel can drop a **`scry.config.json`** beside its code (run `scry init --local`); that file
overrides the global config whenever `scry` runs from that directory. Precedence (first wins):
`--config path` → `./scry.config.json` → `~/.config/scry/config.json` → built-in defaults. See the
bundled [`config.json`](config.json) for a fully-worked example (it is a reference, **not**
auto-loaded by name — pass it with `--config ./config.json` to use it directly).

- **`settings`** — the global default Fusion knobs (`web_tools`, `max_tool_calls`, `effort`,
  `max_output_tokens`, `timeout`, `save_history`). `timeout` is the per-call timeout in seconds (default
  420). Every pipeline phase inherits these and may override them in `phases` (below).
- **`phases`** — per-phase overrides of `settings`. Each stage inherits every global setting and overrides
  only what it lists. Stages: `panel`, `judge`, `synthesis` (a fusion run); `interview`, `final` (plan);
  `brief`, `research`, `reflect` (deep research). Resolution per call (later wins): `settings` →
  `phases[stage]` → the `final` overlay (plan draft only) → explicit CLI flags. Defaults reproduce scry's
  built-in behavior (synthesis/interview/brief/reflect web-off; `research` web-on with `timeout: 2100`;
  `final` gets `timeout: 2100`). The research `reflect` judge runs **web-off** on purpose — it reasons
  over the panel's gathered findings and must return strict JSON; web tools make it ramble and break the
  gap loop. `max_tool_calls` is **uncapped by default** — set it on any phase to cap claude's
  `--max-turns`. e.g. `"judge": {"web_tools": false, "max_tool_calls": 4}`.
- **`providers`** — how to drive each CLI: base `cmd`, `model_flag`, capture (`json`+`result_path`,
  `outfile`+`-o`, or `text`), `system_flag`, `env_unset`, and a **`caps`** block mapping each
  Fusion knob to that CLI's flags (`web_on`/`web_off`, `tool_cap`, `effort`, `max_tokens_env`). A
  provider can instead carry an **`agent_file`** block (kimi) when the CLI has no argv flags for
  tool/web control — scry then writes a temp read-only agent file per call (see "Adding Moonshot").
  Each provider record also carries a top-tier **`model`** field (e.g. `"model": "opus"` for claude).
  Panel members, the judge, and the aggregator that omit their own `model` inherit this provider
  default — one swap-point per provider to upgrade the whole fleet. An explicit `model` on a panel
  member always overrides the provider default. Each provider can also declare a top-tier **`effort`**
  that all phases inherit (panel, judge, and synthesis) — an explicit `--effort` flag or a
  `phases[stage].effort` override still wins. Pinned defaults: claude `max`, codex `xhigh`, deepseek
  `max`, glm `max` (sent to the API as `reasoning_effort` + `thinking` by the `scry-deepseek` /
  `scry-glm` adapters). agy is already maxed via its model name (`Gemini 3.1 Pro (High)`) and kimi
  runs thinking-on by default
  (can't be disabled on K2.7), so neither has an `effort` field. **Note:** per-provider max effort
  raises latency and cost on every call, including judge and synthesis.
- **`panel`** — `{provider, model, label}` proposers. Repeats are allowed (self-pairing still helps).
  The built-in default runs all six providers at top tier: claude (`opus`), codex (`gpt-5.5`), agy
  (`Gemini 3.1 Pro (High)`), deepseek (`deepseek-v4-pro`), kimi (`K2.7`), and glm (`glm-5.2`). Note
  that deepseek is **knowledge-only** (no web search) — a voice without live grounding; glm, by
  contrast, has **built-in web search**. Compose your own panel with
  **`scry init`** or `--panel`.
- **`judge`**, **`aggregator`** — `{provider, model}` per stage (default `claude:opus`).
- **`mode`** — the default pipeline for bare `scry`: `research` (deep research, the default), `fusion`
  (single-shot 3-stage), or `synthesize` (2-stage). `--mode` overrides it per run.
- **`research`** — Deep Research mode knobs: `clarify` (the interactive interview, on by default),
  `max_rounds` (target depth, 3), `hard_cap` (ceiling, 5), `early_exit` (stop on no new gaps),
  `sub_questions` (brief decomposition target, 6), and `repo_context` (`"auto"` detects a surrounding
  git repo, `"off"` forces external-world). `--depth`/`--max-rounds`/`--no-clarify`/`--repo`/`--no-repo`
  override. A provider record may also set `"no_web": true` (deepseek) so a live-web gap is never routed
  to it.

### Setup wizard (`scry init`)

`scry init` is a small interactive wizard. It opens with an
animated **rune-circle** splash — a violet sigil that inscribes itself stroke-by-stroke, lights the
four "seer" runes, and opens a central eye (distinct from the run-time scrying orb; press Enter to
continue) — then lists the known provider CLIs with their install status, lets you pick panel members
— **repeats allowed**, with an optional `:model` (e.g. `1:opus`) — then asks for a judge,
an aggregator, and whether to enable web search, and writes a minimal config (it references the
built-in provider records, so the file stays small). By default it writes the global
`~/.config/scry/config.json`; **`--local`** writes a project-local `./scry.config.json` instead. Flags:
`--out PATH` to choose an exact path (overrides `--local`), `--force` to overwrite without a prompt.
The splash honors `--no-anim` / `NO_COLOR` / non-TTY (static frame, no keypress needed), so scripted
`scry init --out …` stays non-interactive. Re-run it any time to recompose your panel.

### Google (Antigravity / `agy`)

Google is wired in via the **Antigravity CLI** (`agy`), driven through your existing Google
subscription. The default panel uses `Gemini 3.1 Pro (High)` (inherited from the provider's top-tier
`model` field).

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

### Moonshot (Kimi / `kimi-cli`)

Moonshot is wired in via the **Kimi CLI** (`kimi-cli`), authenticated with `kimi-cli login` (Kimi Code OAuth —
it reuses your Kimi membership, **no API key**; we unset `KIMI_API_KEY` so a stray key can't divert
billing off the subscription). Install it with `curl -LsSf https://code.kimi.com/install.sh | bash`
(or `uv tool install --python 3.13 kimi-cli`).

- **Default model:** none — the kimi provider ships with `model: ""`, so kimi uses your account's
  `default_model` (the Kimi Code membership configures `kimi-for-coding`). Unlike the other
  providers, kimi is **not** pinned to a top tier: a model id passed to `kimi-cli --model` **must be
  defined in your `~/.kimi/config.toml`** (run `kimi-cli info` to see what your account exposes), so a
  fixed default would error `LLM not set` for memberships that lack it. Pin one yourself when it's
  there — e.g. `--panel "...,kimi:K2.7"` or set the provider's `model`. Leaving it blank always works.
- **How it's driven:** `kimi-cli --quiet` runs print mode (`--print --output-format text
  --final-message-only`), which prints **only the final answer as plain text** (`"capture": "text"`) and
  auto-approves tool calls (`--afk`); the prompt arrives on **stdin**. There's no per-call
  system-prompt flag, so (like codex/agy) the system prompt is folded into the prompt.
- **Read-only + web toggle (the interesting bit):** Kimi has no argv flags to allow/deny tools or turn
  web on/off. Instead, scry hands each call a **generated agent file** (`--agent-file`) that `extend`s
  Kimi's built-in `default` agent but excludes the mutating tools (`Shell`, `WriteFile`,
  `StrReplaceFile`, `Agent`) — making it read-only like the other panels — and, when web is off, also
  excludes `SearchWeb`/`FetchURL`. So **unlike agy, kimi honors `--no-web`** (web off for synthesis, per
  Fusion). The file is written to a temp path per call and removed afterward.

### The API-key providers: DeepSeek and GLM

scry's panel is built around the subscription CLIs above, but two providers — **DeepSeek** and
**GLM** (Zhipu/Z.ai) — have **no subscription CLI**, only an API. scry ships a small stdlib adapter
for each (**`scry-deepseek`**, **`scry-glm`**) that calls the provider's OpenAI-compatible API.
These are the **two providers that need an API key**; both ship in the default panel at top tier.
DeepSeek first:

```sh
# Recommended: keep the key in scry's config dir (a sibling of config.json) — read
# by every shell, cron job, CI run, and Claude Code session:
mkdir -p ~/.config/scry
cp .env.example ~/.config/scry/.env && chmod 600 ~/.config/scry/.env   # then add DEEPSEEK_API_KEY=sk-…
# …or export it — but use ~/.zshenv, NOT ~/.zshrc (see "Shell-rc caveat" below):
export DEEPSEEK_API_KEY=sk-...                        # platform.deepseek.com (metered, pay-as-you-go)
scry --panel "claude:opus,codex,deepseek" "..."     # deepseek inherits the top-tier default (deepseek-v4-pro)
scry --check --panel "...,deepseek"                  # shows: ✓ deepseek installed
```

- **Key management.** `scry-deepseek` resolves `DEEPSEEK_API_KEY` in this order (first wins): the real
  env var → `$SCRY_ENV_FILE` → a `.env` next to the adapter (handy in a cloned repo) → `~/.config/scry/.env`
  (the recommended home — a sibling of scry's `config.json`, read regardless of shell type). It's gitignored;
  never commit it, and keep it `chmod 600`. The key is loaded only in the `scry-deepseek` process, so the
  other providers never see it; it never belongs in `config.json`. See [SECURITY.md](SECURITY.md).
- **Shell-rc caveat.** A bare `export DEEPSEEK_API_KEY=…` in `~/.zshrc`/`~/.bashrc` is read **only by
  interactive shells**, so headless runs (the `/scry` and `/scry-plan` Claude Code skills, scripts, cron)
  won't see it. Put the export in `~/.zshenv` (zsh) / `~/.profile` (bash), or — simpler — use
  `~/.config/scry/.env`, which sidesteps shell startup entirely.
- The adapter **resolves automatically as a sibling of `scry`** — scry now resolves a provider command
  by PATH → next-to-scry → cwd, so no install/symlink is needed even though proposers run in a temp cwd.
- **In the default panel at top tier (`deepseek-v4-pro`)** — requires `DEEPSEEK_API_KEY`; drop it
  with `--panel` or `config.json` if you haven't set a key.
- **Knowledge-only:** the raw chat API has no web search, so this member is handicapped on web-research
  tasks like DRACO. Defaults to the top-tier `deepseek-v4-pro` via the provider `model` field; the
  legacy `deepseek-chat`/`deepseek-reasoner` aliases route to V4-Flash. Base URL overridable via
  `DEEPSEEK_BASE_URL`.
- **No silent truncation:** the adapter requests the model's documented max output (V4: 384K tokens),
  so long answers are never cut short at the API's 4096-token default. Override with `--max-tokens`.
- Unlike the subscription providers, this one **bills per token against a metered API**, not a flat
  subscription.

#### GLM (Zhipu / Z.ai)

GLM is the **second API-key provider**, via the **`scry-glm`** adapter (same shape as `scry-deepseek`).
Get a key at <https://z.ai/manage-apikey/apikey-list> and set **`GLM_API_KEY`** the same way (recommended:
`~/.config/scry/.env`; the key-resolution order and the `~/.zshenv` shell-rc caveat are identical to
DeepSeek above). It ships in the default panel at top tier (`glm-5.2`).

- **Built-in web search (unlike DeepSeek).** GLM has a first-class `web_search` tool, so scry's web-on
  cap passes `--web` and the adapter injects it for live grounding — and `--no-web` omits it. No external
  search provider or function-call loop is needed; web search is a **metered add-on per query**.
- **Endpoint.** Defaults to the international `https://api.z.ai/api/paas/v4`; override with `GLM_BASE_URL`
  (e.g. the mainland `https://open.bigmodel.cn/api/paas/v4` — a separate account/key). The search backend
  is `GLM_SEARCH_ENGINE` (default `search_pro_jina`; on the mainland use `search_pro`/`search_std`).
- **No silent truncation:** requests `glm-5.2`'s documented max output (128K) so long answers aren't cut
  at the API default (override with `--max-tokens`). Effort `max` → `--reasoning-effort` → top-level
  `reasoning_effort` + `thinking:{type:enabled}`.
- The adapter **resolves automatically as a sibling of `scry`** (PATH → next-to-scry → cwd). Like
  DeepSeek, this **bills per token**, not against a flat subscription.

## Robustness

- **Parallel** proposers, each with a configurable per-call `timeout` (`settings.timeout`, default 420s;
  override per stage in `phases`).
- **Partial-failure tolerant** — a failed/timed-out proposer (including a bad model id, detected via
  `is_error`) is logged to stderr and excluded; the run continues if ≥1 proposer answers. If *all*
  fail you get a Fusion-style `failure_reason` (`all_panels_failed` / `rate_limited` /
  `insufficient_credits`) and exit 1.
- **Clean stdout** — progress on stderr; stdout is just the final answer (or `--json`).
- **No repo bleed-in** — proposers run in a throwaway temp cwd, so they answer as plain models.
- **Recursion guard** — `SCRY_DEPTH` is set in child env.

## Live output & the consensus map

- **Streaming** — on an interactive terminal the fused answer types itself out token-by-token as
  the synthesizer writes it (claude aggregator, via `--output-format stream-json`). Piped or
  `--json` output stays byte-clean and buffered; a provider that can't stream falls back silently.
- **Consensus map** — after a fusion run, `scry` surfaces the judge's analysis (otherwise computed
  and discarded) as a colored panel: what the panel **agreed** on (trust it), where they
  **contradicted** (scrutinize), each model's **unique insight**, and the **blind spots** none
  addressed — i.e. what the extra fusion cost actually bought. Auto-shown on a TTY; `--map` forces
  it, `--no-map` hides it.
- **Accessible** — honors `NO_COLOR` / `FORCE_COLOR` / `TERM=dumb`; `--no-anim` (or `SCRY_NO_ANIM`)
  swaps the scrying-orb animation (and the `scry init` rune-circle splash) for a plain static fallback.

## Cost & run history

Fusion fans one prompt across N+2 model calls, so **knowing what a run cost matters.** Every
run ends with a one-line tally on stderr:

```
✦ 5 calls · $0.34 · 47k→6k tok · 3 web · 72s
```

and `--json` carries a structured `cost` block (per-stage `total_usd`, input/output tokens, and
web-search count). The numbers come straight from each provider's own output via a config-driven
`usage` path map (claude reports `total_cost_usd` + `usage`). **Honesty over false precision:**
subscription CLIs that don't meter per call aren't reported as `$0` — the tally says so
explicitly, and the `$` total covers only the calls that actually reported a cost.

Each run is also saved under `~/.scry/` (a full transcript per run, plus a `history.jsonl` index):

```sh
scry last        # re-print the most recent fused answer (clean on stdout — pipe it)
scry log         # table of recent runs: when · cost · ok/total · prompt
scry log 50      # the last 50
```

Saving is on by default (`settings.save_history`); pass `--no-save` to skip a single run, or set
`"save_history": false` in config to disable it entirely.

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
(2) *Cost* — the default `--grader-mode one-shot` grades all ~39 criteria in **one** call per output,
so a 10-task fused-only stratified run ≈ 10 scry runs + 10 grading calls (≈ 40 with `--grade-solos`,
which grades all 4 outputs). The accuracy-leaning `--grader-mode per-criterion` makes ~39 calls/task
instead (≈ 400 for that run, ≈ 1,600 across all 4 outputs).

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
  available if you want terminal restrictions. Default `--print-timeout` is 5m; `scry` sets 400s under
  the shared 420s `settings.timeout`.
- **Moonshot (`kimi-cli`).** Auth is `kimi-cli login` (Kimi Code OAuth — reuses your membership, no API key);
  `KIMI_API_KEY` is unset so a stray key can't divert billing off the subscription. Print mode
  auto-approves tool calls (`--afk`), so scry constrains it to **read-only** via a generated
  `--agent-file` (no `Shell`/`WriteFile`/`StrReplaceFile`/`Agent`); that same file is how `--no-web` is
  honored (it also drops `SearchWeb`/`FetchURL`). `kimi-cli --version` only confirms the binary — login isn't
  cheaply verifiable, so `scry --check` reports "installed & runnable", not "logged in". Uses the shared
  `settings.timeout` (default 420s) like every other provider.
- **`ANTHROPIC_API_KEY`** is unset for `claude` calls so it never silently bills the Console API.
  Don't run `claude` here under `--bare` (that path requires a key).
- **Cost** ≈ sum of every panel member + judge + synthesis call (like real Fusion), plus web-search
  tool usage.

## Out of scope

`temperature` / `max_completion_tokens` (no CLI equivalent) and multi-layer MoA. (Streaming the
final answer token-by-token is now supported for the synthesis stage on a TTY — see "Live output
& the consensus map" above.)
