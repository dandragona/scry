# DeepSeek API-key resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the installed `scry-deepseek` find `DEEPSEEK_API_KEY` from `~/.config/scry/.env` (and a `$SCRY_ENV_FILE` override), with a helpful missing-key error, so the documented `.env` workflow works after `curl | sh` and headless runs stop failing.

**Architecture:** Refactor `scry-deepseek`'s single-location `.env` loader into a precedence chain (real env var → `$SCRY_ENV_FILE` → script-dir `.env` → realpath-dir `.env` → `~/.config/scry/.env`), factored into pure, unit-testable helpers. Rewrite the missing-key error to name every location and the `~/.zshrc`-vs-`~/.zshenv` trap. Update README, `.env.example`, and install.sh's closing text.

**Tech Stack:** Python 3.9+ stdlib only (no new deps); `unittest` via the repo's `tests/_harness.py` `SourceFileLoader` pattern.

## Global Constraints

- **Stdlib only.** No new dependencies anywhere (scry is deliberately zero-dependency). Copied verbatim from spec: "No new dependency. Stays stdlib-only; reuses the existing dotenv parser."
- **`~/.config/scry` is hardcoded** like scry core's `_global_config_path` (`Path.home()/".config"/"scry"`); do **not** honor `$XDG_CONFIG_HOME`.
- **Real env var always wins** over any file; among files, first-to-define wins.
- **Key stays scoped to the `scry-deepseek` process** — never load it anywhere `scry` core or the other providers would inherit it.
- **Hermetic tests:** pass a fake `script_path` and point `HOME`/`SCRY_ENV_FILE` at temp paths via `_harness.env_vars()`; never read the repo's real `.env`. Suite command: `python3 -m unittest discover -s tests`.
- Build fake temp paths through `os.path.realpath(...)` so macOS `/var`→`/private/var` doesn't make the realpath candidate spuriously differ from abspath.

---

### Task 1: `.env` resolution — config-dir + override + testable candidates

**Files:**
- Modify: `tests/_harness.py` (add `SCRY_DEEPSEEK` path + `load_scry_deepseek()` loader)
- Modify: `scry-deepseek:27-48` (replace `_load_env_file` with `_config_dir` + `_env_file_candidates` + `_apply_env_file` + new `_load_env_file`)
- Test: `tests/test_deepseek_env.py` (create; `TestEnvResolution`)

**Interfaces:**
- Consumes: `_harness._load`, `_harness.REPO_ROOT`, `_harness.env_vars`.
- Produces (in the `scry-deepseek` module, used by Task 2 and the tests):
  - `_config_dir() -> str` → `~/.config/scry`
  - `_env_file_candidates(script_path: str) -> list[str]`
  - `_apply_env_file(path: str) -> None`
  - `_load_env_file(script_path: str = __file__) -> None`
  - `load_scry_deepseek()` in `_harness` → the loaded module.

- [ ] **Step 1: Add the harness loader**

In `tests/_harness.py`, after the `SCRY_EVAL = REPO_ROOT / "scry-eval"` line (≈line 40) add:

```python
SCRY_DEEPSEEK = REPO_ROOT / "scry-deepseek"
```

After `load_scry_eval()` (≈line 68) add:

```python
def load_scry_deepseek():
    """The `scry-deepseek` adapter loaded as a module (symbols only; main() not run)."""
    return _load("scry_deepseek_sut", SCRY_DEEPSEEK)
```

- [ ] **Step 2: Write the failing resolution tests**

Create `tests/test_deepseek_env.py`:

