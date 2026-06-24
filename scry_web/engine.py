"""In-process bridge to the single-file `scry` engine — stdlib only (no FastAPI).

Reuses scry's Python internals directly (NO shelling out to the `scry` CLI): the
single-file script is loaded as a module exactly the way the test harness does
(`SourceFileLoader`), and its functions (`scry_run`, `plan_step`, `load_config`,
`resolve_command`, `_probe`) are called in-process. Every engine call is synchronous
(it owns its own `asyncio.run`) so the FastAPI layer can dispatch it through
`asyncio.to_thread` without nesting event loops.

Set ``SCRY_WEB_FAKE_ENGINE=1`` to short-circuit every call with canned structured
results — the API test suite uses this to exercise routes without scry or any model.
``SCRY_BIN`` overrides the path to the scry script (defaults to the sibling file).
"""
from __future__ import annotations

import asyncio
import os
import time
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import shutil

RESEARCH_FRAMING = (
    "Produce a thorough, well-structured deep-research report answering the request "
    "below. Use web search/fetch to ground every nontrivial claim in current sources, "
    "and cite them inline. Organize the report with a short executive summary, then "
    "detailed sections, then a list of sources. Be comprehensive and specific.\n\n"
    "Research request:\n"
)

_SCRY_MOD = None
_ATTACH_INLINE_CAP = 20000  # bytes of a text attachment inlined into the prompt


def fake_enabled() -> bool:
    """True when SCRY_WEB_FAKE_ENGINE is set — return canned results, never call scry."""
    return bool(os.environ.get("SCRY_WEB_FAKE_ENGINE"))


def _scry_path() -> Path:
    override = os.environ.get("SCRY_BIN")
    if override:
        return Path(override)
    sibling = Path(__file__).resolve().parent.parent / "scry"
    if sibling.exists():
        return sibling
    found = shutil.which("scry")
    if found:
        return Path(found).resolve()
    return sibling  # let the loader raise a clear error


def load_scry():
    """The `scry` CLI loaded as a module (cached; main() is not invoked on import)."""
    global _SCRY_MOD
    if _SCRY_MOD is None:
        path = _scry_path()
        loader = SourceFileLoader("scry_web_engine_sut", str(path))
        spec = spec_from_loader(loader.name, loader)
        mod = module_from_spec(spec)
        loader.exec_module(mod)
        _SCRY_MOD = mod
    return _SCRY_MOD


def load_config(config_path: str | None = None) -> dict:
    """scry's effective config (defaults backfilled), resolved like the CLI does."""
    return load_scry().load_config(config_path)


# --------------------------------------------------------------------------- #
# Option mapping — curated UI options -> scry settings + cli_overrides, matching
# exactly how scry's main() applies the equivalent flags on a normal run.
# --------------------------------------------------------------------------- #
_OVERRIDE_KEYS = ("effort", "max_tool_calls", "max_output_tokens", "timeout")


def _apply_options(cfg: dict, options: dict | None, *, force_web: bool = False) -> tuple:
    options = options or {}
    settings = dict(cfg["settings"])
    cli: dict = {}
    if force_web:
        settings["web_tools"] = True
        cli["web_tools"] = True
    elif options.get("web_tools") is not None:
        settings["web_tools"] = bool(options["web_tools"])
        cli["web_tools"] = bool(options["web_tools"])
    for k in _OVERRIDE_KEYS:
        v = options.get(k)
        if v not in (None, ""):
            settings[k] = v
            cli[k] = v
    return settings, cli


# --------------------------------------------------------------------------- #
# Prompt assembly (multi-turn + attachments + context)
# --------------------------------------------------------------------------- #
def _read_capped(path: str, cap: int) -> str:
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return f"<could not read attachment: {e}>"
    truncated = len(data) > cap
    text = data[:cap].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n… [truncated, {len(data)} bytes total]"
    return text


