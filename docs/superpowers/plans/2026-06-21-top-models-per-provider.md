# Top model per provider + DeepSeek off-spec fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each provider's top model a single visible/swappable config field, run all five providers at top tier by default, and stop the DeepSeek adapter from silently truncating output at 4,096 tokens.

**Architecture:** Add a per-provider `model` default resolved with a one-line fallback in `render_call` (member model → provider model → CLI default). Pin top models in `DEFAULT_CONFIG` + `config.json`, expand the default panel to all five, and give the `scry-deepseek` adapter a per-model max-tokens default.

**Tech Stack:** Python 3 (stdlib only), `unittest` test suite (`python3 -m unittest discover -s tests`). Two extension-less executables: `scry` and `scry-deepseek`, loaded as modules in tests via `_harness`.

## Global Constraints

- Stdlib only — no new dependencies (both executables and the tests are zero-dependency).
- Tests never spawn a real model CLI or spend money — use `_harness` stubs / monkeypatched `call_cli` / direct function calls.
- The reference `config.json` must mirror `DEFAULT_CONFIG`'s shape (tests load `config.json` via `h.CONFIG_JSON`).
- `load_config` does a **shallow** `cfg.update()` — only `settings`/`plan`/`phases` are backfilled; `providers`/`panel` are not deep-merged. Do not rely on provider-`model` backfill for user configs.
- Pinned top models (verified against installed CLIs): claude `opus`, codex `gpt-5.5`, agy `Gemini 3.1 Pro (High)`, deepseek `deepseek-v4-pro`, kimi `K2.7`.
- Resolution precedence (first non-empty wins): member `model` → `providers[provider].model` → CLI default (flag omitted).
- Run the full suite green before the final commit (currently 525 hermetic tests).

---

### Task 1: Per-provider `model` field + resolution

Add a canonical `model` to each provider record; make `render_call` fall back to it so panel members / judge / aggregator can omit `model` and inherit. Pin the five top models. Panel stays 3 members in this task (expanded in Task 3).

**Files:**
- Modify: `scry` — `render_call` (`scry:490`, the single argv choke point), `DEFAULT_CONFIG.providers.*` (add `model`), `DEFAULT_CONFIG.panel`/`judge`/`aggregator` (drop inline `model`).
- Modify: `config.json` — mirror provider `model` fields; drop inline `model` from `panel`/`judge`/`aggregator`.
- Test: `tests/test_render_call.py`.

**Interfaces:**
- Consumes: `render_call(p, model, system, web, settings, outfile, stream=False, agentfile="")` → `(argv, env_overrides)`; provider records `cfg["providers"][name]`.
- Produces: provider records now carry a top-level `"model"` string; `render_call` resolves `model = model or p.get("model", "")` before building the `--model` pair.

- [ ] **Step 1: Write the failing tests**

Add a new class to `tests/test_render_call.py` (after `TestKimi`), and **delete** the now-contradictory `TestClaude.test_empty_model_no_model_flag` (lines ~140-144 — its old assertion that empty model omits `--model` is replaced by `test_no_model_anywhere_omits_flag` below):

```python
# --------------------------------------------------------------------------- #
# per-provider model resolution: member model -> provider model -> CLI default
# --------------------------------------------------------------------------- #
class TestModelResolution(_Base):
    def test_empty_member_inherits_provider_model(self):
        # config.json gives claude a default model "opus"; an empty member model
        # inherits it instead of omitting the flag.
        p = self.prov("claude")
        argv, _ = self.scry.render_call(
            p, "", None, True, self.settings(), "/tmp/out")
        self.assertTrue(_adjacent(argv, "--model", "opus"))

    def test_explicit_member_model_overrides_provider(self):
        p = self.prov("claude")  # provider default "opus"
        argv, _ = self.scry.render_call(
            p, "sonnet", None, True, self.settings(), "/tmp/out")
        self.assertTrue(_adjacent(argv, "--model", "sonnet"))

    def test_no_model_anywhere_omits_flag(self):
        p = self.prov("claude")
        p.pop("model", None)  # neither member nor provider declares a model
        argv, _ = self.scry.render_call(
            p, "", None, True, self.settings(), "/tmp/out")
        self.assertNotIn("--model", argv)

    def test_deepseek_provider_default_is_v4_pro(self):
        p = self.prov("deepseek")
        argv, _ = self.scry.render_call(
            p, "", "SYS", True, self.settings(), "/tmp/out")
        self.assertTrue(_adjacent(argv, "--model", "deepseek-v4-pro"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_render_call -v`
