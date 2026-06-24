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

# --- config resolution: global by default, scry.config.json as opt-in override -
# scry's config lives once per computer at ~/.config/scry/config.json; a project
# may override with ./scry.config.json. A generic ./config.json must NOT be read
# (it belongs to other tools — reading it was a filename-collision footgun).
python3 - "$ROOT" <<'PY' || fail "config resolution precedence"
import json, os, sys, tempfile
from importlib.machinery import SourceFileLoader
root = sys.argv[1]
home, proj = tempfile.mkdtemp(), tempfile.mkdtemp()
os.environ["HOME"] = home        # global_config_path() honors $HOME
os.chdir(proj)
m = SourceFileLoader("scry_sut", os.path.join(root, "scry")).load_module()

assert m.LOCAL_CONFIG_NAME == "scry.config.json", m.LOCAL_CONFIG_NAME
assert str(m.global_config_path()) == os.path.join(home, ".config", "scry", "config.json")

# (a) the global config is read when nothing else is present
os.makedirs(os.path.dirname(str(m.global_config_path())))
open(str(m.global_config_path()), "w").write(json.dumps({"mode": "synthesize"}))
assert m.load_config(None)["mode"] == "synthesize", "global config not read"

# (b) a generic ./config.json in cwd is IGNORED (no collision with other tools' files)
open(os.path.join(proj, "config.json"), "w").write(json.dumps({"mode": "GENERIC-IGNORE-ME"}))
assert m.load_config(None)["mode"] == "synthesize", "a stray ./config.json must NOT be loaded"

# (c) a project-local ./scry.config.json overrides the global config
open(os.path.join(proj, m.LOCAL_CONFIG_NAME), "w").write(
    json.dumps({"mode": "fusion", "judge": {"provider": "codex", "model": ""}}))
cfg = m.load_config(None)
assert cfg["mode"] == "fusion" and cfg["judge"]["provider"] == "codex", cfg
assert "claude" in cfg.get("providers", {}), "providers lost on partial override"

# (d) an explicit --config path still wins over both
explicit = os.path.join(proj, "explicit.json")
open(explicit, "w").write(json.dumps({"mode": "synthesize"}))
assert m.load_config(explicit)["mode"] == "synthesize", "--config must win"
PY
pass "config resolution: global default, scry.config.json override, generic config.json ignored"

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
printf '#!/bin/sh\necho "kimi-cli 0.0.0-stub"\n'            > "$STUB/kimi-cli"
chmod +x "$STUB/claude" "$STUB/codex" "$STUB/agy" "$STUB/kimi-cli"

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
echo "$KOUT" | grep -q "kimi-cli --quiet"  || fail "dry-run missing 'kimi-cli --quiet'"
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
# Piped answers: panel "1,5:kimi-for-coding" (claude default model + kimi with an
# explicit model), <enter> judge, <enter> aggregator, "y" web. --out skips the path
# prompt. Member 1 (claude) takes its suggested model; member 5 (kimi) pins an
# explicit one. (Provider order: 1 claude, 2 codex, 3 agy, 4 deepseek, 5 kimi, 6 glm.)
INITCFG="$STUB/init-config.json"
printf '1,5:kimi-for-coding\n\n\ny\n' | PATH="$STUB:$PATH" "$SCRY" init --out "$INITCFG" >/dev/null 2>&1 \
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

# --- `scry init` writes the GLOBAL config by default (works from any dir) ------
# No --out: the wizard's last prompt defaults to ~/.config/scry/config.json. HOME
# is sandboxed to a temp dir so we never touch the real one. Piped answers: panel
# "1", <enter> judge, <enter> aggregator, "y" web, <enter> to accept the default path.
GHOME="$(mktemp -d)"
printf '1\n\n\ny\n\n' | HOME="$GHOME" PATH="$STUB:$PATH" "$SCRY" init >/dev/null 2>&1 \
  || fail "scry init (global default) exited non-zero"
[ -f "$GHOME/.config/scry/config.json" ] \
  || fail "scry init should write the global ~/.config/scry/config.json by default"
pass "scry init writes the global ~/.config/scry/config.json by default"

# --- `scry init --local` writes ./scry.config.json in the cwd -----------------
LHOME="$(mktemp -d)"; LPROJ="$(mktemp -d)"
( cd "$LPROJ" && printf '1\n\n\ny\n\n' | HOME="$LHOME" PATH="$STUB:$PATH" "$SCRY" init --local \
    >/dev/null 2>&1 ) || fail "scry init --local exited non-zero"
[ -f "$LPROJ/scry.config.json" ] || fail "scry init --local should write ./scry.config.json"
[ ! -f "$LHOME/.config/scry/config.json" ] || fail "scry init --local must not touch the global config"
pass "scry init --local writes ./scry.config.json (not the global config)"

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

# --- cost meter + run history (metered stub claude, isolated SCRY_HOME) --------
HOME2="$(mktemp -d)"
CLI2="$(mktemp -d)"
trap 'rm -rf "$STUB" "$HOME2" "$CLI2"' EXIT
cat > "$CLI2/claude" <<'EOF'
#!/usr/bin/env python3
import sys, json
sys.stdin.read()
u = {"input_tokens": 8, "cache_read_input_tokens": 2000, "output_tokens": 400,
     "server_tool_use": {"web_search_requests": 1}}
print(json.dumps({"type": "result", "is_error": False, "result": "Fused.",
                  "total_cost_usd": 0.05, "usage": u}))
EOF
chmod +x "$CLI2/claude"

# panel=claude only -> 3 claude calls (panel+judge+synth), all metered.
COST_JSON="$(PATH="$CLI2:$PATH" SCRY_HOME="$HOME2" "$SCRY" --panel claude:opus --json "q1" 2>/dev/null)"
echo "$COST_JSON" | python3 -c '
import json, sys
c = json.load(sys.stdin)["cost"]
assert c["total_usd"] == 0.15, c
assert c["calls"] == 3 and c["metered_calls"] == 3, c
assert c["web_searches"] == 3 and c["output_tokens"] == 1200, c
' || fail "cost block not threaded through --json"
pass "cost meter: \$/tokens/web roll up into the --json cost block"

# the run above was recorded; log lists it, last reprints its answer to stdout
PATH="$CLI2:$PATH" SCRY_HOME="$HOME2" "$SCRY" log | grep -q "q1" \
  || fail "scry log missing the saved run"
PATH="$CLI2:$PATH" SCRY_HOME="$HOME2" "$SCRY" last 2>/dev/null | grep -q "Fused." \
  || fail "scry last didn't reprint the saved answer"
ROWS_BEFORE="$(wc -l < "$HOME2/history.jsonl")"
PATH="$CLI2:$PATH" SCRY_HOME="$HOME2" "$SCRY" --panel claude:opus --no-save "q2" >/dev/null 2>&1
[ "$ROWS_BEFORE" = "$(wc -l < "$HOME2/history.jsonl")" ] || fail "--no-save still wrote a history row"
pass "run history: scry log / scry last / --no-save"

printf '\n\033[32mAll smoke checks passed.\033[0m\n'
