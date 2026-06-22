# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Per-provider max reasoning effort (all phases).** Each provider record now carries an `effort`
  default that panel, judge, and synthesis all inherit — an explicit `--effort` flag or a
  `phases[stage].effort` override still wins. Pinned: claude `max`, codex `xhigh`, deepseek `max`.
  agy is already maxed via its model name (`Gemini 3.1 Pro (High)`) and kimi runs thinking-on by
  default (can't be disabled on K2.7), so neither has an `effort` field. The `scry-deepseek`
  adapter maps the `max` effort to `--reasoning-effort` and writes `reasoning_effort` + `thinking:
  {type: enabled}` into the API request body; this is gated off for non-thinking models (e.g.
  `deepseek-chat`). Note: per-provider max effort raises latency and cost on every call, including
  judge and synthesis.
- **Per-provider top-tier `model` field with member-inherits resolution.** Each provider record now
  carries a `model` field pinned to its top tier (claude `opus`, codex `gpt-5.5`, agy `Gemini 3.1
  Pro (High)`, deepseek `deepseek-v4-pro`, kimi `K2.7`). Panel members, the judge, and the
  aggregator that omit their own `model` inherit the provider default — one swap-point per provider
  to upgrade the whole fleet. An explicit `model` on a panel member overrides it.
- **Default panel expanded to all five providers at top tier.** The built-in panel now fans out to
  claude, codex, agy, deepseek, and kimi — each at its provider-default top-tier model. Note that
  deepseek is knowledge-only (no web search) — a voice without live grounding.
- **DeepSeek adapter requests the model's documented max output (no more 4096 truncation).** The
  `scry-deepseek` adapter now sends `max_tokens` equal to the model's documented ceiling (V4:
  384K), so long answers are never silently cut short at the API's 4096-token default. Override with
  `--max-tokens`.
- **`SCRY_NO_TIMEOUT` / a negative `--timeout` disables the per-call timeout** (no kill —
  the call runs to completion, however long it takes). The per-call timeout is a hard
  `SIGKILL` that **discards the subprocess's work**, so a slow-but-progressing web-heavy
  agentic call — notably the `scry plan` final-draft panel, where claude/opus reads the
  repo + web to draft — could burn its whole ceiling and be killed with **0 tokens
  returned** rather than a partial answer. This escape hatch lets such a call finish: set
  the env var `SCRY_NO_TIMEOUT=1` (overrides every phase, regardless of config), or pass a
  negative `--timeout` (e.g. `--timeout -1`) or `"timeout": -1` in `settings`/`phases`. A
  timeout of `0`/unset still falls back to the default — a stray zero must not silently
  uncap a run and let it hang forever.

### Fixed
- **`scry plan` panel drafters now write a plan instead of trying to *do* the task.**
  The final-draft panel reuses the fusion pipeline, but each proposer was invoked with
  no system prompt — so an agentic coding CLI handed a bare task plus read-only repo +
  web access behaved like a coding agent: it tried to implement the work rather than
  describe it. The result was a half-strength panel (claude/opus churning tool calls to
  a 0-token timeout, codex/kimi wasting turns on blocked writes, and the unsandboxed
  agy/gemini member able to mutate the working tree). A new `PLAN_DRAFTER_SYSTEM` is now
  threaded to every proposer via `scry_run(..., panel_system=...)`, telling them their
  answer IS the plan, to read-only-ground it (no file writes, no mutating commands), and
  to treat the answered questions as binding. Normal `scry` runs are unchanged
  (`panel_system` defaults to `None`). This also restores the read-only invariant *by
  instruction* for the agy member, which has no argv sandbox flag to enforce it.
- **`scry plan` multiple-choice answers record the chosen option text, not bare
  numbers.** Answering a clarifying question with several option numbers (e.g.
  `1,3,4,5`) now resolves to the option labels in the transcript the drafting models
  see; previously only a single-digit answer was mapped, so a multi-select was stored
  verbatim as `1,3,4,5` and the plan referenced option numbers whose meanings it never
  knew. Comma- or space-separated selections are both accepted; anything that isn't a
  clean list of in-range numbers is still kept as free text.
