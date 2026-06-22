# Per-provider max reasoning effort

**Date:** 2026-06-21
**Status:** Approved (design)
**Scope:** `scry` CLI + `scry-deepseek` adapter — give each provider a top-tier `effort`
default (parallel to the `model` field), so every reasoning-capable provider runs at its
maximum. Folds into the `dandragona/top-models-per-provider` branch (PR #28).

## Motivation

After pinning each provider's top *model*, reasoning *effort* is still unset (`effort: None`
→ each CLI's own default). Maxing effort raises answer quality on hard planning/coding tasks,
at the cost of latency and spend (accepted). The lever is real for three providers; two are
already maxed by other means:

| provider | effort lever | max value | today |
|---|---|---|---|
| claude | `--effort <level>` | `max` | unset (CLI default) |
| codex | `-c model_reasoning_effort=<level>` | `xhigh` | medium (user config) |
| deepseek | API body `reasoning_effort` + `thinking` | `max` | **not sent** → default depth |
| agy | baked into model name | — | already max (`Gemini 3.1 Pro (High)`) |
| kimi | none (thinking always on, can't disable on K2.7) | — | already max by default |

Verified values: claude `--effort` accepts `low|medium|high|xhigh|max`; codex
`model_reasoning_effort` accepts `none|minimal|low|medium|high|xhigh`; DeepSeek V4 `reasoning_effort`
accepts `high|max` (and maps `xhigh`→`max`). Sources: `claude --help`, DeepSeek thinking-mode docs.

## Goals

- A per-provider `effort` default, resolved with the same precedence pattern as `model`.
- Every pipeline phase (panel, judge, synthesis, plan interview, plan final draft) runs at the
  provider's max effort, unless a global/phase/`--effort` value overrides it.
- DeepSeek actually runs at max reasoning depth (currently it does not).

## Non-goals

- Per-phase effort tuning (a phase *can* override via the existing `effort` phase key, but we
  ship no phase overrides — all phases inherit the provider default).
- Effort knobs for agy/kimi (none exist / already maxed).
- Touching `max_output_tokens` / `max_tool_calls` (out of scope; `max_tool_calls` is already uncapped).

## Component 1 — per-provider `effort` field + resolution

### Config

Add `"effort"` to the providers that have a knob:

```jsonc
"claude":   { "model": "opus",            "effort": "max",   ... },
"codex":    { "model": "gpt-5.5",         "effort": "xhigh", ... },
"deepseek": { "model": "deepseek-v4-pro", "effort": "max",   ... },
```

agy/kimi get **no** `effort` field (no cap to render; adding one would be a no-op that misleads).

### Resolution — one-line change in `render_call`

Today (`scry`):

```python
eff = settings.get("effort")
```

Becomes (exact parallel to the `model = model or p.get("model","")` fallback):

```python
eff = settings.get("effort") or p.get("effort")
```

Precedence (first non-empty wins): global/phase/`--effort` setting → provider `effort` → CLI default.
Because every call routes through `render_call`, all phases (incl. the streamed synthesis via
`stream_call`) pick up the provider effort automatically — **no per-phase edits**.

### Rendering per provider (caps)

- claude: existing `caps.effort = ["--effort", "{effort}"]` → `--effort max`.
- codex: existing `caps.effort = ["-c", "model_reasoning_effort={effort}"]` → `-c model_reasoning_effort=xhigh`.
- deepseek: **new** `caps.effort = ["--reasoning-effort", "{effort}"]` → `--reasoning-effort max`.

## Component 2 — `scry-deepseek` adapter: reasoning effort + thinking

DeepSeek's effort lives in the request body, not a CLI flag scry can render directly — so scry
passes `--reasoning-effort <value>` to the adapter, which translates it into the API body.

- New arg: `ap.add_argument("--reasoning-effort", default=None)`.
- The body fields (DeepSeek's OpenAI-compatible endpoint): top-level `reasoning_effort` and
  top-level `thinking` (the OpenAI SDK's `extra_body.thinking` flattens to top-level in a raw
  POST, which is what this adapter does). Helper:

```python
# Models that cannot accept reasoning_effort/thinking (V4-Flash non-thinking).
NON_THINKING_MODELS = {"deepseek-chat"}

def thinking_payload(model, effort):
    """Reasoning fields to merge into the request body for a thinking-capable model.
    Returns {} when no effort is requested or the model can't think (so a non-thinking
    model is never sent fields it would reject). DeepSeek takes `reasoning_effort` and
    `thinking` at the top level of the raw body."""
    if not effort or model in NON_THINKING_MODELS:
        return {}
    return {"reasoning_effort": effort, "thinking": {"type": "enabled"}}
```

Payload (merge into the existing dict, after the `max_tokens` field from the truncation fix):

```python
payload = {"model": args.model, "messages": messages, "stream": False,
           "max_tokens": resolve_max_tokens(args.max_tokens, args.model),
           **thinking_payload(args.model, args.reasoning_effort)}
```

The 384K `max_tokens` default (already shipped) covers max-mode's long chains of thought — DeepSeek's
docs call for a generous `max_tokens` and ≥384K context for "Think Max". The adapter still returns
`message.content` (the answer); `reasoning_content` is ignored, as before.

## Files

- `scry`: add `effort` to claude/codex/deepseek provider records; add deepseek `caps.effort`;
  change the one `eff = …` line in `render_call`. Mirror in `config.json`.
- `scry-deepseek`: `--reasoning-effort` arg + `NON_THINKING_MODELS` + `thinking_payload` + payload merge.
- `config.json`: mirror provider `effort` fields + deepseek `caps.effort`; update notes.
- Docs: README (effort section), CHANGELOG, config.json notes.

## Testing

- **`render_call` effort resolution** (extend `tests/test_render_call.py`):
  - claude provider `effort:"max"`, no settings effort → argv has `--effort max`.
  - codex provider `effort:"xhigh"` → argv has `-c model_reasoning_effort=xhigh`.
  - deepseek provider `effort:"max"` + new cap → argv has `--reasoning-effort max`.
  - settings effort overrides provider effort (e.g. `settings(effort="low")` on claude → `--effort low`).
  - a provider with no effort and no settings effort (agy/kimi) → no effort flag.
  - **Update** `TestClaude.test_effort_absent_by_default` — it breaks once claude has a default
    effort; re-target it to assert absence only when neither settings nor provider set effort
    (pop `effort` from the provider copy), mirroring `test_no_model_anywhere_omits_flag`.
- **`scry-deepseek` `thinking_payload`** (extend `tests/test_deepseek_adapter.py`):
  - `("deepseek-v4-pro","max")` → `{"reasoning_effort":"max","thinking":{"type":"enabled"}}`.
  - `("deepseek-reasoner","max")` → same shape (thinking alias).
  - `("deepseek-chat","max")` → `{}` (gated — non-thinking model).
  - `("deepseek-v4-pro", None)` → `{}` (no effort requested).
- Existing `effort` tests that pass effort via settings still pass (settings wins). The dry-run
  header still shows `effort=None` (the *global* setting is unchanged); proposer command lines
  now include the effort flags, which no current dry-run test asserts absent — confirm via the
  full suite.
- Full suite green (`python3 -m unittest discover -s tests`).

## Risks / caveats

- **Cost/latency:** every call now runs at max reasoning (incl. judge + synthesis). Accepted.
- **Codex reset bug:** codex has a reported issue (openai/codex#12042) where `model_reasoning_effort`
  can reset to medium. Our `-c` override is the documented mechanism; flag verifying it takes effect,
  but it's codex-side, not ours.
- **DeepSeek body shape:** `thinking` is sent top-level (raw POST, not the OpenAI SDK's `extra_body`).
  Verify a real call is accepted; lower to `reasoning_effort` only if the API rejects top-level `thinking`.
- **Non-thinking deepseek:** the `NON_THINKING_MODELS` gate keeps `deepseek-chat` from being sent
  fields it rejects; the pinned `deepseek-v4-pro` is unaffected.

## Docs to update

`README.md` (a short "reasoning effort" note: per-provider `effort`, claude `max` / codex `xhigh`
/ deepseek `max`, agy+kimi already maxed, applies to all phases, costs more), `config.json` (effort
fields + deepseek caps note), `CHANGELOG.md` (new entry).
