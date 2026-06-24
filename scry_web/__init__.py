"""scry_web — the optional local web UI for scry (`scry web`).

The core `scry` CLI stays a single, stdlib-only, zero-dependency file. This package
holds everything that needs third-party deps (FastAPI/uvicorn) plus the vendored,
pre-built static SPA. It is lazy-imported only by the `web` subcommand, so a scry
install without the web extras keeps working exactly as before.

Public entry point: `serve()` (used by `scry web`). Submodules engine/store/locations/
runs/artifacts/attachments/paths/state are stdlib-only and importable without FastAPI.
"""
from __future__ import annotations

__all__ = ["serve", "create_app"]


def serve(*args, **kwargs):
    # Lazy import so `import scry_web` (and the stdlib-only submodules) never require
    # FastAPI; the import error surfaces only when you actually launch the server.
    from .server import serve as _serve
    return _serve(*args, **kwargs)


def create_app(*args, **kwargs):
    from .server import create_app as _create_app
    return _create_app(*args, **kwargs)
