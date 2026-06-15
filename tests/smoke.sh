#!/bin/sh
# scry smoke test — exercises the CLI end-to-end against STUB provider binaries.
# Never spends money: it only runs --version / --help / --check / --dry-run / init,
# with fake claude/codex/agy/kimi on PATH that echo canned output. CI + local.
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
# kimi is wired as a selectable provider with the read-only agent-file policy.
k = provs.get("kimi") or {}
assert k.get("agent_file", {}).get("exclude_always"), "kimi provider missing agent_file policy"
PY
pass "config.json valid + every referenced provider defined (incl. kimi)"

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
printf '#!/bin/sh\necho "kimi 0.0.0-stub"\n'                > "$STUB/kimi"
chmod +x "$STUB/claude" "$STUB/codex" "$STUB/agy" "$STUB/kimi"

# --- doctor passes when all CLIs are present ---------------------------------
PATH="$STUB:$PATH" "$SCRY" --check >/dev/null 2>&1 || fail "--check should pass with stubs present"
pass "--check exit 0 with all providers present"

# --- doctor probes kimi too when it's in the panel ---------------------------
PATH="$STUB:$PATH" "$SCRY" --check --panel "claude:opus,kimi" >/dev/null 2>&1 \
  || fail "--check should pass with kimi in the panel"
pass "--check exit 0 with kimi in the panel"

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

# --- dry-run renders the kimi provider (quiet + generated agent file) ---------
# Default (no model): kimi uses the account default, so no --model is emitted.
KOUT="$(PATH="$STUB:$PATH" "$SCRY" --dry-run --panel "claude:opus,kimi" "x")"
echo "$KOUT" | grep -q "kimi --quiet"  || fail "dry-run missing 'kimi --quiet'"
echo "$KOUT" | grep -q -- "--agent-file" || fail "dry-run missing kimi '--agent-file'"
# An explicit model is rendered as --model.
PATH="$STUB:$PATH" "$SCRY" --dry-run --panel "kimi:kimi-for-coding" "x" \
  | grep -q -- "--model kimi-for-coding" || fail "dry-run missing explicit kimi --model"
pass "--dry-run renders kimi (default = no --model; explicit model = --model)"

# --- synthesize mode skips the judge -----------------------------------------
PATH="$STUB:$PATH" "$SCRY" --mode synthesize --dry-run "x" | grep -q "^JUDGE" \
  && fail "synthesize mode should not emit a JUDGE stage" || true
pass "--mode synthesize skips the judge"

# --- streaming plumbing: final answer types out token-by-token ---------------
python3 - "$ROOT" "$STUB" <<'PY' || fail "stream_call plumbing"
import asyncio, json, os, sys, tempfile
from importlib.machinery import SourceFileLoader
root, stub = sys.argv[1], sys.argv[2]
m = SourceFileLoader("scry_sut", os.path.join(root, "scry")).load_module()
cfg = m.load_config(os.path.join(root, "config.json"))
claude = os.path.join(stub, "claude")
with open(claude, "w") as f:
    f.write('#!/usr/bin/env python3\nimport sys, json\nsys.stdin.read()\n'
            'for ch in "streamed ok":\n'
            '    print(json.dumps({"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":ch}}}), flush=True)\n'
            'print(json.dumps({"type":"result","result":"streamed ok"}), flush=True)\n')
os.chmod(claude, 0o755)
os.environ["PATH"] = stub + ":" + os.environ["PATH"]
chunks = []
r = asyncio.run(m.stream_call(cfg, "claude", "", None, "hi",
                              tempfile.mkdtemp(prefix="scry-run-"), 0,
                              cfg["settings"], lambda d: chunks.append(d)))
assert "".join(chunks) == "streamed ok" and r["streamed"] is True, (chunks, r)
PY
pass "stream_call streams deltas + reconstructs the final answer"

# --- `scry init` composes a panel + writes a valid config (no prompts left) ---
# Piped answers: panel "1,4:kimi-for-coding" (claude default model + kimi with an
# explicit model), <enter> judge, <enter> aggregator, "y" web. --out skips the path
# prompt. Member 1 (claude) takes its suggested model; member 4 pins an explicit one.
INITCFG="$STUB/init-config.json"
printf '1,4:kimi-for-coding\n\n\ny\n' | PATH="$STUB:$PATH" "$SCRY" init --out "$INITCFG" >/dev/null 2>&1 \
  || fail "scry init exited non-zero"
python3 - "$INITCFG" <<'PY' || fail "scry init wrote an invalid/unexpected config"
import json, sys
cfg = json.load(open(sys.argv[1]))
provs = [m["provider"] for m in cfg["panel"]]
assert provs == ["claude", "kimi"], provs
assert cfg["panel"][0]["model"] == "opus", cfg["panel"]          # suggested default
assert cfg["panel"][1]["model"] == "kimi-for-coding", cfg["panel"]  # explicit :model
assert len({m["label"] for m in cfg["panel"]}) == len(cfg["panel"]), "labels not unique"
assert cfg["judge"]["provider"] == "claude" and cfg["aggregator"]["provider"] == "claude"
assert cfg["settings"]["web_tools"] is True
PY
pass "scry init builds a panel + writes a valid config.json"

# --- `scry init` rejects an unknown judge/aggregator provider (fail-fast) ------
BADCFG="$STUB/init-bad.json"
# panel "1" (claude), judge "nope" (invalid), <enter> aggregator. Validation must
# fail before writing — so the file must NOT be created.
if printf '1\nnope\n\n' | PATH="$STUB:$PATH" "$SCRY" init --out "$BADCFG" >/dev/null 2>&1; then
  fail "scry init should exit non-zero for an unknown judge provider"
fi
[ ! -f "$BADCFG" ] || fail "scry init should not write a config when validation fails"
pass "scry init rejects an unknown judge/aggregator provider (writes nothing)"

# --- init welcome animation (RuneCircle) renders without error ----------------
python3 - "$ROOT" <<'PY' || fail "RuneCircle render"
import os, sys, re
from importlib.machinery import SourceFileLoader
m = SourceFileLoader("scry_sut", os.path.join(sys.argv[1], "scry")).load_module()
RC = m.RuneCircle
c = RC()
# every frame across build + idle renders exactly ROWS lines of fixed visible width
strip = re.compile(r"\x1b\[[0-9;]*m")
for f in range(RC.BUILD + 30):
    plain = c.render(f, color=False)
    colored = c.render(f, color=True)
    assert len(plain) == RC.ROWS, (f, len(plain))
    assert all(len(ln) == RC.COLS for ln in plain), (f, plain)
    # colored lines strip back to the same glyphs (well-formed ANSI, no leaks)
    assert [strip.sub("", ln) for ln in colored] == plain, f
# the eye is open on the settled/static frame
assert "◉" in "".join(c.render(RC.BUILD, color=False))
# kimi defaults to the account model (empty), since a fixed id may not be defined
assert m.INIT_SUGGEST["kimi"] == ("", "kimi"), m.INIT_SUGGEST["kimi"]
# static welcome composes (no stdin touched) — smoke runs are non-TTY anyway
m.show_init_welcome(no_anim=True)
PY
pass "init RuneCircle animation renders (build+idle, well-formed ANSI, eye opens)"

printf '\n\033[32mAll smoke checks passed.\033[0m\n'