- **`scry` installed unreadable → `[Errno 13] Permission denied` / "needs sudo to run".**
  `install.sh` set the binary's mode with `chmod +x` on a `mktemp` (0600) file, yielding
  **711**. For a root-owned install in `/usr/local/bin` (the default, which needs sudo to
  write), that's executable-but-not-*readable* by non-owners — and since `scry` is a Python
  script the interpreter must read to run, plain `scry …` failed with `[Errno 13]` and only
  `sudo scry` worked. `install.sh` now installs **755**, and `scry update` forces read bits
  (`| 0o444`) on the swapped-in file so an existing broken install **self-heals** on the next
  `scry update`.

### Changed
- **`scry plan` clarifying-round cap lowered to 5** (was 6). The interview was
  drifting toward the cap, so the default is now shorter; raise it back per-run with
  `--max-rounds N` or permanently via `plan.max_rounds` in config. Type `done` at any
  prompt to stop early regardless of the cap.
- **`max_tool_calls` is now uncapped by default** (was 8). A turn cap (`claude --max-turns`) discards
  the model's work on overrun rather than returning a partial answer, so as a default it only caused
  surprise failures; the per-call `timeout` is the real backstop. `scry init` no longer writes it, and
  `phases.final` no longer needs its old `max_tool_calls: 24`. Still fully supported as an opt-in — set
  `max_tool_calls` in `settings`, any `phases.<stage>`, or via `--max-tool-calls` to cap claude's turns.
- **Per-phase settings — every pipeline stage is now independently configurable** via a new top-level
  `phases` block. Each stage (`panel`, `judge`, `synthesis` for a run; `interview`, `final` for `scry
  plan`) inherits the global `settings` and overrides only what it lists — `web_tools`, `max_tool_calls`,
  `timeout`, `effort`, `max_output_tokens`. Resolution per call (later wins): `settings` → `phases[stage]`
  → the `final` overlay (plan draft only) → explicit CLI flags. e.g. run the judge web-off with fewer
  turns: `"judge": {"web_tools": false, "max_tool_calls": 4}`.
- **All providers share one per-call `timeout`** (`settings.timeout`, default **420s**), overridable per
  phase. Replaces the previous per-provider timeouts (claude/deepseek 360s, codex/agy/kimi 420s) that made
  models race different clocks. A new global `--timeout` flag overrides it for a run.
- **`scry plan`'s final draft sets its budget directly, not by scaling.** The deep, web-on, repo-reading
  draft can run long, so `phases.final` declares an explicit `timeout: 2100` layered across the draft's
  panel/judge/synthesis (tool-call turns are uncapped — see above). **Removed** the old multiplier knobs
  `plan.final_tool_call_scale` / `plan.final_timeout_scale` (and `plan.interview_web` →
  `phases.interview.web_tools`); old keys in a config are ignored and the new defaults reproduce the prior
  behavior exactly.
- **The installer no longer uses `sudo` or installs into a system directory.** `install.sh`
  now installs into a **user-owned** dir — `~/.local/bin` by default (override with
  `INSTALL_DIR`), like `rustup`/`uv`/`pipx` — so install, update, and run never need
  elevation, and the root-owned/unreadable failure mode can't occur. If the dir isn't on
  `PATH` it prints the exact `export PATH=…` line to add (it never edits your shell files),
  and it warns if an older `scry` earlier on `PATH` would shadow the new one. Covered by a
  new sandboxed `tests/test_install.py` (runs the real installer with a `sudo` stub that
  fails if invoked).
- **`scry plan` writes its output by default.** It now saves the plan to
  `./scry-plan-<id>.md` (override with `--out PATH`) plus a diagnostics file alongside
  it, instead of only printing to stdout. Pass `--no-out` for the old print-only
  behavior.
- **Config is global-first.** The canonical config lives once per computer at
  `~/.config/scry/config.json`; a project that needs a different panel opts in with a
  `./scry.config.json` (which overrides the global config when `scry` runs from that
  directory). scry no longer auto-loads a generic `./config.json` from the working
  directory — that filename is too common, so a `config.json` belonging to another tool
  could be silently merged into scry's config. Precedence: `--config` → `./scry.config.json`
  → `~/.config/scry/config.json` → built-in defaults. (`scry init` now writes the global
  config by default; `scry init --local` writes the project-local file.)