```python
"""Unit tests for scry-deepseek's API-key / .env resolution.

Hermetic: every test passes a FAKE script_path under a temp dir and points HOME /
SCRY_ENV_FILE at temp paths via env_vars(), so the repo's real .env is never read
and os.environ is always restored. No network, no real DeepSeek call.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402

ds = h.load_scry_deepseek()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class TestEnvResolution(unittest.TestCase):
    def setUp(self):
        # realpath'd temp so abspath == realpath (macOS /var -> /private/var).
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ds-env-"))
        self.script = os.path.join(self.tmp, "bin", "scry-deepseek")
        os.makedirs(os.path.dirname(self.script), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_real_env_var_wins_over_files(self):
        _write(os.path.join(self.tmp, "bin", ".env"), "DEEPSEEK_API_KEY=from-file\n")
        with h.env_vars(DEEPSEEK_API_KEY="from-env", SCRY_ENV_FILE=None, HOME=self.tmp):
            ds._load_env_file(self.script)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "from-env")

    def test_scry_env_file_override(self):
        envf = os.path.join(self.tmp, "custom.env")
        _write(envf, "DEEPSEEK_API_KEY=from-override\n")
        with h.env_vars(DEEPSEEK_API_KEY=None, SCRY_ENV_FILE=envf, HOME=self.tmp):
            ds._load_env_file(self.script)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "from-override")

    def test_config_dir_env(self):
        _write(os.path.join(self.tmp, ".config", "scry", ".env"),
               "DEEPSEEK_API_KEY=from-config\n")
        with h.env_vars(DEEPSEEK_API_KEY=None, SCRY_ENV_FILE=None, HOME=self.tmp):
            ds._load_env_file(self.script)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "from-config")

    def test_script_dir_env_backward_compat(self):
        _write(os.path.join(self.tmp, "bin", ".env"), "DEEPSEEK_API_KEY=from-scriptdir\n")
        with h.env_vars(DEEPSEEK_API_KEY=None, SCRY_ENV_FILE=None, HOME=self.tmp):
            ds._load_env_file(self.script)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "from-scriptdir")

    def test_precedence_scriptdir_beats_config(self):
        _write(os.path.join(self.tmp, "bin", ".env"), "DEEPSEEK_API_KEY=scriptdir\n")
        _write(os.path.join(self.tmp, ".config", "scry", ".env"), "DEEPSEEK_API_KEY=config\n")
        with h.env_vars(DEEPSEEK_API_KEY=None, SCRY_ENV_FILE=None, HOME=self.tmp):
            ds._load_env_file(self.script)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "scriptdir")

    def test_candidates_skip_realpath_when_equal(self):
        with h.env_vars(SCRY_ENV_FILE=None, HOME=self.tmp):
            cands = ds._env_file_candidates(self.script)
        bindir = os.path.join(self.tmp, "bin", ".env")
        self.assertEqual(cands.count(bindir), 1)
        self.assertEqual(cands[-1], os.path.join(self.tmp, ".config", "scry", ".env"))

    def test_nothing_set_leaves_key_unset(self):
        with h.env_vars(DEEPSEEK_API_KEY=None, SCRY_ENV_FILE=None, HOME=self.tmp):
            ds._load_env_file(self.script)
            self.assertIsNone(os.environ.get("DEEPSEEK_API_KEY"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_deepseek_env -v`
Expected: FAIL/ERROR — `module 'scry_deepseek_sut' has no attribute '_env_file_candidates'` (and `_load_env_file()` currently takes no `script_path` arg).

- [ ] **Step 4: Implement the resolution helpers**

In `scry-deepseek`, replace the entire existing `_load_env_file` function (lines 27–48) with:

