# Security

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue: email
**danielmandragona@gmail.com** with details and steps to reproduce. You'll get a
response within a few days.

## Threat model & what scry does with your machine

`scry` is a local, single-user tool. **The core CLI runs no server and opens no
port** — it only shells out to the model CLIs you already use. The optional
**`scry web`** subcommand is the one exception: it starts a **local, single-user,
unauthenticated** FastAPI/uvicorn server, bound to **`127.0.0.1`** by default and
protected by a Host/Origin-validation guard (which blocks DNS-rebinding /
cross-origin requests) — but there is **no login**. Because `--host` is
user-settable and unvalidated, **do not bind it to a routable interface**
(e.g. `--host 0.0.0.0`): that exposes the unauthenticated API to your LAN. Keep it
on localhost, or put your own auth/proxy in front. `scry` still sends your prompts
nowhere except the model CLIs it already drives.

Beyond that, it shells out to subprocesses and handles credentials indirectly, so:

- **It spawns subprocesses** (`claude`, `codex`, `agy`, …) via
  `asyncio.create_subprocess_exec` with an argv list (no shell), using commands
  defined in `config.json`. Treat a `config.json` from an untrusted source the
  same as a shell script — it can run arbitrary programs. Only use configs you
  trust.
- **It unsets `ANTHROPIC_API_KEY`** for `claude` calls (`env_unset` in
  `config.json`) so a run authenticates against your Claude subscription and can
  **never silently bill the Console/API**. Don't remove that unless you mean to.
- **API keys live in the environment, never the repo.** The subscription CLIs
  (`claude`/`codex`/`agy`/`kimi`) authenticate via their own logins — `scry` never
  sees or stores those. The only keys `scry` itself reads are **`DEEPSEEK_API_KEY`**
  and **`GLM_API_KEY`** (the two API-key providers, DeepSeek and GLM). Put them in
  your shell or a local **`.env`** (gitignored — copy `.env.example`); real
  environment variables take precedence. **scry strips each managed provider key
  out of every *other* provider's child process**: `DEEPSEEK_API_KEY`,
  `GLM_API_KEY`, `ANTHROPIC_API_KEY` and `KIMI_API_KEY` are each removed from the
  environment of every CLI except the one that needs it — so your DeepSeek/GLM key
  is never handed to the claude / codex / agy / kimi CLIs, whether you `export` it
  or load it from `.env`. Loading from a **`.env`** adds a second layer (the adapter
  reads it directly, so it isn't placed in any sibling's environment to begin with).
  Never commit `.env`, and never put keys in `config.json`.
- **Panel/judge calls run with web tools and read-only file tools enabled, but
  mutators disabled** (`--disallowedTools Bash Edit Write NotebookEdit`), and in
  a throwaway temp working directory so a proposer can't see or modify your repo.
  **One caveat — `scry plan`'s `agy`/Gemini panelist has no argv sandbox flag**, so
  its read-only behavior is enforced only by *instruction* and by **cwd isolation**
  (it can't see your repo), **not** by a hard sandbox. cwd isolation does not stop
  it from writing to **absolute or `$HOME` paths**, so it is not prevented from
  touching files outside the working directory. Run `scry plan` on **untrusted
  requests** with care.
- **Prompts and answers persist in plaintext on your own disk** when history is
  enabled (the default): the CLI writes a full transcript per run under **`~/.scry/`**
  (plus a `history.jsonl` index), and the web UI persists conversations to SQLite —
  the global registry at **`~/.config/scry/web/web.db`** and per-project history at
  **`<repo>/.scry/web/history.db`**. These are unencrypted; delete them (or run with
  `--no-save` / `"save_history": false`) if your prompts are sensitive. Nothing is
  uploaded by `scry` itself.
- **`SCRY_DEPTH`** is set in child environments as a recursion guard so `scry`
  can't accidentally invoke itself in a loop.

## Provider Terms of Service

`scry` drives vendor subscription CLIs in parallel, headless mode. No vendor
explicitly blesses programmatic fan-out across a single personal subscription —
this is a personal, local tool and that call is yours. See the README "Caveats".