Expected: FAIL — `test_empty_member_inherits_provider_model` / `test_deepseek_provider_default_is_v4_pro` fail (provider has no `model` yet; no fallback), `--model opus` absent.

- [ ] **Step 3: Add the `render_call` fallback**

In `scry`, `render_call` (`scry:490`), make the resolution the first statement of the body, immediately after the docstring and before `argv = list(p["cmd"])`:

```python
    # Per-provider default: an empty member model inherits the provider's top model
    # (one swap-point per provider); an explicit member model still wins.
    model = model or p.get("model", "")
    argv = list(p["cmd"])
```

- [ ] **Step 4: Pin provider models + make members inherit (`scry` DEFAULT_CONFIG)**

In `scry`, add a top-level `"model"` to each provider record in `DEFAULT_CONFIG["providers"]`:

| provider | line (record start) | add |
|---|---|---|
| claude | ~`scry:211` | `"model": "opus",` |
| codex | `scry:256` | `"model": "gpt-5.5",` |
| agy | `scry:282` | `"model": "Gemini 3.1 Pro (High)",` |
| deepseek | `scry:301` | `"model": "deepseek-v4-pro",` |
| kimi | `scry:327` | `"model": "K2.7",` |

Place `"model"` right after the record's opening `"cmd": [...]` line in each. Then change the panel/judge/aggregator (`scry:361-368`) to inherit (drop inline `model`):

```python
    "panel": [
        {"provider": "claude", "label": "claude-opus"},
        {"provider": "codex",  "label": "codex-gpt"},
        {"provider": "agy",    "label": "gemini-pro"},
    ],
    "judge":      {"provider": "claude"},
    "aggregator": {"provider": "claude"},
```

- [ ] **Step 5: Mirror into `config.json`**

In `config.json`, add the same `"model"` to each provider record (claude `scry`→`config.json:26`, codex `:66`, agy `:87`, deepseek `:104`, kimi `:119`) and update `panel`/`judge`/`aggregator` (`config.json:151-158`) to drop inline `model` exactly as in Step 4.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_render_call -v`
Expected: PASS (all `TestModelResolution` + the existing claude/codex/agy/kimi cases — codex now gets `-m gpt-5.5`, asserted tails unchanged).

- [ ] **Step 7: Commit**

```bash
git add scry config.json tests/test_render_call.py
git commit -m "Add per-provider model default with member-inherits fallback

Each provider record carries its top model; panel/judge/aggregator inherit
via render_call (member model -> provider model -> CLI default). Pins
opus/gpt-5.5/Gemini 3.1 Pro (High)/deepseek-v4-pro/K2.7.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: DeepSeek max-tokens fix (`scry-deepseek`)

Stop the adapter inheriting DeepSeek's silent 4,096-token output default. When `--max-tokens` is not passed, request the model's documented maximum.

**Files:**
- Modify: `scry-deepseek` — add `resolve_max_tokens` + the model→max map; always set `max_tokens` in the payload (`scry-deepseek:79-81`).
- Modify: `tests/_harness.py` — add a `load_scry_deepseek()` loader.
- Test: `tests/test_deepseek_adapter.py` (new).

**Interfaces:**
- Consumes: `args.max_tokens` (int|None), `args.model` (str).
- Produces: `resolve_max_tokens(explicit, model) -> int`; `DEEPSEEK_MAX_OUTPUT` (dict), `DEFAULT_MAX_OUTPUT` (int); `h.load_scry_deepseek()` returning the adapter module.

- [ ] **Step 1: Add the `_harness` loader**

