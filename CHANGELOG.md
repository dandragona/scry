# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **Renamed the project to `scry`** (was `fuse`). The command, config path
  (`~/.config/scry/`), recursion-guard env (`SCRY_DEPTH`), and the eval harness
  (`scry-eval`) all moved. The `ScryingOrb` progress animation gives the project
  its name: gaze into the orb, see one answer from many models.

### Added
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
  exact elevated command, preserving any overrides). Honors `SCRY_REPO` / `SCRY_REF` /
  `SCRY_UPDATE_URL`, mirroring `install.sh`.
- **`scry init`** — an interactive setup wizard (in the spirit of `openspec init`) that
  lists your installed provider CLIs, lets you compose a panel (**repeats allowed**, with
  optional `:model`), pick a judge + aggregator, toggle web search, and writes a minimal
  `config.json`. Flags: `--out PATH`, `--force`. It opens with an animated **rune-circle**
  welcome splash (a self-inscribing violet sigil — distinct from the run-time scrying orb)
  that degrades to a static frame under `--no-anim` / `NO_COLOR` / non-TTY.
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