def build_contextual_prompt(history: list, content: str, context: str | None = None,
                            attachments: list | None = None,
                            attach_cap: int = _ATTACH_INLINE_CAP) -> str:
    """Assemble a single prompt from prior turns, an optional context field, attached
    files (text inlined under a size cap; binary/large referenced by path), and the
    current message. Prior model output + user answers are framed as data."""
    parts: list = []
    if history:
        convo = []
        for t in history:
            who = "User" if t.get("role") == "user" else "Assistant"
            convo.append(f"{who}: {t.get('content', '')}")
        parts.append("Conversation so far (most recent last):\n" + "\n\n".join(convo))
    if context:
        parts.append("Additional context provided by the user:\n" + context)
    if attachments:
        atts = []
        for a in attachments:
            name = a.get("filename", "file")
            if a.get("is_text"):
                body = _read_capped(a["path"], attach_cap)
                atts.append(f"--- attached file: {name} ---\n{body}")
            else:
                atts.append(f"--- attached file: {name} "
                            f"({a.get('size', '?')} bytes, not inlined) "
                            f"at {a.get('path')} ---")
        parts.append("Attached files:\n" + "\n\n".join(atts))
    parts.append("Current request:\n" + content)
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Synchronous engine calls (each owns its own asyncio.run; dispatch via to_thread)
# --------------------------------------------------------------------------- #
def _fake_result(prompt: str, mode: str) -> dict:
    return {
        "status": "ok", "mode": mode, "prompt": prompt,
        "final": f"[fake-engine] {mode} answer for: {prompt[:120]}",
        "responses": [
            {"label": "claude-opus", "model": "opus", "ok": True,
             "content": "fake proposer", "error": None, "seconds": 0.1},
            {"label": "codex-gpt", "model": "", "ok": True,
             "content": "fake proposer", "error": None, "seconds": 0.1},
        ],
        "analysis": {"consensus": ["c1"], "contradictions": [], "partial_coverage": [],
                     "unique_insights": ["u1"], "blind_spots": []},
        "cost": {"calls": 3, "total_usd": 0.0, "seconds": 0.3, "by_stage": []},
    }


def run_scry_sync(cfg: dict, prompt: str, options: dict | None = None,
                  cwd: str | None = None) -> dict:
    """One-shot scry fusion/synthesize run, in-process. Returns the full result dict."""
    if fake_enabled():
        return _fake_result(prompt, "fusion")
    scry = load_scry()
    mode = (options or {}).get("mode") or cfg.get("mode", "fusion")
    settings, cli = _apply_options(cfg, options)
    return asyncio.run(scry.scry_run(cfg, prompt, mode, settings,
                                     lambda _m: None, cli_overrides=cli, cwd=cwd))


def run_research_sync(cfg: dict, prompt: str, options: dict | None = None,
                      cwd: str | None = None) -> dict:
    """A web-on deep-research run: a fusion run with web tools forced on for every
    phase and a research-report framing wrapped around the prompt. Tagged mode=research."""
    if fake_enabled():
        r = _fake_result(prompt, "research")
        return r
    scry = load_scry()
    settings, cli = _apply_options(cfg, options, force_web=True)
    result = asyncio.run(scry.scry_run(
        cfg, RESEARCH_FRAMING + prompt, "fusion", settings,
        lambda _m: None, cli_overrides=cli, cwd=cwd))
    result["mode"] = "research"
    result["prompt"] = prompt
    return result


def topic_slug(cfg: dict, request: str, cwd: str | None = None) -> str:
    """A short, meaningful filename slug for `request` (a cheap LLM-generated title,
    falling back to the prompt's first words). Empty under the fake engine so tests
    fall back to run-id names. `cwd` is accepted for symmetry but ignored — scry runs
    the title call in its own throwaway dir."""
    if fake_enabled():
        return ""
    return load_scry().topic_slug(cfg, request) or ""


def plan_step(cfg: dict, request: str, options: dict | None = None, *,
              resume=False, repo_cwd: str | None = None, payload: dict | None = None,
              out: str | None = None, no_out: bool = False,
              out_dir: str | None = None) -> dict:
    """One headless round of the plan interview, in-process. Returns the envelope dict
    ({status: questions|ready|done|error, ...}) from scry's pure `plan_step` engine.
    `out_dir` lets the caller pass a directory and have scry pick a meaningful
    `scry-plan-<topic>.md` name inside it (an explicit `out` path still wins)."""
    if fake_enabled():
        return _fake_plan_step(request, resume, payload, out, no_out, out_dir)
    scry = load_scry()
    settings, cli = _apply_options(cfg, options)
    plan_settings = dict(cfg.get("plan", {}))
    if options and options.get("max_rounds") not in (None, ""):
        plan_settings["max_rounds"] = options["max_rounds"]
    return scry.plan_step(cfg, request, settings, plan_settings, resume=resume,
                          repo_cwd=repo_cwd, payload=payload, out=out, no_out=no_out,
                          cli_overrides=cli, out_dir=out_dir)


