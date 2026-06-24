"""Shared test harness for the scry test suite — stdlib only, no dependencies.

Every test module imports from here. It provides:

  * load_scry() / load_scry_eval() — load the two extension-less executables as
    importable modules (via SourceFileLoader, exactly like scry-eval does), cached.
  * StubBins — a context manager that drops fake `claude`/`codex`/`agy`/`kimi`
    executables on PATH so the integration tests can drive call_cli / scry_run /
    --check / --dry-run end-to-end WITHOUT ever invoking a real (paid) model CLI.
  * stub script factories (claude_json, claude_stream, claude_smart, codex_outfile,
    agy_text, kimi_text, fail, hang, echo_argv) — canned stub bodies for each
    provider's capture mode.
  * run_scry() — invoke the real ./scry executable as a subprocess.
  * FileServer — a localhost HTTP server used by the `scry update` tests (point
    SCRY_UPDATE_URL at it; no network, no GitHub).
  * make_scry_copy() — copy ./scry to a temp dir so `scry update` can swap a throwaway
    file instead of the repo's tracked one.

Nothing here spends money: the stubs only echo canned text.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import stat
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRY = REPO_ROOT / "scry"
SCRY_EVAL = REPO_ROOT / "scry-eval"
SCRY_DEEPSEEK = REPO_ROOT / "scry-deepseek"
SCRY_GLM = REPO_ROOT / "scry-glm"
CONFIG_JSON = REPO_ROOT / "config.json"


# --------------------------------------------------------------------------- #
# Module loaders (cached) — load the extension-less scripts as modules. Their
# `if __name__ == "__main__"` guards mean main() does NOT run on import.
# --------------------------------------------------------------------------- #
_CACHE: dict = {}


def _load(name: str, path: Path):
    if name not in _CACHE:
        loader = SourceFileLoader(name, str(path))
        spec = spec_from_loader(name, loader)
        mod = module_from_spec(spec)
        loader.exec_module(mod)
        _CACHE[name] = mod
    return _CACHE[name]


def load_scry():
    """The `scry` CLI loaded as a module (symbols only; main() not invoked)."""
    return _load("scry_sut", SCRY)


def load_scry_eval():
    """The `scry-eval` harness loaded as a module."""
    return _load("scry_eval_sut", SCRY_EVAL)


def load_scry_deepseek():
    """The `scry-deepseek` adapter loaded as a module (symbols only; main() not run)."""
    return _load("scry_deepseek_sut", SCRY_DEEPSEEK)


def load_scry_glm():
    """The `scry-glm` adapter loaded as a module (symbols only; main() not run)."""
    return _load("scry_glm_sut", SCRY_GLM)


# --------------------------------------------------------------------------- #
# Stub provider executables. Each returns a complete, self-contained script body
# (python3) that mimics one real CLI's headless contract closely enough for
# call_cli / scry_run / --check to drive it. They NEVER call a real model.
# --------------------------------------------------------------------------- #

def _py(body: str) -> str:
    return "#!/usr/bin/env python3\n" + body


def claude_json(result: str = "PROPOSER ANSWER", is_error: bool = False) -> str:
    """claude `-p --output-format json`: read stdin, print one JSON object with a
    `result` field and an `is_error` flag (scry's capture='json' contract)."""
    return _py(
        "import sys, json\n"
        "sys.stdin.read()\n"
        f"print(json.dumps({{'result': {result!r}, 'is_error': {bool(is_error)!r}}}))\n"
    )


def claude_stream(text: str = "streamed ok") -> str:
    """claude stream-json: emit one content_block_delta per char, then a `result`.
    Matches scry's _stream_extract('claude') parser."""
    return _py(
        "import sys, json\n"
        "sys.stdin.read()\n"
        f"for ch in {text!r}:\n"
        "    print(json.dumps({'type':'stream_event','event':{'type':'content_block_delta',"
        "'delta':{'type':'text_delta','text':ch}}}), flush=True)\n"
        f"print(json.dumps({{'type':'result','result': {text!r}}}), flush=True)\n"
    )


def claude_smart(proposer: str = "PROPOSER ANSWER", fused: str = "FUSED ANSWER",
                 analysis: dict | None = None) -> str:
    """A claude stub that branches on `--append-system-prompt` so ONE binary can play
    all three pipeline roles (panel / judge / synthesis) in a full scry_run.

    No system flag  -> panel proposer.
    JUDGE_SYSTEM    -> judge: emits the 5-field analysis JSON as its result.
    MOA aggregator  -> synthesis: emits the fused answer.
    """
    if analysis is None:
        analysis = {"consensus": ["c1"], "contradictions": [], "partial_coverage": [],
                    "unique_insights": ["u1"], "blind_spots": []}
    return _py(
        "import sys, json\n"
        "argv = sys.argv[1:]\n"
        "sp = ''\n"
        "for i, a in enumerate(argv):\n"
        "    if a == '--append-system-prompt' and i + 1 < len(argv):\n"
        "        sp = argv[i + 1]\n"
        "sys.stdin.read()\n"
        f"analysis = {analysis!r}\n"
        "if 'impartial judge' in sp:\n"
        "    print(json.dumps({'result': json.dumps(analysis), 'is_error': False}))\n"
        "elif 'synthesize these responses' in sp:\n"
        f"    print(json.dumps({{'result': {fused!r}, 'is_error': False}}))\n"
        "else:\n"
        f"    print(json.dumps({{'result': {proposer!r}, 'is_error': False}}))\n"
    )


def claude_plan(rounds_before_ready: int = 1, questions=None,
                fused: str = "## Context\nThe plan.\n## Steps\n1. do it",
                unique_each_round: bool = False, report_cwd: bool = False,
                fail_synthesis: bool = False) -> str:
    """A claude stub for `scry plan`: ONE binary that plays every plan role by
    branching on a substring UNIQUE to each system prompt (the prompts share phrases
    like 'implementation plan'/'clarifying questions', so we key on disjoint anchors):

      PLAN_QUESTION_JUDGE_SYSTEM  ('deduplicating') -> dedup the proposed questions
                                  found in stdin (case-insensitive).
      PLAN_SYNTH_SYSTEM           ('plan drafts')   -> the markdown plan.
      PLAN_INTERVIEWER_SYSTEM     ('scope a task')  -> emit the questions, or
                                  {"ready":true,"questions":[]} once the transcript
                                  shows >= rounds_before_ready answered questions.
      JUDGE_SYSTEM                ('impartial judge') -> 5-field analysis (final fusion).
      no system (panel proposer in the final fusion) -> a plan draft.

    With unique_each_round, each round's question text embeds the answered-count so
    it's always new (exercises the max-rounds cap)."""
    if questions is None:
        questions = [{"q": "What is the target platform?", "why": "shapes deps",
                      "options": ["linux", "macos"]},
                     {"q": "Any performance budget?", "why": "drives design"}]
    return _py(
        "import sys, json, re, os\n"
        "argv = sys.argv[1:]\n"
        "sp = ''\n"
        "for i, a in enumerate(argv):\n"
        "    if a == '--append-system-prompt' and i + 1 < len(argv):\n"
        "        sp = argv[i + 1]\n"
        # SCRY_SYSDUMP (test hook): append every system prompt this stub is invoked
        # with, so a test can assert which stage got which prompt (e.g. the final-draft
        # panel proposer must receive PLAN_DRAFTER_SYSTEM).
        "if os.environ.get('SCRY_SYSDUMP'):\n"
        "    open(os.environ['SCRY_SYSDUMP'], 'a').write(sp + '\\n===SCRY-SYS-END===\\n')\n"
        "data = sys.stdin.read()\n"
        f"qs = {questions!r}\n"
        f"need = {int(rounds_before_ready)}\n"
        f"uniq = {bool(unique_each_round)!r}\n"
        f"rc = {bool(report_cwd)!r}\n"
        f"fail_synth = {bool(fail_synthesis)!r}\n"
        "answered = len(re.findall(r'\\nA\\d+: ', data))\n"
        "if 'deduplicating' in sp:\n"
        "    m = re.search(r'\\{.*\\}', data, re.S)\n"
        "    prop = json.loads(m.group(0)).get('proposed_questions', []) if m else []\n"
        "    seen = set(); out = []\n"
        "    for q in prop:\n"
        "        k = str(q.get('q', '')).strip().lower()\n"
        "        if k and k not in seen:\n"
        "            seen.add(k); out.append(q)\n"
        "    print(json.dumps({'result': json.dumps({'questions': out}), 'is_error': False}))\n"
        "elif 'plan drafts' in sp:\n"
        f"    out = {fused!r}\n"
        "    if rc: out = out + '\\nCWD=' + os.getcwd()\n"
        "    print(json.dumps({'result': out, 'is_error': False}))\n"
        "elif 'scope a task' in sp:\n"
        "    ready = answered >= need\n"
        "    if rc:\n"
        "        qlist = [] if ready else [{'q': 'cwd is ' + os.getcwd()}]\n"
        "    elif uniq:\n"
        "        qlist = [] if ready else [{'q': 'Detail number %d' % answered}]\n"
        "    else:\n"
        "        qlist = [] if ready else qs\n"
        "    print(json.dumps({'result': json.dumps({'ready': ready, 'questions': qlist}), 'is_error': False}))\n"
        "elif 'impartial judge' in sp:\n"
        "    analysis = {'consensus': [], 'contradictions': [], 'partial_coverage': [],\n"
        "                'unique_insights': [], 'blind_spots': []}\n"
        "    print(json.dumps({'result': json.dumps(analysis), 'is_error': False}))\n"
        "else:\n"
        "    print(json.dumps({'result': ('boom' if fail_synth else 'PLAN DRAFT'),\n"
        "                       'is_error': fail_synth}))\n"
    )


def claude_research(findings: str = "CLAUDE FINDINGS", fused: str = "RESEARCH ANSWER",
                    subqs=None, gaps: bool = False, needs_web: bool = True,
                    report_cwd: bool = False) -> str:
    """A claude stub for Deep Research mode: ONE binary that plays every research role
    by branching on the UNIQUE anchor in each system prompt:

      RESEARCH_BRIEF_SYSTEM     ('research brief')        -> {intent, sub_questions}
      RESEARCH_JUDGE_SYSTEM     ('research referee')      -> 5-field analysis + open_questions
      RESEARCH_SYNTH_SYSTEM     ('research synthesis')    -> the fused prose answer
      RESEARCH_PANEL_SYSTEM     ('deep research analyst') -> a condensed findings brief

    `gaps` controls whether the referee keeps naming an open question every round
    (drives early-exit vs hard-cap loop tests); `needs_web` tags that gap so round-2+
    routing tests can assert no-web providers are excluded. `report_cwd` appends the
    proposer's cwd to its findings (for repo-grounding tests)."""
    if subqs is None:
        subqs = ["sub-q-1", "sub-q-2", "sub-q-3"]
    oq = [{"question": "What remains unresolved?", "needs_web": bool(needs_web)}] if gaps else []
    analysis = {"consensus": [], "contradictions": [], "partial_coverage": [],
                "unique_insights": [], "blind_spots": [], "open_questions": oq}
    brief = {"intent": "INTENT", "sub_questions": subqs}
    return _py(
        "import sys, json, os\n"
        "argv = sys.argv[1:]\n"
        "sp = ''\n"
        "for i, a in enumerate(argv):\n"
        "    if a == '--append-system-prompt' and i + 1 < len(argv):\n"
        "        sp = argv[i + 1]\n"
        "if os.environ.get('SCRY_SYSDUMP'):\n"
        "    open(os.environ['SCRY_SYSDUMP'], 'a').write(sp + '\\n===SCRY-SYS-END===\\n')\n"
        "sys.stdin.read()\n"
        f"brief = {brief!r}\n"
        f"analysis = {analysis!r}\n"
        f"findings = {findings!r}\n"
        f"rc = {bool(report_cwd)!r}\n"
        "if 'research brief' in sp:\n"
        "    print(json.dumps({'result': json.dumps(brief), 'is_error': False}))\n"
        "elif 'research referee' in sp:\n"
        "    print(json.dumps({'result': json.dumps(analysis), 'is_error': False}))\n"
        "elif 'research synthesis' in sp:\n"
        f"    print(json.dumps({{'result': {fused!r}, 'is_error': False}}))\n"
        "else:\n"
        "    out = findings + ('\\nCWD=' + os.getcwd() if rc else '')\n"
        "    print(json.dumps({'result': out, 'is_error': False}))\n"
    )


def flaky_text(marker: str, text: str = "RECOVERED") -> str:
    """A capture='text' proposer that fails ONCE then succeeds: the first call exits
    1 with empty output (-> a transient "empty output" ProviderError) and drops a
    marker file; later calls print `text`. Drives the research retry test."""
    return _py(
        "import sys, os\n"
        "sys.stdin.read()\n"
        f"marker = {marker!r}\n"
        "if not os.path.exists(marker):\n"
        "    open(marker, 'w').write('1')\n"
        "    sys.exit(1)\n"
        f"sys.stdout.write({text!r} + '\\n')\n"
    )


def codex_outfile(result: str = "CODEX ANSWER") -> str:
    """codex `exec -o <outfile>`: write the answer to the file named after `-o`
    (scry's capture='outfile' contract); falls back to stdout if no -o."""
    return _py(
        "import sys\n"
        "argv = sys.argv[1:]\n"
        "out = None\n"
        "for i, a in enumerate(argv):\n"
        "    if a == '-o' and i + 1 < len(argv):\n"
        "        out = argv[i + 1]\n"
        "sys.stdin.read()\n"
        f"text = {result!r}\n"
        "if out:\n"
        "    open(out, 'w').write(text)\n"
        "else:\n"
        "    sys.stdout.write(text)\n"
    )


def agy_text(result: str = "GEMINI ANSWER") -> str:
    """agy `-p <prompt>`: prompt arrives as an ARG (not stdin); print plain text."""
    return _py("import sys\n" f"sys.stdout.write({result!r} + '\\n')\n")


def kimi_text(result: str = "KIMI ANSWER") -> str:
    """kimi `--quiet`: read stdin, print the final answer as plain text."""
    return _py("import sys\n" "sys.stdin.read()\n" f"sys.stdout.write({result!r} + '\\n')\n")


def deepseek_text(result: str = "DEEPSEEK ANSWER") -> str:
    """scry-deepseek: read stdin, print the assistant message as plain text
    (capture='text'). The stub ignores --model/--system/--max-tokens flags."""
    return _py("import sys\n" "sys.stdin.read()\n"
               f"sys.stdout.write({result!r} + '\\n')\n")


def glm_text(result: str = "GLM ANSWER") -> str:
    """scry-glm: read stdin, print the assistant message as plain text
    (capture='text'). The stub ignores --model/--system/--max-tokens/--web flags."""
    return _py("import sys\n" "sys.stdin.read()\n"
               f"sys.stdout.write({result!r} + '\\n')\n")


def version_stub(line: str = "stub 0.0.0") -> str:
    """A trivial `--version`/`--help` style probe target: print a line, exit 0."""
    return _py("import sys\n" f"sys.stdout.write({line!r} + '\\n')\n")


def fail(code: int = 1, stderr: str = "boom", stdout: str = "") -> str:
    """A stub that exits non-zero (probe failure / proposer failure)."""
    return _py(
        "import sys\n"
        "sys.stdin.read()\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({int(code)})\n"
    )


def hang(seconds: float = 30.0) -> str:
    """A stub that sleeps (used to exercise call_cli/stream_call timeout paths)."""
    return _py("import time, sys\n" "sys.stdin.read()\n" f"time.sleep({float(seconds)})\n")


def echo_argv() -> str:
    """Print the received argv as JSON on stdout (assert end-to-end argv wiring)."""
    return _py("import sys, json\n" "print(json.dumps(sys.argv[1:]))\n")


# Convenience: a default set of well-behaved stubs covering all six providers.
def default_stubs() -> dict:
    return {
        "claude": claude_json("CLAUDE ANSWER"),
        "codex": codex_outfile("CODEX ANSWER"),
        "agy": agy_text("GEMINI ANSWER"),
        "kimi-cli": kimi_text("KIMI ANSWER"),
        "scry-deepseek": deepseek_text("DEEPSEEK ANSWER"),
        "scry-glm": glm_text("GLM ANSWER"),
    }


# --------------------------------------------------------------------------- #
# StubBins — write stub executables into a temp dir and prepend it to PATH.
#
# Patches os.environ["PATH"] in-process (so shutil.which + create_subprocess_exec
# resolve the stubs), and exposes `.env` for spawning ./scry as a subprocess.
# Restores PATH and removes the dir on exit.
# --------------------------------------------------------------------------- #
class StubBins(contextlib.AbstractContextManager):
    def __init__(self, stubs: dict | None = None, patch_path: bool = True):
        self.dir = Path(tempfile.mkdtemp(prefix="scry-stub-"))
        self._patch_path = patch_path
        self._old_path = os.environ.get("PATH", "")
        for name, body in (stubs or {}).items():
            self.add(name, body)

    def add(self, name: str, body: str) -> Path:
        """Write one executable stub `name` with the given script `body`."""
        p = self.dir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return p

    @property
    def env(self) -> dict:
        """An environment dict (copy of os.environ) with the stub dir on PATH —
        pass to subprocess.run/run_scry when not patching the process PATH."""
        e = os.environ.copy()
        e["PATH"] = str(self.dir) + os.pathsep + self._old_path
        return e

    def __enter__(self) -> "StubBins":
        if self._patch_path:
            os.environ["PATH"] = str(self.dir) + os.pathsep + self._old_path
        return self

    def __exit__(self, *exc) -> None:
        if self._patch_path:
            os.environ["PATH"] = self._old_path
        shutil.rmtree(self.dir, ignore_errors=True)
        return None


# --------------------------------------------------------------------------- #
# Subprocess driver for the real ./scry
# --------------------------------------------------------------------------- #
import subprocess  # noqa: E402  (kept near its use)


def run_scry(args, input: str | None = None, env: dict | None = None,
             cwd: str | None = None, timeout: float = 60.0):
    """Run `./scry <args>` as a subprocess; return the CompletedProcess.

    `env` defaults to the current environment; pass StubBins(...).env to inject
    stub provider binaries. `cwd` defaults to a throwaway temp dir so a stray
    project-local ./scry.config.json can't leak into the test. (scry does not
    auto-load a generic ./config.json; an explicit --config path is unaffected
    by cwd.)

    SCRY_HOME is isolated to a throwaway dir unless the caller already set one, so
    a subprocess run never writes history/plan-checkpoints into the real ~/.scry.
    A test that needs to inspect history sets SCRY_HOME in `env` itself."""
    argv = [str(SCRY), *[str(a) for a in args]]
    own_cwd = cwd is None
    cwd = cwd or tempfile.mkdtemp(prefix="scry-test-cwd-")
    env = dict(env) if env is not None else os.environ.copy()
    own_home = "SCRY_HOME" not in env
    if own_home:
        env["SCRY_HOME"] = tempfile.mkdtemp(prefix="scry-test-home-")
    try:
        return subprocess.run(argv, input=input, env=env, cwd=str(cwd),
                              capture_output=True, text=True, timeout=timeout)
    finally:
        if own_cwd:
            shutil.rmtree(cwd, ignore_errors=True)
        if own_home:
            shutil.rmtree(env["SCRY_HOME"], ignore_errors=True)


def make_scry_copy(dest_dir: str | Path, name: str = "scry") -> Path:
    """Copy ./scry into dest_dir (executable). Used by `scry update` tests so the
    in-place swap mutates a throwaway file, never the repo's tracked scry."""
    dest = Path(dest_dir) / name
    shutil.copy2(SCRY, dest)
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR)
    return dest