In `tests/_harness.py`, beside `SCRY_EVAL` (`_harness.py:40`) add:

```python
SCRY_DEEPSEEK = REPO_ROOT / "scry-deepseek"
```

and beside `load_scry_eval` (`_harness.py:66`) add:

```python
def load_scry_deepseek():
    """The `scry-deepseek` adapter loaded as a module (symbols only)."""
    return _load("scry_deepseek_sut", SCRY_DEEPSEEK)
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_deepseek_adapter.py`:

```python
"""Unit tests for the scry-deepseek adapter's max-tokens resolution.

DeepSeek's chat API silently caps output at 4096 tokens when max_tokens is
omitted, truncating long answers (e.g. a `scry plan` draft). resolve_max_tokens
requests the model's documented maximum instead. Pure function — no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class ResolveMaxTokensTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = h.load_scry_deepseek()

    def test_v4_pro_uses_full_max(self):
        self.assertEqual(
            self.ds.resolve_max_tokens(None, "deepseek-v4-pro"), 384000)

    def test_legacy_aliases_cap_at_8192(self):
        self.assertEqual(
            self.ds.resolve_max_tokens(None, "deepseek-chat"), 8192)
        self.assertEqual(
            self.ds.resolve_max_tokens(None, "deepseek-reasoner"), 8192)

    def test_unknown_model_falls_back(self):
        self.assertEqual(
            self.ds.resolve_max_tokens(None, "something-new"), 8192)

    def test_explicit_value_wins(self):
        self.assertEqual(
            self.ds.resolve_max_tokens(1000, "deepseek-v4-pro"), 1000)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_deepseek_adapter -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'resolve_max_tokens'`.

- [ ] **Step 4: Implement the resolver in `scry-deepseek`**

In `scry-deepseek`, after the imports (`scry-deepseek:24`, before `_load_env_file`), add:

```python
# DeepSeek's chat API silently defaults max_tokens to 4096, truncating long
# answers. We instead request the chosen model's documented maximum output so
# nothing is cut off — a ceiling is free (the model stops when done).
DEEPSEEK_MAX_OUTPUT = {
    "deepseek-v4-pro": 384000,     # V4 family: 1M context, 384K max output
    "deepseek-chat": 8192,         # legacy alias -> V4-Flash (non-thinking)
    "deepseek-reasoner": 8192,     # legacy alias -> V4-Flash (thinking)
}
DEFAULT_MAX_OUTPUT = 8192          # safe documented ceiling for unknown ids


def resolve_max_tokens(explicit, model):
    """Output-token ceiling to request: an explicit --max-tokens if given, else
    the model's documented maximum (never the API's silent 4096 default)."""
    if explicit:
        return explicit
    return DEEPSEEK_MAX_OUTPUT.get(model, DEFAULT_MAX_OUTPUT)
```

Then replace the payload's conditional max-tokens (`scry-deepseek:79-81`):

```python
    payload = {"model": args.model, "messages": messages, "stream": False,
               "max_tokens": resolve_max_tokens(args.max_tokens, args.model)}
```

