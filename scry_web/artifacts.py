"""Where generated artifacts (plans, research reports, chat transcripts) are written.

For an opened project or managed workspace, artifacts are written IN PLACE at the
project root (CLI-style, e.g. ``scry-plan-<id>.md``). For a contextless session they
go under ``<base>/runs/<conversation_id>/``. Stdlib only.
"""
from __future__ import annotations

import time
from pathlib import Path

from . import paths


def artifact_dir(location: dict, conversation_id: str) -> Path:
    """The directory a run's artifacts are written to for this location."""
    if location.get("type") in ("project", "workspace") and location.get("root_path"):
        d = Path(location["root_path"])
    else:
        d = paths.runs_dir() / paths.safe_segment(conversation_id, fallback="conv")
    d.mkdir(parents=True, exist_ok=True)
    return d


def plan_out_path(location: dict, conversation_id: str, run_id: str) -> str:
    """The plan file path handed to scry's plan_step (`scry-plan-<id>.md`); its
    diagnostics file is written alongside it automatically."""
    rid = paths.safe_segment(run_id, fallback="run")
    return str(artifact_dir(location, conversation_id) / f"scry-plan-{rid}.md")


def write_research(location: dict, conversation_id: str, run_id: str,
                   request: str, final: str) -> str | None:
    rid = paths.safe_segment(run_id, fallback="run")
    return _write(artifact_dir(location, conversation_id) / f"scry-research-{rid}.md",
                  _framed("Deep research report", request, final))


def write_chat(location: dict, conversation_id: str, run_id: str,
               request: str, final: str) -> str | None:
    rid = paths.safe_segment(run_id, fallback="run")
    return _write(artifact_dir(location, conversation_id) / f"scry-chat-{rid}.md",
                  _framed("scry answer", request, final))


def _framed(kind: str, request: str, final: str) -> str:
    when = time.strftime("%Y-%m-%d %H:%M")
    return (f"# {kind}\n\n_{when}_\n\n## Request\n\n{request}\n\n## Answer\n\n"
            f"{final}\n")


def _write(path: Path, body: str) -> str | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return str(path)
    except OSError:
        return None
