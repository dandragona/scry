#!/bin/sh
# scry smoke test — exercises the CLI end-to-end against STUB provider binaries.
# Never spends money: it only runs --version / --help / --check / --dry-run, with
# fake claude/codex/agy on PATH that echo canned output. Used by CI and locally.
#
#   sh tests/smoke.sh
#
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRY="$ROOT/scry"
fail() { printf '\033[31mFAIL:\033[0m %s\n' "$1" >&2; exit 1; }
pass() { printf '\033[32mok:\033[0m %s\n' "$1"; }

# --- compile -----------------------------------------------------------------
python3 -m py_compile "$SCRY" "$ROOT/scry-eval" || fail "py_compile"
pass "py_compile scry + scry-eval"

# --- config.json is valid and self-consistent --------------------------------
python3 - "$ROOT/config.json" <<'PY' || fail "config.json invalid/inconsistent"
import json, sys
cfg = json.load(open(sys.argv[1]))
provs = cfg.get("providers", {})
used = [m["provider"] for m in cfg.get("panel", [])]
used += [cfg.get("judge", {}).get("provider"), cfg.get("aggregator", {}).get("provider")]
missing = sorted({p for p in used if p and p not in provs})
assert not missing, f"panel/judge/aggregator reference undefined providers: {missing}"
PY
pass "config.json valid + every referenced provider defined"

# --- --version / --help ------------------------------------------------------
"$SCRY" --version | grep -q "^scry " || fail "--version"
"$SCRY" --help >/dev/null 2>&1 || fail "--help exit code"
pass "--version / --help"

# --- stub provider binaries --------------------------------------------------
STUB="$(mktemp -d)"
trap 'rm -rf "$STUB"' EXIT
printf '#!/bin/sh\necho "claude 0.0.0-stub"\n'              > "$STUB/claude"
printf '#!/bin/sh\necho "Logged in as ci@example.com"\n'    > "$STUB/codex"
printf '#!/bin/sh\necho "agy 0.0.0-stub"\n'                 > "$STUB/agy"
chmod +x "$STUB/claude" "$STUB/codex" "$STUB/agy"

# --- doctor passes when all CLIs are present ---------------------------------
PATH="$STUB:$PATH" "$SCRY" --check >/dev/null 2>&1 || fail "--check should pass with stubs present"
pass "--check exit 0 with all providers present"

# --- doctor fails when a provider binary is missing --------------------------
# Point one provider at a binary that cannot exist (host-independent — the CI
# host may or may not have the real CLIs installed, so we don't rely on PATH).
BAD="$STUB/bad-config.json"
python3 - "$ROOT/config.json" "$BAD" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
cfg["providers"]["codex"]["cmd"] = ["scry-nonexistent-cli-xyz"]
json.dump(cfg, open(sys.argv[2], "w"))
PY
if PATH="$STUB:$PATH" "$SCRY" --check --config "$BAD" >/dev/null 2>&1; then
  fail "--check should exit non-zero when a provider binary is missing"
fi
pass "--check exit non-zero when a provider binary is missing"

# --- dry-run constructs the expected pipeline (no spend) ---------------------
OUT="$(PATH="$STUB:$PATH" "$SCRY" --dry-run "smoke test prompt")"
echo "$OUT" | grep -q "^PROPOSER"   || fail "dry-run missing PROPOSER lines"
echo "$OUT" | grep -q "^JUDGE"      || fail "dry-run missing JUDGE line"
echo "$OUT" | grep -q "^AGGREGATOR" || fail "dry-run missing AGGREGATOR line"
echo "$OUT" | grep -q -- "--output-format json" || fail "dry-run missing claude json flag"
pass "--dry-run builds panel + judge + aggregator argv"

# --- synthesize mode skips the judge -----------------------------------------
PATH="$STUB:$PATH" "$SCRY" --mode synthesize --dry-run "x" | grep -q "^JUDGE" \
  && fail "synthesize mode should not emit a JUDGE stage" || true
pass "--mode synthesize skips the judge"

printf '\n\033[32mAll smoke checks passed.\033[0m\n'