```python
def _config_dir() -> str:
    """scry's per-user config home — same as scry core's _global_config_path
    (~/.config/scry). Like it, we do NOT honor XDG_CONFIG_HOME, so this .env sits
    beside scry's own config.json."""
    return os.path.join(os.path.expanduser("~"), ".config", "scry")


def _env_file_candidates(script_path: str) -> list:
    """The .env files to try, in precedence order (first to define a key wins).
    A real environment variable still beats all of these (see _apply_env_file)."""
    cands = []
    override = os.environ.get("SCRY_ENV_FILE")
    if override:
        cands.append(override)
    d_abs = os.path.dirname(os.path.abspath(script_path))
    cands.append(os.path.join(d_abs, ".env"))
    d_real = os.path.dirname(os.path.realpath(script_path))
    if d_real != d_abs:
        cands.append(os.path.join(d_real, ".env"))
    cands.append(os.path.join(_config_dir(), ".env"))
    return cands


def _apply_env_file(path: str) -> None:
    """Load KEY=VALUE lines from `path` into os.environ WITHOUT overriding keys
    already set, so an explicit export / CI secret / higher-priority .env wins.
    A missing or unreadable file is a no-op."""
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _load_env_file(script_path: str = __file__) -> None:
    """Populate os.environ from the .env candidates that exist, in precedence
    order: $SCRY_ENV_FILE, then a `.env` next to this script (and its realpath,
    for symlinked installs), then ~/.config/scry/.env. Looked up by the script's
    own path / config dir, never cwd, since scry runs providers from a throwaway
    temp dir. Keeps scry zero-dependency — no python-dotenv. The key lives only in
    this process — it never enters scry's other subprocesses (claude/codex/agy/kimi)."""
    for path in _env_file_candidates(script_path):
        _apply_env_file(path)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_deepseek_env -v`
Expected: PASS (7 tests in `TestEnvResolution`).

- [ ] **Step 6: Run the full suite (no regressions)**

Run: `python3 -m unittest discover -s tests`
Expected: OK (all pre-existing tests still pass; `main()` still calls `_load_env_file()` with its default arg).

- [ ] **Step 7: Commit**

```bash
git add scry-deepseek tests/_harness.py tests/test_deepseek_env.py
git commit -m "scry-deepseek: resolve DEEPSEEK_API_KEY from ~/.config/scry/.env + \$SCRY_ENV_FILE

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Helpful missing-key error message

**Files:**
- Modify: `scry-deepseek` (add module-level `MISSING_KEY_MSG`; use it in `main`, ≈lines 64–68)
- Test: `tests/test_deepseek_env.py` (add `TestMissingKeyMessage`)

**Interfaces:**
- Consumes: `ds.MISSING_KEY_MSG`; `_harness.REPO_ROOT`.
- Produces: `MISSING_KEY_MSG: str` (module-level constant in `scry-deepseek`).

- [ ] **Step 1: Write the failing error-message tests**

Append to `tests/test_deepseek_env.py` (before the `if __name__` block):

```python
class TestMissingKeyMessage(unittest.TestCase):
    def test_message_names_locations_and_zshenv(self):
        msg = ds.MISSING_KEY_MSG
        self.assertIn("~/.config/scry/.env", msg)
        self.assertIn("SCRY_ENV_FILE", msg)
        self.assertIn("~/.zshenv", msg)
        self.assertIn("DEEPSEEK_API_KEY", msg)

    def test_subprocess_missing_key_exits_2(self):
        # Run a COPY of the adapter (so its script-dir has no .env) with a scrubbed
        # env and empty HOME -> no key anywhere -> exit 2 + the guidance message.
        copydir = os.path.realpath(tempfile.mkdtemp(prefix="ds-copy-"))
        try:
            dest = os.path.join(copydir, "scry-deepseek")
            shutil.copy2(h.REPO_ROOT / "scry-deepseek", dest)
            os.chmod(dest, 0o755)
            empty_home = os.path.join(copydir, "home")
            os.makedirs(empty_home)
            env = os.environ.copy()
            env.pop("DEEPSEEK_API_KEY", None)
            env.pop("SCRY_ENV_FILE", None)
            env["HOME"] = empty_home
            r = subprocess.run([sys.executable, dest, "--model", "deepseek-chat"],
                               input="", env=env, capture_output=True, text=True,
                               timeout=30)
            self.assertEqual(r.returncode, 2)
            self.assertIn("~/.config/scry/.env", r.stderr)
            self.assertIn("~/.zshenv", r.stderr)
        finally:
            shutil.rmtree(copydir, ignore_errors=True)
