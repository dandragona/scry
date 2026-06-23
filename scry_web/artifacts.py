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
        d = paths.runs_dir() / conversation_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def plan_out_path(location: dict, conversation_id: str, run_id: str) -> str:
    """The plan file path handed to scry's plan_step (`scry-plan-<id>.md`); its
    diagnostics file is written alongside it automatically."""
    return str(artifact_dir(location, conversation_id) / f"scry-plan-{run_id}.md")


def write_research(location: dict, conversation_id: str, run_id: str,
                   request: str, final: str) -> str | None:
    return _write(artifact_dir(location, conversation_id) / f"scry-research-{run_id}.md",
                  _framed("Deep research report", request, final))


def write_chat(location: dict, conversation_id: str, run_id: str,
               request: str, final: str) -> str | None:
    return _write(artifact_dir(location, conversation_id) / f"scry-chat-{run_id}.md",
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
