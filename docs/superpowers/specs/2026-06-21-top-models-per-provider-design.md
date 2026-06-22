# Top model per provider + DeepSeek off-spec fix

**Date:** 2026-06-21
**Status:** Approved (design)
**Scope:** `scry` CLI + `scry-deepseek` adapter — make each provider's top model a single
visible/swappable config field, run every provider at its top tier (default panel = all 5),
and fix the DeepSeek adapter's silent output truncation.

## Motivation

A `scry plan` run exposed DeepSeek (`deepseek-chat`) going off-spec in three ways:

1. **Off-form** — it produced the *artifact* (a draft survey document) instead of a *plan*.
2. **Truncated** — the draft was cut off mid-document.
3. **Internally inconsistent** — its ranked table put rank-1 (composite 16) *below* rank-3
   (composite 17).

Root causes (code-grounded, not guesses):

- **(2) is a real bug.** `scry-deepseek` only sends `max_tokens` when `--max-tokens` is
  passed (`scry-deepseek:58,80`), and the `deepseek` provider has empty `"caps": {}`
  (`scry:312`), so scry has no path to set it (`max_output_tokens` is "claude only",
  `scry:187`/`scry:529`). DeepSeek then applies its API **default of 4,096 output tokens**
  — a long plan draft is silently truncated. Independent of model quality.
- **(1) and (3) are weak-model behavior.** `deepseek-chat` routes to **V4-Flash
  *non-thinking*** (`config.json:105`) — the weakest tier, no reasoning step. The
  plan-vs-artifact instruction lives only in the system prompt (`PLAN_DRAFTER_SYSTEM`,
  `scry:158`); the user prompt is just `"Original request: <task>"` (`render_plan_prompt`,
  `scry:2215`), so a weak non-thinking model latches onto the task and *does* it, and has
  no chain-of-thought to keep a composite-score table self-consistent.

The fix has two halves: a genuine bug fix (truncation) and a quality lever the user chose —
**run every provider at its top tier**, which moves DeepSeek to `deepseek-v4-pro`
(thinking-capable, far stronger at instruction-following + arithmetic). At top tier the
prompt-anchoring mitigation is unnecessary and is not pursued.

## Goals

- Each provider's model is declared **once**, visibly, in config, and is a one-line swap as
  new models ship (per-provider `model` field).
- Every provider runs at its **top tier**; the shipped default panel includes all five.
- DeepSeek never silently truncates: the adapter requests the model's max output ceiling.

## Non-goals

- Prompt-anchoring the drafter (lever C). Top-tier models make it unnecessary.
- Per-phase model selection (a provider uses one model across phases unless a member overrides).
- Changing judge/aggregator (already `claude` → `opus`, the top tier).

## Decisions (confirmed)

1. **Config shape:** per-provider `model` field (small code change), not panel-only strings.
2. **codex / kimi:** pin explicit top IDs (they expose no "latest" alias), not empty=default.
3. **Default panel:** all five providers at top tier.

## Pinned top models

Verified against the installed CLIs on this machine:

| provider | model | how confirmed | tracks latest? |
|---|---|---|---|
| claude | `opus` | alias → latest Opus | yes (alias) |
| codex | `gpt-5.5` | `~/.codex/config.toml` (`model = "gpt-5.5"`) | no — re-pin on new model |
| agy | `Gemini 3.1 Pro (High)` | `agy models` (top; Pro > Flash, no 3.5 Pro yet) | no — re-pin |
| deepseek | `deepseek-v4-pro` | DeepSeek docs (top tier; 1M ctx, 384K max out) | tier alias |
| kimi | `K2.7` | `~/.kimi` logs (current model) | no — re-pin |

`gpt-5.5` and `K2.7` are the strings read from local config/logs — verify each CLI accepts
them as a `--model` value during implementation.

## Component 1 — per-provider `model` field

### Config shape

Each provider record gains a documented `"model"` = its canonical top tier. Panel members,
judge, and aggregator **omit** `model` and inherit it, so there is exactly one swap-point
per provider. An explicit `model` on a member still wins (power-user override).

```jsonc
"providers": {
  "claude":   { "model": "opus", ... },
  "codex":    { "model": "gpt-5.5", ... },
  "agy":      { "model": "Gemini 3.1 Pro (High)", ... },
  "deepseek": { "model": "deepseek-v4-pro", ... },
  "kimi":     { "model": "K2.7", ... }
},
"panel": [
  { "provider": "claude", "label": "claude-opus" },
  { "provider": "codex",  "label": "codex-gpt"  },
  { "provider": "agy",    "label": "gemini-pro" },
  { "provider": "deepseek","label": "deepseek"  },
  { "provider": "kimi",   "label": "kimi"       }
],
"judge":      { "provider": "claude" },
"aggregator": { "provider": "claude" }
```

### Resolution and precedence

Effective model for any call, first non-empty wins:

```
member model (panel[].model / judge.model / aggregator.model)  →  providers[provider].model  →  CLI default (flag omitted)
```

Implemented as a **one-line fallback** at the single argv choke point `render_call`
(`scry:490`), which already receives the provider dict `p`:

```python
model = model or p.get("model", "")
```

