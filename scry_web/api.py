"""FastAPI routes for the scry web app. Imports FastAPI (only loaded when the optional
web deps are installed). Every route reads the shared AppState wired in server.py.

The run model is create→poll: POST a message starts a run and returns it immediately;
the client polls GET /api/runs/{id}. Plan runs additionally accept POST .../answers to
walk the clarifying-question interview.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse


def add_routes(app, appstate) -> None:
    router = APIRouter(prefix="/api")
    L = appstate.locations
    R = appstate.runs

    # -- status / setup gate ---------------------------------------------- #
    @router.get("/status")
    async def status():
        return appstate.status()

    # -- locations --------------------------------------------------------- #
    @router.get("/locations")
    async def list_locations():
        out = []
        for loc in L.list():
            store = L.store_for(loc)
            convs = store.list_conversations(loc["id"])
            d = dict(loc)
            d["conversation_count"] = len(convs)
            out.append(d)
        return {"locations": out}

    @router.post("/locations")
    async def create_workspace(request: Request):
        body = await _json(request)
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        return {"location": L.create_workspace(name)}

    @router.post("/locations/open")
    async def open_project(request: Request):
        from .locations import LocationError
        body = await _json(request)
        path = (body.get("path") or "").strip()
        if not path:
            raise HTTPException(400, "path is required")
        try:
            return {"location": L.open_project(path)}
        except LocationError as e:
            raise HTTPException(400, str(e))

    @router.get("/locations/{location_id}/conversations")
    async def location_conversations(location_id: str):
        loc = L.get(location_id)
        if not loc:
            raise HTTPException(404, "location not found")
        store = L.store_for(loc)
        return {"location": loc, "conversations": store.list_conversations(location_id)}

    # -- conversations ----------------------------------------------------- #
    @router.post("/conversations")
    async def create_conversation(request: Request):
        body = await _json(request)
        location_id = body.get("location_id") or L.CONTEXTLESS_ID
        loc = L.get(location_id)
        if not loc:
            raise HTTPException(404, "location not found")
        store = L.store_for(loc)
        conv = store.create_conversation(location_id, body.get("title") or "Untitled")
        return {"conversation": conv, "location": loc}

    @router.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str):
        loc, store, conv = L.locate_conversation(conversation_id)
        if not conv:
            raise HTTPException(404, "conversation not found")
        return {
            "conversation": conv, "location": loc,
            "messages": store.list_messages(conversation_id),
            "runs": store.list_runs(conversation_id),
            "attachments": store.list_attachments(conversation_id),
        }

    @router.get("/conversations/{conversation_id}/export")
    async def export_conversation(conversation_id: str):
        loc, store, conv = L.locate_conversation(conversation_id)
        if not conv:
            raise HTTPException(404, "conversation not found")
        md = _export_markdown(conv, store.list_messages(conversation_id))
        return JSONResponse({"markdown": md, "filename":
                             f"scry-conversation-{conversation_id}.md"})

    @router.post("/conversations/{conversation_id}/messages")
    async def post_message(conversation_id: str, request: Request):
        loc, store, conv = L.locate_conversation(conversation_id)
        if not conv:
            raise HTTPException(404, "conversation not found")
        body = await _json(request)
        content = (body.get("content") or "").strip()
        if not content:
            raise HTTPException(400, "content is required")
        capability = body.get("capability") or "scry"
        if capability not in ("scry", "plan", "research"):
            raise HTTPException(400, f"unknown capability: {capability}")
        run = R.start(conv, loc, capability, content, body.get("options") or {},
                      body.get("attachment_ids") or [])
        return {"run": run}

    @router.post("/conversations/{conversation_id}/attachments")
    async def upload_attachment(conversation_id: str, file: UploadFile = File(...)):
        loc, store, conv = L.locate_conversation(conversation_id)
        if not conv:
            raise HTTPException(404, "conversation not found")
        data = await file.read()
        # Use the DB-sourced id (not the raw route param) for on-disk storage.
        rec = _save_attachment(L, loc, conv["id"], file.filename or "file", data)
        att = store.add_attachment(conversation_id, rec["filename"], rec["path"],
                                   rec["size"], rec["is_text"])
        return {"attachment": att}

    @router.post("/conversations/{conversation_id}/upgrade")
    async def upgrade_conversation(conversation_id: str, request: Request):
        from .locations import LocationError
        body = await _json(request)
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        try:
            result = L.upgrade_contextless_to_project(conversation_id, name)
        except LocationError as e:
            raise HTTPException(400, str(e))
        return result

    # -- runs -------------------------------------------------------------- #
    @router.get("/runs/{run_id}")
    async def get_run(run_id: str):
        run = R.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        return {"run": run}

    @router.post("/runs/{run_id}/answers")
    async def answer_run(run_id: str, request: Request):
        body = await _json(request)
        payload = {}
        if body.get("answers") is not None:
            payload["answers"] = body["answers"]
        if body.get("done"):
            payload["done"] = True
        run = R.answer_plan(run_id, payload)
        if not run:
            raise HTTPException(404, "run not found")
        return {"run": run}

    @router.get("/runs/{run_id}/download")
    async def download_artifact(run_id: str, index: int = 0):
        run = R.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        paths = run.get("artifact_paths") or []
        if index < 0 or index >= len(paths):
            raise HTTPException(404, "no such artifact")
        p = Path(paths[index])
        if not p.exists():
            raise HTTPException(404, "artifact file missing")
        return FileResponse(str(p), filename=p.name, media_type="text/markdown")

    # -- reveal (macOS) ---------------------------------------------------- #
    @router.post("/runs/{run_id}/reveal")
    async def reveal_artifact(run_id: str, index: int = 0):
        # Reveal a run artifact by (run_id, index) — never an arbitrary client path.
        # The path comes from the run record (server-written, under managed storage),
        # so the unauthenticated localhost server can't be steered to `open -R` files
        # outside what scry produced.
        run = R.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        artifacts = run.get("artifact_paths") or []
        if index < 0 or index >= len(artifacts):
            raise HTTPException(404, "no such artifact")
        p = Path(artifacts[index])
        if not p.exists():
            raise HTTPException(404, "artifact file missing")
        if sys.platform == "darwin":
            try:
                subprocess.run(["open", "-R", str(p)], timeout=10,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError) as e:
                raise HTTPException(500, f"reveal failed: {e}")
            return {"ok": True}
        return {"ok": False, "detail": "reveal is macOS-only"}

    app.include_router(router)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _json(request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — empty/invalid body -> {}
        return {}
    return body if isinstance(body, dict) else {}


def _save_attachment(L, location, conversation_id, filename, data):
    from . import attachments as att_mod
    return att_mod.save_upload(location, conversation_id, filename, data)


def _export_markdown(conv: dict, messages: list) -> str:
    lines = [f"# {conv.get('title') or 'Conversation'}", ""]
    for m in messages:
        who = "User" if m.get("role") == "user" else "Assistant"
        cap = f" · {m.get('capability')}" if m.get("capability") else ""
        lines.append(f"## {who}{cap}")
        lines.append("")
        lines.append(m.get("content") or "")
        lines.append("")
    return "\n".join(lines)
