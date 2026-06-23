"""Filesystem layout for the scry web app — stdlib only.

Everything the web app persists lives under one base directory (default
``~/.config/scry``), so a single env var (``SCRY_WEB_HOME``) relocates the whole
lot for tests and never touches the real config:

    <base>/web/web.db                 global registry DB (locations + contextless data)
    <base>/web/attachments/<conv>/    contextless attachment storage
    <base>/workspaces/<slug>/         managed, CLI-compatible scry project scaffolds
    <base>/runs/<conv>/               contextless run artifacts (plans, research reports)

A workspace or opened project keeps its OWN history DB + attachments under its
``<root>/.scry/web/`` so it stays self-contained and openable by the CLI.
"""
from __future__ import annotations

import os
from pathlib import Path


def web_base() -> Path:
    """The base directory for all web-app storage (honors $SCRY_WEB_HOME)."""
    return Path(os.environ.get("SCRY_WEB_HOME") or (Path.home() / ".config" / "scry"))


def web_dir() -> Path:
    """`<base>/web` — holds the global registry DB and contextless attachments."""
    d = web_base() / "web"
    d.mkdir(parents=True, exist_ok=True)
    return d


def registry_db_path() -> Path:
    """The global registry DB: the locations table + all contextless conversations."""
    return web_dir() / "web.db"


def workspaces_dir() -> Path:
    """`<base>/workspaces` — where managed standalone workspaces are scaffolded."""
    d = web_base() / "workspaces"
    d.mkdir(parents=True, exist_ok=True)
    return d


def runs_dir() -> Path:
    """`<base>/runs` — default artifact destination for contextless sessions."""
    d = web_base() / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def location_db_path(root: str) -> Path:
    """The per-location history DB inside an opened project / workspace."""
    return Path(root) / ".scry" / "web" / "history.db"


def slugify(name: str, fallback: str = "workspace") -> str:
    """A filesystem-safe slug for a workspace directory name."""
    out = []
    for ch in (name or "").strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_", "."):
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:60] or fallback
