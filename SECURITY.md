# Security

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue: email
**danielmandragona@gmail.com** with details and steps to reproduce. You'll get a
response within a few days.

## Threat model & what scry does with your machine

`scry` is a local, single-user CLI. It does **not** run a server, open a port, or
send your prompts anywhere except to the model CLIs you already use. Still, it
shells out to subprocesses and handles credentials indirectly, so:

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
  sees or stores those. The only key `scry` itself reads is **`DEEPSEEK_API_KEY`**
  (the optional DeepSeek provider). Put it in your shell or a local **`.env`**
  (gitignored — copy `.env.example`); real environment variables take precedence.
  Keeping it in `.env` rather than `export`-ing it scopes the key to the
  `scry-deepseek` process, so the other providers' subprocesses never receive it.
  Never commit `.env`, and never put keys in `config.json`.
- **Panel/judge calls run with web tools and read-only file tools enabled, but
  mutators disabled** (`--disallowedTools Bash Edit Write NotebookEdit`), and in
  a throwaway temp working directory so a proposer can't see or modify your repo.
- **Prompts and answers** are passed to the model CLIs and (if you enable history)
  written to `~/.scry/` on your own disk. Nothing is uploaded by `scry` itself.
- **`SCRY_DEPTH`** is set in child environments as a recursion guard so `scry`
  can't accidentally invoke itself in a loop.

## Provider Terms of Service

`scry` drives vendor subscription CLIs in parallel, headless mode. No vendor
explicitly blesses programmatic fan-out across a single personal subscription —
this is a personal, local tool and that call is yours. See the README "Caveats".