```

Add `import subprocess` to the test file's imports (top of file, after `import shutil`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_deepseek_env.TestMissingKeyMessage -v`
Expected: FAIL — `module 'scry_deepseek_sut' has no attribute 'MISSING_KEY_MSG'`.

- [ ] **Step 3: Implement the message constant + use it**

In `scry-deepseek`, add this module-level constant just above `def main()`:

```python
MISSING_KEY_MSG = (
    "scry-deepseek: DEEPSEEK_API_KEY not set — this provider needs an API key.\n"
    "Get one at https://platform.deepseek.com -> API Keys, then set it via any of:\n"
    "  - ~/.config/scry/.env        (recommended; read in every shell, cron, CI, Claude Code)\n"
    "  - $SCRY_ENV_FILE             (explicit path to a .env)\n"
    "  - a .env next to scry-deepseek\n"
    "  - export DEEPSEEK_API_KEY=... (note: ~/.zshrc is read ONLY by interactive shells,\n"
    "                                 so headless `scry plan` won't see it — use ~/.zshenv)\n"
    'See README: "DeepSeek - the API-key exception".\n'
)
```

Then in `main()` replace the existing missing-key branch (lines 64–68):

```python
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        sys.stderr.write("scry-deepseek: DEEPSEEK_API_KEY not set (this provider needs an "
                         "API key — see README 'DeepSeek — the API-key exception')\n")
        return 2
```

with:

```python
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        sys.stderr.write(MISSING_KEY_MSG)
        return 2
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_deepseek_env -v`
Expected: PASS (all `TestEnvResolution` + `TestMissingKeyMessage` tests).

- [ ] **Step 5: Run the full suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add scry-deepseek tests/test_deepseek_env.py
git commit -m "scry-deepseek: explain where to set the key when it's missing

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Docs — README, .env.example, install.sh closing text

**Files:**
- Modify: `README.md:351-364` (DeepSeek setup block + key-management bullets)
- Modify: `.env.example` (header note after the `cp` line)
- Modify: `install.sh:109` (closing DeepSeek line)

**Interfaces:**
- Consumes: nothing (docs only).
- Produces: nothing code depends on. No test pins this text (verified: no test references the old error string, `.env.example` body, or README DeepSeek prose).

- [ ] **Step 1: Update the README setup block**

In `README.md`, replace the fenced `sh` block (lines 351–357):

```sh
cp .env.example .env && $EDITOR .env                 # add DEEPSEEK_API_KEY=sk-… (gitignored; recommended)
# …or, equivalently, export it in your shell:
export DEEPSEEK_API_KEY=sk-...                        # platform.deepseek.com (metered, pay-as-you-go)
scry --panel "claude:opus,codex,deepseek:deepseek-chat" "..."
scry --check --panel "...,deepseek:deepseek-chat"    # shows: ✓ deepseek installed
```

with:

```sh
# Recommended: keep the key in scry's config dir (a sibling of config.json) — read
# by every shell, cron job, CI run, and Claude Code session:
mkdir -p ~/.config/scry
cp .env.example ~/.config/scry/.env && chmod 600 ~/.config/scry/.env   # then add DEEPSEEK_API_KEY=sk-…
# …or export it — but use ~/.zshenv, NOT ~/.zshrc (see "Shell-rc caveat" below):
export DEEPSEEK_API_KEY=sk-...                        # platform.deepseek.com (metered, pay-as-you-go)
scry --panel "claude:opus,codex,deepseek:deepseek-chat" "..."
scry --check --panel "...,deepseek:deepseek-chat"    # shows: ✓ deepseek installed
```

- [ ] **Step 2: Update the README key-management bullet**

Replace the first bullet (lines 359–362):

```markdown
- **Key management:** put `DEEPSEEK_API_KEY` in a local **`.env`** (copy `.env.example`; it's gitignored —
  never commit it) or `export` it. `scry-deepseek` auto-loads `.env`; real environment variables win. Using
  `.env` keeps the key scoped to the `scry-deepseek` process — the other providers never see it. Keys never
  belong in `config.json`. See [SECURITY.md](SECURITY.md).
```