# --------------------------------------------------------------------------- #
# FileServer — a localhost HTTP server for `scry update` (SCRY_UPDATE_URL).
# Serves a fixed byte payload; can simulate a bad Content-Length or an HTTP error.
# --------------------------------------------------------------------------- #
class FileServer(contextlib.AbstractContextManager):
    def __init__(self, payload: bytes, status: int = 200,
                 content_length: int | None = None):
        self.payload = payload
        self.status = status
        # None => honest length; otherwise advertise this (mismatch => truncation).
        self.content_length = content_length
        self._srv = None
        self._thread = None

    def __enter__(self) -> "FileServer":
        payload, status, clen = self.payload, self.status, self.content_length

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length",
                                 str(clen if clen is not None else len(payload)))
                self.end_headers()
                if status < 400:
                    self.wfile.write(payload)

            def log_message(self, *a):  # silence the server's stderr logging
                return

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self

    @property
    def url(self) -> str:
        host, port = self._srv.server_address
        return f"http://{host}:{port}/scry"

    def __exit__(self, *exc) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
        if self._thread:
            self._thread.join(timeout=2.0)
        return None


# --------------------------------------------------------------------------- #
# Misc test helpers
# --------------------------------------------------------------------------- #
class FakeTTY:
    """A writable stream that reports isatty()=True (or False) — for exercising
    color/animation/streaming branches that gate on a terminal."""

    def __init__(self, tty: bool = True):
        import io
        self._buf = io.StringIO()
        self._tty = tty

    def write(self, s):
        return self._buf.write(s)

    def flush(self):
        pass

    def isatty(self):
        return self._tty

    def getvalue(self):
        return self._buf.getvalue()


@contextlib.contextmanager
def env_vars(**kv):
    """Temporarily set/unset environment variables (value None => unset)."""
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
