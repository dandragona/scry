"""FastAPI app factory + the `serve()` entry point behind `scry web`.

Security posture: the server is single-user, unauthenticated, and bound to localhost
by default. A Host/Origin-validation middleware rejects DNS-rebinding and cross-origin
requests (any page your browser visits could otherwise reach this port). NEVER expose
this on a routable interface without adding your own auth.
"""
from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import add_routes
from .state import AppState

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _allowed_hosts(host: str, port: int) -> set:
    hosts = {host, "localhost", "127.0.0.1", "[::1]", "::1"}
    out = set()
    for h in hosts:
        out.add(h.lower())
        out.add(f"{h}:{port}".lower())
    return out


def _host_ok(host_header: str, allowed: set) -> bool:
    return host_header.lower() in allowed


def _origin_ok(origin: str, allowed: set) -> bool:
    netloc = origin.split("://", 1)[-1].lower()
    return netloc in allowed


def create_app(config_path: str | None = None, host: str = "127.0.0.1",
               port: int = 8765) -> "FastAPI":
    app = FastAPI(title="scry web", docs_url=None, redoc_url=None, openapi_url=None)
    appstate = AppState(config_path=config_path)
    app.state.appstate = appstate
    allowed = _allowed_hosts(host, port)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host_header = request.headers.get("host") or ""
        if host_header and not _host_ok(host_header, allowed):
            return JSONResponse({"detail": "host not allowed"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and not _origin_ok(origin, allowed):
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        return await call_next(request)

    add_routes(app, appstate)

    # The vendored SPA, served at / with SPA fallback to index.html. Registered LAST so
    # the /api/* routes (added above) take precedence over the catch-all static mount.
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="spa")
    return app


def _safe_open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 — opening a browser is a nicety, never fatal
        pass


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
          config_path: str | None = None) -> int:
    """Launch the local web UI (blocks until Ctrl-C). Returns a process exit code."""
    try:
        import uvicorn
    except ImportError as e:
        print("scry web needs uvicorn (part of the optional web deps).\n"
              "  install:  pip install 'scry[web]'\n"
              f"  (import error: {e})", file=sys.stderr)
        return 1
    app = create_app(config_path=config_path, host=host, port=port)
    url = f"http://{host}:{port}"
    print(f"\n  scry web  →  {url}", file=sys.stderr)
    print("  single-user · localhost-only · press Ctrl-C to stop\n", file=sys.stderr)
    if open_browser:
        threading.Timer(1.2, lambda: _safe_open(url)).start()
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0
