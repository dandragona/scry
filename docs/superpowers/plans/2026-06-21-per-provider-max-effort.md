# Per-provider max reasoning effort — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each reasoning-capable provider a top-tier `effort` default so every model runs at its maximum reasoning depth across all pipeline phases.

**Architecture:** A per-provider `effort` field (parallel to the `model` field already shipped), resolved by a one-line fallback in `render_call` (`eff = settings.get("effort") or p.get("effort")`). claude/codex render it as existing CLI flags; deepseek renders it as a new `--reasoning-effort` arg that the `scry-deepseek` adapter translates into the request body (`reasoning_effort` + `thinking`).

**Tech Stack:** Python 3 (stdlib only). `unittest` suite (`python3 -m unittest discover -s tests`). Executables `scry` + `scry-deepseek` loaded as modules via `tests/_harness.py`.

## Global Constraints

- Stdlib only — no new dependencies.
- Tests never spawn a real model CLI or hit the network (harness stubs / direct function calls).
- `config.json` MUST mirror `DEFAULT_CONFIG` (tests load `config.json` via `h.CONFIG_JSON`).
- Effort resolution precedence (first non-empty wins): `settings.effort` (global/phase/`--effort`) → `providers[p].effort` → CLI default (omit).
- Pinned effort values, verbatim: claude `max`, codex `xhigh`, deepseek `max`. (agy/kimi get NO `effort` field — already maxed by other means.)
- DeepSeek body: send top-level `reasoning_effort` and top-level `thinking: {"type":"enabled"}`, gated OFF for non-thinking models (`deepseek-chat`).
- This work folds into the `dandragona/top-models-per-provider` branch (PR #28).
- Full suite green before each commit.

---

### Task 1: Per-provider `effort` field + resolution (claude + codex)

Add the `effort` field to claude/codex, the one-line resolution in `render_call`, and tests. deepseek is handled in Task 2.

**Files:**
- Modify: `scry` — `render_call` effort line; `DEFAULT_CONFIG.providers.claude/codex` (add `effort`).
- Modify: `config.json` — mirror claude/codex `effort`.
- Test: `tests/test_render_call.py`.

**Interfaces:**
- Consumes: `render_call(p, model, system, web, settings, outfile, …)`; `caps.effort` templates (claude `["--effort","{effort}"]`, codex `["-c","model_reasoning_effort={effort}"]`).
- Produces: provider records carry `"effort"`; `render_call` resolves `eff = settings.get("effort") or p.get("effort")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_render_call.py` (after `TestModelResolution`), and **delete** the existing `TestClaude.test_effort_absent_by_default` (it asserts claude has no effort by default — now false; the new `test_no_effort_anywhere_omits` replaces its coverage):

```python
# --------------------------------------------------------------------------- #
# per-provider effort resolution: settings effort -> provider effort -> omit
# --------------------------------------------------------------------------- #
class TestEffortResolution(_Base):
    def test_claude_provider_effort_default(self):
        p = self.prov("claude")  # provider effort "max"
        argv, _ = self.scry.render_call(
            p, "opus", None, True, self.settings(), "/tmp/out")
        self.assertTrue(_contains_seq(argv, ["--effort", "max"]))

    def test_codex_provider_effort_default(self):
        p = self.prov("codex")  # provider effort "xhigh"
        argv, _ = self.scry.render_call(
            p, "", None, True, self.settings(), "/tmp/out.txt")
        self.assertTrue(_contains_seq(argv, ["-c", "model_reasoning_effort=xhigh"]))

    def test_settings_effort_overrides_provider(self):
        p = self.prov("claude")  # provider effort "max"
        argv, _ = self.scry.render_call(
            p, "opus", None, True, self.settings(effort="low"), "/tmp/out")
        self.assertTrue(_contains_seq(argv, ["--effort", "low"]))
        self.assertFalse(_contains_seq(argv, ["--effort", "max"]))

    def test_no_effort_anywhere_omits(self):
        # provider has an effort cap but no effort value, and no settings effort
        p = self.prov("claude")
        p.pop("effort", None)
        argv, _ = self.scry.render_call(
            p, "opus", None, True, self.settings(), "/tmp/out")
        self.assertNotIn("--effort", argv)
        # a provider with no effort cap at all (agy) also renders nothing
        argv2, _ = self.scry.render_call(
            self.prov("agy"), "m", None, True, self.settings(), "/tmp/out")
        self.assertNotIn("--effort", argv2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_render_call -v`
Expected: FAIL — `test_claude_provider_effort_default` / `test_codex_provider_effort_default` fail (no provider `effort` yet; resolution reads only settings).

- [ ] **Step 3: Change the `render_call` resolution**

In `scry`, `render_call`, find:

```python
    eff = settings.get("effort")
```

Replace with:

```python
    # Per-provider default: a provider's effort applies unless a global/phase/--effort
    # setting overrides it (parallel to the model fallback above).
    eff = settings.get("effort") or p.get("effort")
```

- [ ] **Step 4: Add `effort` to claude + codex provider records**

In `scry`, `DEFAULT_CONFIG["providers"]`, add `"effort"` next to the existing `"model"`:
- claude record: `"effort": "max",`
- codex record: `"effort": "xhigh",`

- [ ] **Step 5: Mirror into `config.json`**

In `config.json`, add the same `"effort"` to the claude and codex provider records (next to their `"model"`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_render_call -v`
Expected: PASS (new `TestEffortResolution` + existing `test_effort_present_when_set` / codex `test_effort`, which pass effort via settings and still win).

- [ ] **Step 7: Commit**

```bash
git add scry config.json tests/test_render_call.py
git commit -m "Add per-provider effort default (claude max, codex xhigh)

render_call falls back to the provider's effort when no global/phase/--effort
is set; applies across all phases.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: DeepSeek reasoning effort (adapter + provider)

Add deepseek's `effort` field + a `--reasoning-effort` cap, and teach `scry-deepseek` to translate it into the request body (`reasoning_effort` + `thinking`), gated to thinking-capable models.

**Files:**
- Modify: `scry-deepseek` — `--reasoning-effort` arg, `NON_THINKING_MODELS`, `thinking_payload`, payload merge.
- Modify: `scry` — `DEFAULT_CONFIG.providers.deepseek` add `"effort": "max"` + `caps.effort`.
- Modify: `config.json` — mirror deepseek `effort` + `caps.effort`.
- Test: `tests/test_deepseek_adapter.py`, `tests/test_render_call.py`.

**Interfaces:**
- Consumes: the `render_call` resolution from Task 1; `resolve_max_tokens` (already in `scry-deepseek`).
- Produces: `thinking_payload(model, effort) -> dict`; `NON_THINKING_MODELS` (set); deepseek `caps.effort = ["--reasoning-effort","{effort}"]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_deepseek_adapter.py`:

```python
class ThinkingPayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = h.load_scry_deepseek()

    def test_v4_pro_max_enables_thinking(self):
        self.assertEqual(
            self.ds.thinking_payload("deepseek-v4-pro", "max"),
            {"reasoning_effort": "max", "thinking": {"type": "enabled"}})

    def test_reasoner_alias_enables_thinking(self):
        self.assertEqual(
            self.ds.thinking_payload("deepseek-reasoner", "max"),
            {"reasoning_effort": "max", "thinking": {"type": "enabled"}})

    def test_non_thinking_model_gated(self):
        self.assertEqual(self.ds.thinking_payload("deepseek-chat", "max"), {})

    def test_no_effort_returns_empty(self):
        self.assertEqual(self.ds.thinking_payload("deepseek-v4-pro", None), {})
```

Add to `tests/test_render_call.py` `TestEffortResolution`:

```python
    def test_deepseek_provider_effort_renders(self):
        p = self.prov("deepseek")  # provider effort "max" + caps.effort
        argv, _ = self.scry.render_call(
            p, "deepseek-v4-pro", "SYS", True, self.settings(), "/tmp/out")
        self.assertTrue(_contains_seq(argv, ["--reasoning-effort", "max"]))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_deepseek_adapter tests.test_render_call -v`
Expected: FAIL — `thinking_payload` doesn't exist; deepseek renders no `--reasoning-effort` (no cap yet).

- [ ] **Step 3: Implement `thinking_payload` + arg in `scry-deepseek`**

In `scry-deepseek`, after the `resolve_max_tokens` block, add:

```python
# Models that cannot accept reasoning_effort/thinking (V4-Flash non-thinking).
NON_THINKING_MODELS = {"deepseek-chat"}


def thinking_payload(model, effort):
    """Reasoning fields to merge into the request body for a thinking-capable model:
    `reasoning_effort` and `thinking` go at the TOP LEVEL of the raw body (the OpenAI
    SDK's extra_body flattens here). Returns {} when no effort is requested or the
    model can't think, so a non-thinking model is never sent fields it would reject."""
    if not effort or model in NON_THINKING_MODELS:
        return {}
    return {"reasoning_effort": effort, "thinking": {"type": "enabled"}}
```

Add the argument (next to `--max-tokens`):

```python
    ap.add_argument("--reasoning-effort", default=None,
                    help="DeepSeek reasoning effort for thinking models (high|max)")
```

Merge into the payload (extend the existing dict literal):

```python
    payload = {"model": args.model, "messages": messages, "stream": False,
               "max_tokens": resolve_max_tokens(args.max_tokens, args.model),
               **thinking_payload(args.model, args.reasoning_effort)}
```

- [ ] **Step 4: Add deepseek `effort` + `caps.effort` in `scry`**

In `scry`, `DEFAULT_CONFIG["providers"]["deepseek"]`: add `"effort": "max",` (next to `"model"`), and set its caps from `{}` to:

```python
            "caps": {"effort": ["--reasoning-effort", "{effort}"]},
```

- [ ] **Step 5: Mirror into `config.json`**

In `config.json`, deepseek provider: add `"effort": "max"` and change `"caps": {}` to `"caps": { "effort": ["--reasoning-effort", "{effort}"] }`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_deepseek_adapter tests.test_render_call -v`
Expected: PASS (4 `ThinkingPayloadTest` + `test_deepseek_provider_effort_renders`).

- [ ] **Step 7: Run the full suite**

Run: `python3 -m unittest discover -s tests`
Expected: all green. The dry-run header still shows `effort=None` (global setting unchanged); proposer command lines now carry the effort flags, which no dry-run test asserts absent. If any test hard-codes a deepseek/claude/codex proposer command line without effort, align it (don't weaken). Re-run until green.

- [ ] **Step 8: Commit**

```bash
git add scry scry-deepseek config.json tests/test_deepseek_adapter.py tests/test_render_call.py
git commit -m "Add DeepSeek max reasoning effort via the adapter body

deepseek provider effort=max renders --reasoning-effort, which the adapter
translates into top-level reasoning_effort + thinking (gated off for the
non-thinking deepseek-chat).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Docs

Document per-provider effort (claude `max`, codex `xhigh`, deepseek `max`; agy+kimi already maxed; applies to all phases; costs more).

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `config.json` (notes).

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update `README.md`**

Add a short "reasoning effort" note in the providers/settings area: each provider declares a top-tier `effort` that all phases inherit (overridable with `--effort`); claude `max`, codex `xhigh`, deepseek `max` (sent as `reasoning_effort` + `thinking` by the adapter); agy is already maxed via its model name and kimi runs thinking-on by default; note it raises latency/cost. READ the surrounding provider docs first and match voice/format.

- [ ] **Step 2: Update `config.json` notes**

In `config.json`, note in the `settings`/`deepseek` `_note`s that `effort` is now a per-provider default (claude `max` / codex `xhigh` / deepseek `max`), and that deepseek's effort is rendered as `--reasoning-effort` → `reasoning_effort` + `thinking` in the API body.

- [ ] **Step 3: Add a `CHANGELOG.md` entry**

Under `### Added`, describe per-provider max reasoning effort (claude `max`, codex `xhigh`, deepseek `max` via the adapter body; agy/kimi already maxed).

- [ ] **Step 4: Verify the suite is still green**

Run: `python3 -m unittest discover -s tests`
Expected: all green (docs-only).

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md config.json
git commit -m "Docs: per-provider max reasoning effort

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Per-provider `effort` field + resolution + precedence → Task 1 (one-liner, claude/codex). ✓
- Pinned values claude `max` / codex `xhigh` / deepseek `max` → Tasks 1 (claude/codex), 2 (deepseek). ✓
- deepseek adapter `reasoning_effort` + `thinking`, top-level, gated to thinking models → Task 2. ✓
- deepseek `caps.effort` + `--reasoning-effort` → Task 2. ✓
- All phases at max (automatic via render_call) → covered by the resolution change in Task 1; no phase edits needed (stated). ✓
- `config.json` mirror → Tasks 1, 2, 3. ✓
- Update stale `test_effort_absent_by_default` → Task 1 Step 1. ✓
- Docs + cost/codex-reset/body-shape caveats → Task 3 + spec risks (verification at execution). ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; the only catch-all (Task 2 Step 7) is a bounded full-suite reconciliation with the expected outcome stated.

**Type consistency:** `thinking_payload(model, effort)`, `NON_THINKING_MODELS`, `resolve_max_tokens`, `caps.effort` template `["--reasoning-effort","{effort}"]`, and the effort values (`max`/`xhigh`/`max`) are named identically across the defining task, its tests, and `config.json`. The `render_call` resolution name (`eff = settings.get("effort") or p.get("effort")`) matches the spec.

## Notes for the implementer

- Argparse maps `--reasoning-effort` to `args.reasoning_effort` (underscore).
- The body-shape risk (top-level `thinking`) and the codex effort-reset bug are real-call verification items, not blockers — confirm at verification, noted in the spec.
