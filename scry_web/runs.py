"""Run lifecycle: create → poll. A run is started by POSTing a message; the server
executes it as an in-process asyncio task and the client polls for status. Every
engine call is dispatched through ``asyncio.to_thread`` (the engine owns its own
event loop) and gated by a small concurrency semaphore so concurrent full-panel
fan-outs don't pile up.

Run statuses: ``running | questions | ready | done | error``. One-shot scry/research
runs go running→done; plan runs walk running→questions→…→ready→done, reusing one
``engine_run_id`` across every interview step (scry's resume checkpoint).

In-flight runs live only in memory (loss-on-disconnect is acceptable for v1, no
cancel); completed runs are persisted in the location's SQLite store.
"""
from __future__ import annotations

import asyncio
import threading

from . import artifacts, engine
from .store import new_id

MAX_CONCURRENT = 4


class RunManager:
    def __init__(self, app):
        self.app = app
        self._sem = None
        self._lock = threading.Lock()
        self._tasks: dict = {}          # run_id -> in-flight asyncio.Task
        self._index: dict = {}          # run_id -> {"location_id"} (fast locate)

    def _sema(self) -> asyncio.Semaphore:
        # Created lazily inside a running loop (a route handler).
        if self._sem is None:
            self._sem = asyncio.Semaphore(MAX_CONCURRENT)
        return self._sem

    # -- start ------------------------------------------------------------- #
    def start(self, conversation: dict, location: dict, capability: str,
              content: str, options: dict | None, attachment_ids: list | None) -> dict:
        store = self.app.locations.store_for(location)
        options = options or {}
        cid = conversation["id"]
        # Prior turns (before this message) become multi-turn context.
        history = [{"role": m["role"], "content": m["content"]}
                   for m in store.list_messages(cid) if m.get("content")]
        attachments = store.get_attachments(attachment_ids or [])
        store.add_message(cid, "user", content, capability=capability,
                          attachments=attachments)
        if not conversation.get("title") or conversation.get("title") in ("Untitled", ""):
            store.touch_conversation(cid, title=content.strip()[:60] or "Untitled")
        else:
            store.touch_conversation(cid)

        run_id = new_id("r_")
        store.create_run(run_id, cid, capability, "running", content, options)
        with self._lock:
            self._index[run_id] = {"location_id": location["id"]}

        if capability == "plan":
            task = asyncio.create_task(self._plan_step(
                run_id, conversation, location, content, options,
                resume=False, payload=None))
        else:
            prompt = engine.build_contextual_prompt(
                history, content, options.get("context"), attachments)
            task = asyncio.create_task(self._run_chat(
                run_id, conversation, location, capability, content, prompt, options))
        self._tasks[run_id] = task
        return store.get_run(run_id)

    # -- one-shot scry / research ------------------------------------------ #
    async def _run_chat(self, run_id, conversation, location, capability, request,
                        prompt, options) -> None:
        store = self.app.locations.store_for(location)
        cfg = self.app.config()
        cwd = location.get("root_path")  # None for contextless (scry scrubs a temp cwd)
        try:
            async with self._sema():
                fn = engine.run_research_sync if capability == "research" else engine.run_scry_sync
                result = await asyncio.to_thread(fn, cfg, prompt, options, cwd)
        except Exception as e:  # noqa: BLE001 — AllPanelsFailed or anything else
            store.update_run(run_id, status="error", error=str(e))
            store.add_message(conversation["id"], "assistant",
                              f"Run failed: {e}", capability=capability, run_id=run_id)
            return
        final = result.get("final") or ""
        if capability == "research":
            ap = artifacts.write_research(location, conversation["id"], run_id,
                                          request, final)
        else:
            ap = artifacts.write_chat(location, conversation["id"], run_id,
                                      request, final)
        store.update_run(run_id, status="done", final=final,
                         responses=result.get("responses"),
                         analysis=result.get("analysis"), cost=result.get("cost"),
                         artifact_paths=[ap] if ap else [])
        store.add_message(conversation["id"], "assistant", final,
                          capability=capability, run_id=run_id)
        store.touch_conversation(conversation["id"])
        engine.mirror_to_cli_history(result, run_id)

    # -- interactive plan -------------------------------------------------- #
    async def _plan_step(self, run_id, conversation, location, request, options,
                         *, resume, payload) -> None:
        store = self.app.locations.store_for(location)
        cfg = self.app.config()
        repo_cwd = location.get("root_path")
        out_path = artifacts.plan_out_path(location, conversation["id"], run_id)

        def _call():
            return engine.plan_step(cfg, request, options, resume=resume,
                                    repo_cwd=repo_cwd, payload=payload,
                                    out=out_path, no_out=False)
        try:
            async with self._sema():
                env = await asyncio.to_thread(_call)
        except Exception as e:  # noqa: BLE001
            store.update_run(run_id, status="error", error=str(e))
            return
        self._apply_plan_envelope(run_id, conversation, location, env)

    def _apply_plan_envelope(self, run_id, conversation, location, env) -> None:
        store = self.app.locations.store_for(location)
        status = env.get("status")
        if status == "questions":
            store.update_run(run_id, status="questions", engine_run_id=env.get("id"),
                             round=env.get("round"), questions=env.get("questions"))
        elif status == "ready":
            store.update_run(run_id, status="ready", engine_run_id=env.get("id"))
        elif status == "done":
            final = env.get("final") or ""
            aps = [p for p in (env.get("plan_path"), env.get("diagnostics_path")) if p]
            store.update_run(run_id, status="done", engine_run_id=env.get("id"),
                             final=final, responses=env.get("responses"),
                             cost=env.get("cost"), artifact_paths=aps, questions=None)
            store.add_message(conversation["id"], "assistant", final,
                              capability="plan", run_id=run_id)
            store.touch_conversation(conversation["id"])
        else:  # error or unknown
            store.update_run(run_id, status="error",
                             error=env.get("error") or "unknown plan error")

    def answer_plan(self, run_id: str, payload: dict) -> dict | None:
        location, conversation, store, run = self.locate(run_id)
        if not run:
            return None
        if run["capability"] != "plan":
            return run
        store.update_run(run_id, status="running")
        options = run.get("options") or {}
        task = asyncio.create_task(self._plan_step(
            run_id, conversation, location, run["prompt"], options,
            resume=run.get("engine_run_id") or False, payload=payload))
        self._tasks[run_id] = task
        return store.get_run(run_id)

    # -- lookup ------------------------------------------------------------ #
    def locate(self, run_id: str):
        """Return (location, conversation, store, run) for a run id, or 4×None."""
        meta = None
        with self._lock:
            meta = self._index.get(run_id)
        if meta:
            loc = self.app.locations.get(meta["location_id"])
            if loc:
                store = self.app.locations.store_for(loc)
                run = store.get_run(run_id)
                if run:
                    conv = store.get_conversation(run["conversation_id"])
                    return loc, conv, store, run
        for loc in self.app.locations.list():
            store = self.app.locations.store_for(loc)
            run = store.get_run(run_id)
            if run:
                conv = store.get_conversation(run["conversation_id"])
                with self._lock:
                    self._index[run_id] = {"location_id": loc["id"]}
                return loc, conv, store, run
        return None, None, None, None

    def get_run(self, run_id: str) -> dict | None:
        _loc, _conv, _store, run = self.locate(run_id)
        return run
