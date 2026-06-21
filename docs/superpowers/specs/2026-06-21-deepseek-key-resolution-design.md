# DeepSeek API-key resolution — config-dir + helpful errors

**Date:** 2026-06-21
**Status:** Approved (design)
**Scope:** `scry-deepseek` adapter (`_load_env_file` / `main`) + `README.md`
"DeepSeek — the API-key exception" + `.env.example`. Make the documented
`.env` key workflow actually work for an installed `scry`, following the
`aws`/`gh`/`kubectl` precedence-chain convention, and make the missing-key
error explain where to put the key.

## Motivation

`scry-deepseek` is the one provider that needs an API key (`DEEPSEEK_API_KEY`).
Today it resolves the key as: real env var, else a `.env` loaded from the
**script's own directory** (`os.path.dirname(os.path.abspath(__file__))`).

Two real, generalizable gaps fall out of "the `.env` must sit next to the binary":

1. **The README's recommended `.env` workflow silently fails for installed
   users.** README says `cp .env.example .env` and "`scry-deepseek` auto-loads
   `.env`." But the standard install (`install.sh`, `curl | sh`) puts the
   adapter at `~/.local/bin/scry-deepseek`, so it only looks for
   `~/.local/bin/.env` — which nobody creates and `install.sh` never writes.
   The documented path works **only when running from a cloned repo**. Most
   installed users' `.env` is never read; only a global `export` works for them.

2. **The missing-key error gives no guidance.** It prints `DEEPSEEK_API_KEY not
   set` with no hint of *where* to put the key, and no mention of the
   interactive-vs-non-interactive shell trap: a key exported in `~/.zshrc` is
   invisible to non-interactive shells, so headless `scry plan` (the shipped
   `/scry` and `/scry-plan` skills run non-interactively) never sees it. This is
   the exact failure a user hit on 2026-06-21.

"Secret lives next to the executable" is the non-standard part. Established CLIs
keep the durable secret in a **user-config directory independent of where the
binary is installed** (`~/.aws/credentials`, `~/.config/gh/`, `~/.kube/config`),
with a precedence chain: real env var > explicit file override > local/project
file > user-config file. A config file is read regardless of shell type, cron,
CI, or Claude Code — which is precisely why headless-capable tools avoid
depending on shell rc files.

## Goals

- Make `scry-deepseek` find the key from a standard, install-agnostic location
  (`~/.config/scry/.env`) so the documented workflow works after `curl | sh`.
- Add an explicit `$SCRY_ENV_FILE` path override (CI/tests/non-standard layouts).
- Preserve today's behavior exactly: real env var always wins; the script-dir
  `.env` keeps working for cloned-repo users.
- Rewrite the missing-key error to list every location checked and call out the
  `~/.zshrc` (interactive-only) vs `~/.zshenv` trap.
- Update README + `.env.example` to point at `~/.config/scry/.env` as the
  recommended home and explain the precedence.
- Factor resolution so it is unit-testable without touching the repo's real `.env`.

## Non-goals

- **No `install.sh` behavior change / no auto-scaffolding** of
  `~/.config/scry/.env`. `aws`/`gh` don't pre-create credential files; they
  document the path and error helpfully. Avoids creating a file users may not
  want. (Considered as a third option and declined.) The only installer edit is a
  one-line update to its closing **text** ("see `.env.example`" → also mention
  `~/.config/scry/.env`) — no logic change.
- **No permission enforcement** (refusing/warning on world-readable `.env`, like
  ssh). Documented as `chmod 600`, not enforced. Possible future addition.
- **No change to `scry` core or other providers.** The key is still loaded only
  inside the `scry-deepseek` process, so it never enters the claude/codex/agy/
  kimi subprocesses — the existing scoping property (README) is preserved.
- **No new dependency.** Stays stdlib-only; reuses the existing dotenv parser.
- No support for a TOML/INI credentials format — a dotenv `.env` matches the
  existing `.env.example` and parser.

## Design

### Resolution order (first hit wins)

For `DEEPSEEK_API_KEY` (and any other keys a `.env` carries, e.g.
`DEEPSEEK_BASE_URL`):

```
1. real process env var DEEPSEEK_API_KEY     # unchanged; an export always wins
2. $SCRY_ENV_FILE                            # explicit path override
3. <dir of abspath(scry-deepseek)>/.env      # current behavior (cloned repo / local)
4. <dir of realpath(scry-deepseek)>/.env     # symlinked installs (skip if == #3)
5. $XDG_CONFIG_HOME/scry/.env                # default ~/.config/scry/.env (canonical fallback)
```

Among files, a higher-priority file that supplies a given key wins; the real env
var beats all files. This is the standard "more-specific wins, user-global last"
precedence (mirrors git repo-config > `~/.config/git`). `~/.config/scry/.env` is
the *recommended* home not because it's highest priority but because it's the one
location that always works (every shell, cron, CI, Claude Code).