All real calls route through `render_call`: `call_cli` (`scry:823`), `stream_call`
(`scry:940`), and the diagnostics dry-run (`scry:1251/1260/1268`). No other call site changes.

### Backward compatibility (shallow merge)

`load_config` does a shallow `cfg.update()` (`scry:414`) — only `settings`/`plan`/`phases`
are backfilled; a user-supplied `providers` or `panel` block replaces the default wholesale.
Implications:

- Users with **no** `providers`/`panel` block get the new defaults automatically.
- Users with a custom `providers` block add `model` to their own records (it's their config).
  If a provider record lacks `model`, inheritance degrades gracefully to CLI default.
- Existing panel members that carry an explicit `model` keep working (member wins).

No backfill of provider `model` is added (would require deep-merging the providers block —
out of scope; the shallow-merge contract is preserved).

## Component 2 — DeepSeek truncation fix

The fix lives in `scry-deepseek` (it owns the DeepSeek API contract), **not** in scry's caps
machinery — simpler, no new global knob, and the right home for provider-specific limits.

When `--max-tokens` is not supplied, the adapter defaults it to the **chosen model's maximum
output** and sends it, instead of inheriting DeepSeek's 4,096 default. A ceiling is free —
the model stops when done, so cost/latency are unaffected; it only removes the silent cut-off.

A tiny model→max map:

```python
DEEPSEEK_MAX_OUTPUT = {            # model id (prefix) -> max output tokens
    "deepseek-v4-pro": 384000,     # V4 family: 1M context, 384K max output
    "deepseek-chat": 8192,         # legacy alias (V4-Flash non-thinking)
    "deepseek-reasoner": 8192,     # legacy alias (V4-Flash thinking)
}
# default for unknown models: 8192 (the safe documented ceiling)
```

`--max-tokens` remains an explicit override. Update the adapter docstring to note the
no-silent-truncation behavior.

## Component 3 — default panel = all five at top tier

`DEFAULT_CONFIG["panel"]` (`scry:361`) gains `deepseek` + `kimi` members (model inherited).
A comment notes the DeepSeek caveat: **knowledge-only (no web search)**, so it contributes
no live grounding — acceptable as a panel voice, weighted by the judge/synthesizer.

## Code changes

**`scry`**
1. `DEFAULT_CONFIG.providers.*` — add `"model"` to each provider (table above).
2. `DEFAULT_CONFIG.panel` — drop inline `model`s (inherit); add `deepseek` + `kimi`; keep
   labels. `judge`/`aggregator` drop inline `model` (inherit `opus`).
3. `render_call` — add `model = model or p.get("model", "")` as the first statement.

**`scry-deepseek`**
4. Add `DEEPSEEK_MAX_OUTPUT` map + resolve a default `--max-tokens` from `--model` when not
   passed; always include `max_tokens` in the payload. Docstring note.

**`config.json`** (reference mirror)
5. Mirror the provider `model` fields, the 5-member panel, and the inherit-by-omission shape;
   update the deepseek `_note` (top tier + no-truncation behavior).

**User's live config** (separate, offered)
6. Apply the same shape to `~/.config/scry/config.json` so the running setup gets it.

## Alternatives considered

- **Max-tokens via scry caps** (generalize `max_tokens_env` to a `max_tokens_flag` argv form):
  rejected — adds a global knob and machinery for a provider-specific safety default that
  belongs in the adapter. The adapter map is simpler and self-contained.
- **Model strings in the panel array only** (no code): rejected by decision #1 — a provider's
  model would only be visible when it's a panel member, and there'd be no single swap-point
  for judge/aggregator/CLI-shorthand uses.

## Testing

- **`render_call` resolution** (new): member `model` wins; empty member → provider `model`;
  both empty → flag omitted (CLI default). One test per branch, asserting argv contains/omits
  `--model <x>`.
- **`load_config`**: a config with no `providers`/`panel` yields the 5-member panel and the
  pinned provider models; a custom `panel` still replaces wholesale (shallow-merge contract).
- **`scry-deepseek`**: `--max-tokens` absent + `--model deepseek-v4-pro` → payload
  `max_tokens == 384000`; legacy alias → 8192; unknown model → 8192; explicit `--max-tokens`
  overrides. (Build the payload via a small extracted helper so it's unit-testable without a
  network call.)
- Full suite stays green (currently 525 hermetic tests).

## Risks / open items

- **Graceful skip:** the shipped default now lists 5 providers; verify a missing/unauthed CLI
  is skipped with a log, not a hard failure (check the panel loop + `scry --check`). Acceptance
  bar before shipping the 5-member default.
- **Exact IDs:** confirm `gpt-5.5` and `K2.7` are valid `--model` values for codex/kimi
  (smoke each once). Re-pin if the accepted string differs.
- **Cost/latency:** five top-tier members per phase is pricier/slower than three — expected
  and chosen.
- **DeepSeek V4 max:** 384K is from current DeepSeek docs; if `deepseek-v4-pro`'s API rejects
  it, lower to the documented value. The ceiling is a safety, not a target.

## Docs to update

`README.md` (providers/panel + the DeepSeek "API-key exception" note: now top tier + no
truncation), `config.json` (as above), `CHANGELOG.md` (new entry: per-provider `model`,
all-5 top-tier panel, DeepSeek max-tokens fix).