- **Renamed the project to `scry`** (was `fuse`). The command, config path
  (`~/.config/scry/`), recursion-guard env (`SCRY_DEPTH`), and the eval harness
  (`scry-eval`) all moved. The `ScryingOrb` progress animation gives the project
  its name: gaze into the orb, see one answer from many models.

### Added
- **`/scry-plan` Claude Code skill + `scry plan --step` protocol.** Run the full
  panel-driven planning interview from inside Claude Code: the panel's clarifying
  questions are relayed to you as native question cards, your answers feed back round
  by round, then scry drafts a repo-grounded plan + diagnostics. The skill drives a new
  headless `scry plan --step --json` protocol — each call reads an optional answers
  payload on stdin (`{"answers":[{"q","a"}],"done":bool}`) and prints one JSON envelope
  (`questions` / `ready` / `done` / `error`), carrying state between calls via the
  existing resume checkpoints. The installer now drops both `/scry` and `/scry-plan`.
- **`scry plan` diagnostics file.** Every plan run now writes a human-readable
  `<plan>.diagnostics.md` next to the plan: a per-stage table of which models ran,
  their status/timing/cost, **any failures** (so a panel member that errored is visible
  at a glance), the settings the run used (including the base and scaled tool-call cap),
  and the judge's consensus map. Suppress with `--no-out`.
- **Moonshot Kimi provider (`kimi`)** — fan a prompt out to Kimi via the Kimi CLI in
  headless print mode (`kimi --quiet`), authenticated with `kimi login` (Kimi Code
  OAuth — reuses your membership, no API key). Uses your account's default model
  (the Kimi Code membership exposes `kimi-for-coding`); pin a specific id with
  `kimi:<model>` only if your `~/.kimi/config.toml` defines it. Because Kimi's print mode has no
  argv flags for tool/web control, scry drives it with a **generated per-call agent
  file** (`--agent-file`) that makes it read-only (no `Shell`/`WriteFile`/`StrReplaceFile`/
  `Agent`) and toggles web by excluding `SearchWeb`/`FetchURL` — so, unlike agy, kimi
  honors `--no-web`. Wired into `config.json`, `scry --check`, and `scry-eval`.
- **`scry update`** — a self-update command. Downloads the latest single-file build
  from GitHub, verifies it's **complete** (declared length, entry-point marker, and
  that it compiles — so a dropped connection can't brick the binary) and not a
  downgrade, then **atomically** swaps it in place (same-directory temp + `os.replace`),
  preserving the exec bit. No `sudo` unless installed to a system dir (it prints the
  exact elevated command, preserving any overrides). Honors `SCRY_REPO` / `SCRY_REF`
  (shared with `install.sh`) plus `SCRY_UPDATE_URL` (a self-update-only full-file override).
- **`scry init`** — an interactive setup wizard that lists your installed provider CLIs,
  lets you compose a panel (**repeats allowed**, with optional `:model`), pick a judge +
  aggregator, toggle web search, and writes a minimal config. By default it writes the
  **global** `~/.config/scry/config.json` (config describes your machine's subscriptions,
  not a repo — so `scry` then works from any directory); `--local` writes a project-local
  `./scry.config.json` that overrides the global config when `scry` runs from that directory.
  Flags: `--out PATH`, `--local`, `--force`. It opens with an animated **rune-circle**
  welcome splash (a self-inscribing violet sigil — distinct from the run-time scrying orb)
  that degrades to a static frame under `--no-anim` / `NO_COLOR` / non-TTY.
- **Cost & token meter** — every run now reports what it actually spent: a one-line
  footer (`✦ 5 calls · $0.34 · 47k→6k tok · 3 web · 72s`) on an interactive run, and
  a `cost` block (per-stage `total_usd` / tokens / web searches) in `--json`. Read
  from each provider's output via a config-driven `usage` path map; providers that
  don't meter per call (subscription CLIs) are reported honestly as such rather than
  shown as `$0`.