### Code shape (`scry-deepseek`)

Factor the candidate list out of the loader so it is pure and testable. The
loader takes the script path as a parameter (default `__file__`) so tests pass a
fake path and never read the repo's real `.env`:

```python
def _config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return os.path.join(base, "scry")

def _env_file_candidates(script_path: str) -> list[str]:
    cands = []
    if os.environ.get("SCRY_ENV_FILE"):
        cands.append(os.environ["SCRY_ENV_FILE"])
    d_abs = os.path.dirname(os.path.abspath(script_path))
    cands.append(os.path.join(d_abs, ".env"))
    d_real = os.path.dirname(os.path.realpath(script_path))
    if d_real != d_abs:
        cands.append(os.path.join(d_real, ".env"))
    cands.append(os.path.join(_config_dir(), ".env"))
    return cands

def _load_env_file(script_path: str = __file__) -> None:
    """Load KEY=VALUE lines from the first existing candidate(s) into the process
    environment WITHOUT overriding variables already set, so an explicit export /
    CI secret / higher-priority file always wins."""
    for path in _env_file_candidates(script_path):
        _apply_env_file(path)   # parse + set keys only if key not in os.environ
```

`_apply_env_file(path)` is the body of today's loader (open, skip blank/`#`/no-`=`
lines, strip quotes, `if key not in os.environ: os.environ[key] = val`), tolerant
of a missing file (`OSError` → return). Iterating in priority order with the
"don't override" rule gives the precedence above for free.

### Missing-key error (`main`)

When no key is found after `_load_env_file()`, print a multi-line, actionable
error to stderr (still `return 2`):

```
scry-deepseek: DEEPSEEK_API_KEY not set — this provider needs an API key.
Get one at https://platform.deepseek.com -> API Keys, then set it via any of:
  - ~/.config/scry/.env        (recommended; read in every shell, cron, CI, Claude Code)
  - $SCRY_ENV_FILE             (explicit path to a .env)
  - a .env next to scry-deepseek
  - export DEEPSEEK_API_KEY=... (note: ~/.zshrc is read ONLY by interactive shells,
                                 so headless `scry plan` won't see it — use ~/.zshenv)
See README: "DeepSeek - the API-key exception".
```

### Docs

- **README "DeepSeek — the API-key exception":** change the recommended setup to
  `mkdir -p ~/.config/scry && cp .env.example ~/.config/scry/.env && chmod 600
  ~/.config/scry/.env`; state the resolution order; add the `~/.zshrc` vs
  `~/.zshenv` note; clarify the in-repo `.env` is a dev convenience.
- **`.env.example`:** add a header line — for an installed `scry`, this file
  belongs at `~/.config/scry/.env` (or export the key in `~/.zshenv`, not
  `~/.zshrc`).

## Testing

New `tests/test_deepseek_env.py`, loading `scry-deepseek` via the existing
`tests/_harness.py` `SourceFileLoader` pattern. Each test passes a **fake
script_path** under a `tempfile.TemporaryDirectory()` and points
`XDG_CONFIG_HOME` / `SCRY_ENV_FILE` at temp paths, with `os.environ` saved and
restored — so the repo's real `.env` is never read and the suite stays hermetic.

Cases (TDD — write failing first):

- Real `DEEPSEEK_API_KEY` in env → all files ignored (env wins).
- `$SCRY_ENV_FILE` → key loaded from that file.
- `XDG_CONFIG_HOME=<tmp>` with `<tmp>/scry/.env` → key loaded (the installed-user
  path).
- script-dir `.env` still loaded (backward compat) when it's the only source.
- Precedence: with several present, the higher-priority file wins; a real env var
  beats every file.
- `_env_file_candidates` skips the realpath entry when it equals the abspath entry.
- Nothing set anywhere → key stays unset and `main()` returns `2` with the new
  error text on stderr (capture stderr; assert it names `~/.config/scry/.env` and
  the `~/.zshenv` note).

Run hermetically: `python3 -m unittest discover -s tests`.

## Risks / notes

- **New secret location.** `~/.config/scry/.env` is a new place a key can live;
  docs say `chmod 600`. Not auto-created, so no surprise files.
- **`$SCRY_ENV_FILE` naming** is consistent with scry's existing `SCRY_*` env
  (`SCRY_REPO`, `SCRY_REF`).
- **Backward compatibility:** purely additive. Real-env-wins and script-dir `.env`
  are unchanged; cloned-repo users see identical behavior.
- **Key scoping preserved:** the `.env` is still loaded only in the `scry-deepseek`
  process, so the key never reaches the other provider subprocesses.
- **install.sh** prints "see `.env.example`"; update that one line to mention
  `~/.config/scry/.env` so the installer's closing guidance matches the new docs
  (text-only, no behavior change).