(Delete the old `if args.max_tokens: payload["max_tokens"] = args.max_tokens`.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_deepseek_adapter -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add scry-deepseek tests/_harness.py tests/test_deepseek_adapter.py
git commit -m "Fix scry-deepseek silent truncation: request model max output

The DeepSeek chat API caps output at 4096 tokens when max_tokens is omitted,
truncating long answers. resolve_max_tokens requests the model's documented
maximum (deepseek-v4-pro: 384K) unless --max-tokens overrides.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Default panel = all five providers at top tier

Add `deepseek` + `kimi` to the shipped default panel (models inherited from Task 1). Add a DeepSeek stub so any subprocess path has it, and fix the two hardcoded panel counts in `test_dry_run`.

**Files:**
- Modify: `scry` — `DEFAULT_CONFIG.panel` (`scry:361`).
- Modify: `config.json` — `panel` (`config.json:151`).
- Modify: `tests/_harness.py` — add `deepseek_text` stub + include it in `default_stubs()`.
- Test: `tests/test_dry_run.py` (count assertions).

**Interfaces:**
- Consumes: provider records + `render_call` from Task 1; `StubBins`/`default_stubs` from `_harness`.
- Produces: `default_stubs()` now includes a `"scry-deepseek"` key; default panel has 5 members.

- [ ] **Step 1: Update the failing count assertions in `test_dry_run.py`**

In `tests/test_dry_run.py`, change the two hardcoded proposer counts from `3` to `5`:

- `test_fusion_default_panel_structure` (line ~44):

```python
        # 5 proposers (claude/codex/agy/deepseek/kimi), one judge, one aggregator.
        self.assertEqual(len(_lines(out, "PROPOSER")), 5, out)
```

- `test_synthesize_mode_omits_judge` (line ~87):

```python
        self.assertEqual(len(_lines(out, "PROPOSER")), 5, out)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_dry_run -v`
Expected: FAIL — both expect 5 but the panel is still 3.

- [ ] **Step 3: Expand the default panel (`scry` + `config.json`)**

In `scry`, `DEFAULT_CONFIG["panel"]` (`scry:361`):

```python
    "panel": [
        {"provider": "claude", "label": "claude-opus"},
        {"provider": "codex",  "label": "codex-gpt"},
        {"provider": "agy",    "label": "gemini-pro"},
        # deepseek is knowledge-only (no web search) — a voice without live grounding.
        {"provider": "deepseek", "label": "deepseek"},
        {"provider": "kimi",   "label": "kimi"},
    ],
```

Mirror the identical 5-member list into `config.json` `panel` (`config.json:151`).

- [ ] **Step 4: Add the DeepSeek stub to `_harness`**

In `tests/_harness.py`, after `kimi_text` (`_harness.py:234`):

```python
def deepseek_text(result: str = "DEEPSEEK ANSWER") -> str:
    """scry-deepseek: read stdin, print the assistant message as plain text
    (capture='text'). The stub ignores --model/--system/--max-tokens flags."""
    return _py("import sys\n" "sys.stdin.read()\n"
               f"sys.stdout.write({result!r} + '\\n')\n")
```

and add it to `default_stubs()` (`_harness.py:264`) — note the key is the command name `scry-deepseek` (resolved on PATH first by `resolve_command`):

```python
def default_stubs() -> dict:
    return {
        "claude": claude_json("CLAUDE ANSWER"),
        "codex": codex_outfile("CODEX ANSWER"),
        "agy": agy_text("GEMINI ANSWER"),
        "kimi": kimi_text("KIMI ANSWER"),
        "scry-deepseek": deepseek_text("DEEPSEEK ANSWER"),
    }
```

- [ ] **Step 5: Run to verify dry-run passes**

Run: `python3 -m unittest tests.test_dry_run -v`
Expected: PASS (the kimi/agy/claude line-finder tests still find their members among the 5).

- [ ] **Step 6: Run the FULL suite and fix residual fallout**

Run: `python3 -m unittest discover -s tests`
Expected: all green. `test_scry_run` (mocked `call_cli`, tracks `len(self.panel)`) and `test_check` (`n_panel = len(cfg["panel"])`, dynamic) adapt automatically. If any test hard-codes the old 3-member panel or the pre-`model` provider shape, update that assertion to match the new default (5 members / inherited top models) — do not weaken a test, align it with the new shipped default. Re-run until green.

- [ ] **Step 7: Commit**

```bash
git add scry config.json tests/_harness.py tests/test_dry_run.py
git commit -m "Default panel = all five providers at top tier

Add deepseek + kimi to the shipped panel (models inherited from the provider
records). Add a scry-deepseek test stub; update dry-run panel counts.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Docs

Update user-facing docs to describe the per-provider `model` field, the five-member top-tier panel, and the DeepSeek no-truncation behavior.

**Files:**
- Modify: `README.md` (providers/panel sections; the DeepSeek "API-key exception" note).
- Modify: `config.json` (`deepseek` `_note`, `_harness` not involved).
- Modify: `CHANGELOG.md`.

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update `config.json` deepseek `_note`**

In `config.json` (`config.json:105`), revise the `deepseek` `_note` to state it now defaults to the top tier and no longer truncates:

```
"_note": "API-KEY EXCEPTION (testing only). DeepSeek has no subscription CLI, so the scry-deepseek adapter (a sibling script resolved next to scry) calls DeepSeek's OpenAI-compatible API and needs DEEPSEEK_API_KEY. Knowledge-only (no web search). Defaults to the top tier 'deepseek-v4-pro' via the provider `model` field; the legacy 'deepseek-chat'/'deepseek-reasoner' aliases route to V4-Flash. The adapter requests the model's documented max output (V4: 384K) so long answers are never silently truncated at the API's 4096 default; override with --max-tokens.",
```

- [ ] **Step 2: Update `README.md`**

In `README.md`, in the providers/panel documentation: (a) note each provider declares a top-tier `model` that panel/judge/aggregator inherit (one swap-point per provider; an explicit member `model` overrides); (b) the default panel is now all five providers at top tier (claude `opus`, codex `gpt-5.5`, agy `Gemini 3.1 Pro (High)`, deepseek `deepseek-v4-pro`, kimi `K2.7`), flagging that deepseek is knowledge-only (no web); (c) in the DeepSeek "API-key exception" section, note the adapter requests the model's max output so answers aren't truncated.

- [ ] **Step 3: Add a `CHANGELOG.md` entry**

At the top of `CHANGELOG.md`, add an entry describing: per-provider `model` field with member-inherits resolution; default panel expanded to all five providers at top tier; DeepSeek adapter requests the model's max output (no more 4096 truncation).

- [ ] **Step 4: Verify the suite is still green**

Run: `python3 -m unittest discover -s tests`
Expected: all green (docs-only changes, no behavior impact).

- [ ] **Step 5: Commit**

```bash
git add README.md config.json CHANGELOG.md
git commit -m "Docs: per-provider model field, 5-provider panel, DeepSeek no-truncation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Per-provider `model` field + resolution + precedence → Task 1 (render_call fallback, provider models, inherit-by-omission). ✓
- Pinned top models table → Task 1 Step 4. ✓
- DeepSeek truncation fix (adapter, model→max map, override) → Task 2. ✓
- Default panel = all five → Task 3. ✓
- `config.json` mirror → Tasks 1, 3, 4. ✓
- Backward-compat shallow-merge note → Global Constraints (no provider-`model` backfill relied upon). ✓
- Risks: graceful-skip → covered by Task 3 Step 6 full-suite run (`test_check` dynamic) + the shipped-default note; exact-ID confirmation → Global Constraints lists verified strings; cost/latency → accepted (design). ✓
- Docs (README/CHANGELOG/config note) → Task 4. ✓
- Live `~/.config/scry/config.json` update → intentionally out of the committed plan (offered separately; it's the user's machine config, not repo code).

**Placeholder scan:** No TBD/TODO; every code step shows complete code; the only catch-all (Task 3 Step 6) is a deliberate full-suite reconciliation for a shared-config change, with the dynamic-vs-static behavior of each affected test spelled out.

**Type consistency:** `resolve_max_tokens(explicit, model)`, `DEEPSEEK_MAX_OUTPUT`, `DEFAULT_MAX_OUTPUT`, `load_scry_deepseek()`, `deepseek_text()` are named identically in their defining task and their test/consumer. `render_call` signature unchanged; the `model = model or p.get("model","")` resolution name matches across Task 1 and its tests.

## Notes for the implementer

- The DeepSeek 384K max is from current DeepSeek docs. If `deepseek-v4-pro`'s API rejects 384000 with a 400, lower the map value to the documented ceiling — it's a safety, not a target.
- `gpt-5.5` and `K2.7` were read from `~/.codex/config.toml` and `~/.kimi` logs on the author's machine. If a CLI rejects the string as a `--model` value, re-pin to the accepted form (the provider `model` field is the single place to change it).