- **Run history** — each run is saved under `~/.scry/` (full transcript +
  `history.jsonl`). `scry last` re-prints the most recent answer (pipeable on
  stdout); `scry log [N]` lists recent runs with cost and pass/fail counts. Gated by
  `settings.save_history` (default on); `--no-save` skips recording a single run.
- **Streaming** — on an interactive terminal the fused answer now types itself out
  token-by-token as the synthesizer writes it (claude aggregator, `stream-json`),
  instead of appearing as a sudden block. Buffered fallback for piped/`--json`
  output and non-streaming providers, so correctness never depends on the stream.
- **Consensus map** — after a fusion run, the judge's 5-field analysis
  (consensus / contradictions / unique insights / partial coverage / blind spots),
  previously computed and discarded in non-JSON mode, is rendered as a colored
  panel. `--map` forces it, `--no-map` hides it.
- `scry --check` — a zero-cost pre-flight doctor that verifies every configured
  provider CLI is installed **and** logged in (using non-billing probes like
  `codex login status`) before you spend on a real run, and prints the resolved
  panel + the per-run call multiplier.
- Accessibility: the orb now honors `NO_COLOR`, `FORCE_COLOR`, and `TERM=dumb`,
  and a reduced-motion mode via `--no-anim` / `SCRY_NO_ANIM` (auto-enabled when
  color is disabled), falling back to plain progress lines.
- `LICENSE` (MIT), `.gitignore`, `CONTRIBUTING.md`, `SECURITY.md`, and a one-line
  `install.sh`.
- **Test suite** — a stdlib-`unittest` suite (430+ tests, zero dependencies) covering
  `scry` and `scry-eval`: config/argv/JSON/agent-file/stream parsing, the `call_cli` /
  `stream_call` / `scry_run` pipeline, `--dry-run` / `--check` / `init` / `update` /
  the CLI surface, and the eval grading + DRACO logic. Every test runs against **stub**
  model CLIs or monkeypatched fakes, so it **never spends subscription credit**. Shared
  helpers live in `tests/_harness.py`; CI runs it alongside the existing `tests/smoke.sh`
  on ubuntu/macos × Python 3.9/3.12. See `tests/README.md`.

### Fixed
- **`scry update` crashed on a truncated download** — a dropped connection / CDN short
  read (server advertising more bytes than it sends) made `urllib`'s `read()` raise
  `http.client.IncompleteRead`, which slipped past the `(URLError, OSError)` handler and
  surfaced as an uncaught traceback (leaving the friendly "incomplete download" guard as
  dead code). It's now caught and reported cleanly, with the installed binary left
  untouched.
- **`scry-eval` dropped naturally-phrased judge verdicts** — `parse_verdict` only matched
  the choice when it was directly adjacent to the word "verdict", so a judge that wrote
  `My verdict is TIE` or `verdict: it's a tie` parsed as *no verdict* and was silently
  excluded from scoring. It now tolerates common connector words/punctuation while staying
  anchored to the keyword (no false positives on unrelated prose).
- **`scry init` produced odd labels for unnamed repeated members** — `_uniq_label` applied
  its `"member"` fallback only to the first label, so a second empty-base member became
  `-2` instead of `member-2`. The default now carries through to the suffixed labels.
- **`install.sh` on a fresh machine** — the installer defaulted to `/usr/local/bin`
  but never created it, so on a clean macOS (where that directory doesn't exist) the
  `sudo mv` failed with "No such file or directory". It now creates the destination
  first (`mkdir -p`, escalating to `sudo` only when needed), cleans up its temp file
  on failure, and reports the version from the installed path. The README's manual
  `ln -s` snippet had the same gap and now creates the directory too.

## [0.2.0] — 2026-06-14

Initial internal version (as `fuse`): parallel panel → judge → synthesis over
headless subscription CLIs (Claude Code, Codex, Antigravity), `--json` /
`--dry-run` / `--show-proposers`, partial-failure tolerance, the `ScryingOrb`
progress animation, and the `scry-eval` (then `fuse-eval`) evaluation harness.