def _fake_plan_step(request, resume, payload, out, no_out, out_dir=None) -> dict:
    """Deterministic plan interview for SCRY_WEB_FAKE_ENGINE: one question round, then
    ready, then a drafted plan. Keyed off the answers payload so the API tests can drive
    the whole questions -> ready -> done loop."""
    payload = payload if isinstance(payload, dict) else {}
    rid = resume if isinstance(resume, str) else "fakeplan-1"
    if payload.get("done"):
        plan_md = "## Context\nFake plan.\n## Steps\n1. do it"
        plan_path = diag_path = None
        target = out or (str(Path(out_dir) / f"scry-plan-{rid}.md") if out_dir else None)
        if not no_out and target:
            try:
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                Path(target).write_text(plan_md + "\n")
                diag_path = target.replace(".md", ".diagnostics.md")
                Path(diag_path).write_text("# diagnostics\nfake")
                plan_path = target
            except OSError:
                plan_path = diag_path = None
        return {"status": "done", "id": rid, "final": plan_md,
                "plan_path": plan_path, "diagnostics_path": diag_path,
                "responses": _fake_result(request, "fusion")["responses"],
                "cost": {"calls": 3, "total_usd": 0.0, "seconds": 0.3}}
    if payload.get("answers"):
        return {"status": "ready", "id": rid}
    return {"status": "questions", "id": rid, "round": 1,
            "questions": [{"q": "What is the target platform?", "why": "shapes deps",
                           "options": ["linux", "macos"]}]}


# --------------------------------------------------------------------------- #
# Provider readiness — a non-billing probe mirroring `scry --check`, no paid calls.
# --------------------------------------------------------------------------- #
def provider_readiness(cfg: dict, mode: str = "fusion") -> dict:
    """Probe each configured provider CLI (installed? logged in?) WITHOUT any paid
    model call — reuses scry's resolve_command + _probe. Returns a structured report
    the UI surfaces so missing keys show up before a run instead of failing mid-run."""
    if fake_enabled():
        return {"ready": True, "providers": [
            {"name": "claude", "ok": True, "installed": True, "detail": "fake ok"}],
            "panel": [m.get("label", m["provider"]) for m in cfg.get("panel", [])]}
    scry = load_scry()
    providers = cfg.get("providers", {})
    used: list = [m["provider"] for m in cfg.get("panel", [])]
    if mode == "fusion":
        used.append(cfg.get("judge", {}).get("provider"))
    used.append(cfg.get("aggregator", {}).get("provider"))
    seen: list = []
    for p in used:
        if p and p not in seen:
            seen.append(p)
    out: list = []
    hard_fail = False
    for name in seen:
        p = providers.get(name)
        if not p:
            out.append({"name": name, "ok": False, "installed": False,
                        "detail": "unknown provider (not in config)"})
            hard_fail = True
            continue
        chk = p.get("check") or {}
        binary = p["cmd"][0]
        if scry.resolve_command(binary) is None:
            out.append({"name": name, "ok": False, "installed": False,
                        "binary": binary, "detail": f"'{binary}' not found",
                        "install": chk.get("install")})
            hard_fail = True
            continue
        probe = chk.get("probe")
        if not probe:
            out.append({"name": name, "ok": True, "installed": True,
                        "binary": binary, "detail": "installed"})
            continue
        ok, detail = scry._probe(probe, p.get("env_unset"),
                                 chk.get("expect_code", 0), chk.get("expect_text", ""),
                                 chk.get("timeout", 15))
        if not ok:
            out.append({"name": name, "ok": False, "installed": True,
                        "binary": binary, "detail": detail or "probe failed",
                        "hint": chk.get("hint")})
            hard_fail = True
        else:
            out.append({"name": name, "ok": True, "installed": True, "binary": binary,
                        "detail": detail or ("logged in" if chk.get("verifies_auth")
                                             else "installed & runnable")})
    panel = [m.get("label", m["provider"]) for m in cfg.get("panel", [])]
    return {"ready": not hard_fail, "providers": out, "panel": panel}


def has_config_file(config_path: str | None = None) -> bool:
    """Whether a real scry config file exists (the setup gate keys off this — if not,
    the UI directs the user to run `scry init` first)."""
    scry = load_scry()
    if config_path:
        return Path(config_path).exists()
    if (Path.cwd() / scry.LOCAL_CONFIG_NAME).exists():
        return True
    return scry.global_config_path().exists()


def summarize_run_record(result: dict) -> dict:
    """The persisted projection of a finished run result (drop the transient flag)."""
    return {k: v for k, v in result.items() if k != "streamed"}


def mirror_to_cli_history(result: dict, run_id: str | None = None) -> None:
    """Mirror a completed run into the CLI's global ~/.scry log (best-effort)."""
    if fake_enabled():
        return
    try:
        load_scry().save_run(result, run_id)
    except Exception:  # noqa: BLE001 — mirroring must never break a finished run
        pass


def now() -> float:
    return time.time()