with:

```markdown
- **Key management.** `scry-deepseek` resolves `DEEPSEEK_API_KEY` in this order (first wins): the real
  env var → `$SCRY_ENV_FILE` → a `.env` next to the adapter (handy in a cloned repo) → `~/.config/scry/.env`
  (the recommended home — a sibling of scry's `config.json`, read regardless of shell type). It's gitignored;
  never commit it, and keep it `chmod 600`. The key is loaded only in the `scry-deepseek` process, so the
  other providers never see it; it never belongs in `config.json`. See [SECURITY.md](SECURITY.md).
- **Shell-rc caveat.** A bare `export DEEPSEEK_API_KEY=…` in `~/.zshrc`/`~/.bashrc` is read **only by
  interactive shells**, so headless runs (the `/scry` and `/scry-plan` Claude Code skills, scripts, cron)
  won't see it. Put the export in `~/.zshenv` (zsh) / `~/.profile` (bash), or — simpler — use
  `~/.config/scry/.env`, which sidesteps shell startup entirely.
```

- [ ] **Step 3: Update `.env.example`**

In `.env.example`, after the line:

```
#   cp .env.example .env   &&   $EDITOR .env
```

insert:

```
# For an INSTALLED scry (curl | sh), this belongs at ~/.config/scry/.env:
#   mkdir -p ~/.config/scry && cp .env.example ~/.config/scry/.env && chmod 600 ~/.config/scry/.env
# A `.env` next to the scry-deepseek script also works (handy in a cloned repo),
# and $SCRY_ENV_FILE can point at an explicit path. Prefer any of these over an
# `export` in ~/.zshrc — that's read only by interactive shells, so headless
# `scry plan` (Claude Code / cron) won't see it; use ~/.zshenv instead.
```

- [ ] **Step 4: Update install.sh's closing line**

In `install.sh`, replace line 109:

```sh
printf 'For DeepSeek, also set \033[1mDEEPSEEK_API_KEY\033[0m (see .env.example).\n'
```

with:

```sh
printf 'For DeepSeek, set \033[1mDEEPSEEK_API_KEY\033[0m in \033[1m~/.config/scry/.env\033[0m (see .env.example).\n'
```

- [ ] **Step 5: Sanity-check + run the full suite**

Run: `python3 -m unittest discover -s tests`
Expected: OK (docs-only changes; nothing pins this text).

Run: `sh -n install.sh && grep -n "config/scry/.env" README.md .env.example install.sh`
Expected: install.sh parses; the grep shows the new path in all three files.

- [ ] **Step 6: Commit**

```bash
git add README.md .env.example install.sh
git commit -m "docs: point DeepSeek key setup at ~/.config/scry/.env + the shell-rc caveat

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

- **Spec coverage:**
  - Config-dir resolution + `$SCRY_ENV_FILE` + realpath + script-dir precedence → Task 1.
  - "Real env var always wins; first-file-wins" → Task 1 (`_apply_env_file` "don't override" + candidate order); tested by `test_real_env_var_wins_over_files`, `test_precedence_scriptdir_beats_config`.
  - Testability via fake `script_path` + `_harness` loader → Task 1 Steps 1–2.
  - Missing-key error names locations + `~/.zshenv` trap → Task 2.
  - README + `.env.example` + install.sh text → Task 3.
  - Non-goals honored: no installer behavior change (only its closing `printf` text), no auto-scaffolding, no perm enforcement, no scry-core/other-provider change, no new dep.
- **Placeholder scan:** none — every code/test/doc step shows complete content.
- **Type consistency:** `_config_dir`, `_env_file_candidates`, `_apply_env_file`, `_load_env_file`, `MISSING_KEY_MSG`, and `load_scry_deepseek` are named identically wherever referenced across tasks and tests.
