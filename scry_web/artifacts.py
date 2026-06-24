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


def write_research(location: dict, conversation_id: str, run_id: str,
                   request: str, final: str, title: str | None = None) -> str | None:
    return _write(_named(location, conversation_id, "scry-research", run_id, title),
                  _framed("Deep research report", request, final))


def write_chat(location: dict, conversation_id: str, run_id: str,
               request: str, final: str, title: str | None = None) -> str | None:
    return _write(_named(location, conversation_id, "scry-chat", run_id, title),
                  _framed("scry answer", request, final))


def _named(location: dict, conversation_id: str, prefix: str, run_id: str,
           title: str | None) -> Path:
    """The artifact path for this run: `<prefix>-<topic>.md` when a meaningful title
    slug is given (with a `-2`/`-3` suffix on collision), else `<prefix>-<run_id>.md`."""
    directory = artifact_dir(location, conversation_id)
    slug = paths.safe_segment(title, fallback="") if title else ""
    stem = f"{prefix}-{slug}" if slug else f"{prefix}-{paths.safe_segment(run_id, fallback='run')}"
    return _unique(directory, stem)


def _unique(directory: Path, stem: str, ext: str = ".md") -> Path:
    """First `directory/<stem><ext>` (then `-2`, `-3`, …) that does not yet exist."""
    for i in range(1, 1000):
        cand = directory / f"{stem if i == 1 else f'{stem}-{i}'}{ext}"
        if not cand.exists():
            return cand
    return directory / f"{stem}-{int(time.time() * 1000)}{ext}"


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
