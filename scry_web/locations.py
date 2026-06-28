"""Locations: contextless scratchpad, managed workspaces, and opened projects —
plus promoting a contextless session into a real, CLI-openable scry project.

A *location* is where conversations + artifacts live:

  * **contextless** — the default scratchpad; conversations live in the global
    registry DB, artifacts under ``<base>/runs/<conversation>/``.
  * **workspace** — a managed scry project scaffold under ``<base>/workspaces/<slug>``
    (dir + ``git init`` + optional ``scry.config.json`` + ``.scry/web/history.db``).
  * **project** — any directory the user opens; gets its own ``.scry/web/history.db``
    going forward, artifacts written in place.

Workspaces/projects keep their own history DB so the directory is self-contained and
the existing `scry` CLI can be run inside it. Stdlib only (subprocess for `git init`).
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from . import engine, paths
from .store import Store, new_id


class LocationError(Exception):
    """An opened/created location request could not be satisfied (bad path, etc.)."""


class LocationManager:
    CONTEXTLESS_ID = "contextless"

    def __init__(self):
        self.registry = Store(paths.registry_db_path())
        self._ensure_contextless()

    def _ensure_contextless(self) -> None:
        if not self.registry.get_location(self.CONTEXTLESS_ID):
            self.registry.upsert_location({
                "id": self.CONTEXTLESS_ID, "type": "contextless",
                "name": "Scratchpad", "root_path": None,
                "db_path": str(paths.registry_db_path()), "config_path": None,
                "created_at": time.time()})

    # -- lookups ----------------------------------------------------------- #
    def get(self, location_id: str) -> dict | None:
        return self.registry.get_location(location_id)

    def list(self) -> list:
        return self.registry.list_locations()

    def store_for(self, location: dict) -> Store:
        """The history DB store backing this location (registry DB for contextless)."""
        if location["type"] == "contextless":
            return self.registry
        return Store(location["db_path"])

    def locate_conversation(self, conversation_id: str):
        """Find which location holds a conversation. Returns (location, store, conv)
        or (None, None, None). Checks the contextless/registry DB first, then each
        registered project/workspace."""
        conv = self.registry.get_conversation(conversation_id)
        if conv:
            return self.get(conv["location_id"]), self.registry, conv
        for loc in self.list():
            if loc["type"] == "contextless":
                continue
            store = self.store_for(loc)
            conv = store.get_conversation(conversation_id)
            if conv:
                return loc, store, conv
        return None, None, None

    # -- creation ---------------------------------------------------------- #
    def _unique_workspace_root(self, name: str) -> Path:
        base = paths.workspaces_dir()
        slug = paths.slugify(name)
        root = base / slug
        n = 1
        while root.exists():
            root = base / f"{slug}-{n}"
            n += 1
        return root

    @staticmethod
    def _ensure_gitignored(root: Path, entry: str) -> None:
        """Make sure `root/.gitignore` ignores `entry`. Creates the file if absent,
        appends the entry if missing, and never duplicates it. Best-effort: a
        write failure must not break scaffolding."""
        gi = root / ".gitignore"
        try:
            existing = gi.read_text() if gi.exists() else ""
            if entry in existing.splitlines():
                return
            sep = "" if (not existing or existing.endswith("\n")) else "\n"
            gi.write_text(f"{existing}{sep}{entry}\n")
        except OSError:
            pass  # .gitignore is a privacy nicety — never fail the scaffold over it

    def _scaffold(self, root: Path, name: str, loc_type: str,
                  config_path: str | None = None) -> dict:
        """Build a CLI-compatible scry project scaffold at `root` and register it:
        the directory, `git init`, an optional copied `scry.config.json`, the standard
        `.scry/` structure, and a per-location history DB."""
        root.mkdir(parents=True, exist_ok=True)
        # git init — best effort; a workspace is still valid without git.
        if not (root / ".git").exists():
            try:
                subprocess.run(["git", "init", "-q"], cwd=str(root),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass  # no git on PATH (or it failed) — the workspace is still valid
        # Keep the per-project web store (.scry/web/history.db — plaintext prompt/answer
        # history) out of version control so a shared/committed dir can't leak it.
        self._ensure_gitignored(root, ".scry/")
        # Seed scry.config.json from the user's global config so the panel matches.
        cfg_dest = root / "scry.config.json"
        written_config = None
        if not cfg_dest.exists():
            try:
                src = engine.load_scry().global_config_path()
                if src.exists():
                    shutil.copy2(str(src), str(cfg_dest))
                    written_config = str(cfg_dest)
            except OSError:
                written_config = None
        else:
            written_config = str(cfg_dest)
        # Standard .scry structure + the per-location history DB.
        db_path = paths.location_db_path(str(root))
        Store(db_path)  # creates .scry/web/history.db with the schema
        loc = {
            "id": new_id("loc_"), "type": loc_type, "name": name or root.name,
            "root_path": str(root), "db_path": str(db_path),
            "config_path": config_path or written_config, "created_at": time.time(),
        }
        return self.registry.upsert_location(loc)

    def create_workspace(self, name: str) -> dict:
        root = self._unique_workspace_root(name or "workspace")
        return self._scaffold(root, name or root.name, "workspace")

    def open_project(self, path: str) -> dict:
        p = Path(path).expanduser()
        try:
            p = p.resolve()
        except OSError as e:
            raise LocationError(f"cannot resolve path: {e}")
        if not p.exists() or not p.is_dir():
            raise LocationError(f"not a directory: {p}")
        existing = self.registry.get_location_by_root(str(p))
        if existing:
            return existing
        db_path = paths.location_db_path(str(p))
        try:
            Store(db_path)  # create <root>/.scry/web/history.db going forward
        except OSError as e:
            # Unwritable target (e.g. /etc) — surface a clean client error instead
            # of a 500 leaking a traceback. api.py maps LocationError -> HTTP 400.
            raise LocationError(f"cannot open project at {p}: {e}")
        cfg = p / "scry.config.json"
        loc = {
            "id": new_id("loc_"), "type": "project", "name": p.name,
            "root_path": str(p), "db_path": str(db_path),
            "config_path": str(cfg) if cfg.exists() else None,
            "created_at": time.time(),
        }
        return self.registry.upsert_location(loc)

    # -- upgrade contextless -> project ------------------------------------ #
    def upgrade_contextless_to_project(self, conversation_id: str, name: str) -> dict:
        """Promote one contextless conversation into a full scry project scaffold:
        scaffold the dir (+git +config), migrate the conversation's history (messages,
        runs) and artifacts/attachments into it, seed a README from the request, and
        return {location, conversation_id}. The new directory is CLI-openable."""
        conv = self.registry.get_conversation(conversation_id)
        if not conv:
            raise LocationError("conversation not found")
        if conv["location_id"] != self.CONTEXTLESS_ID:
            raise LocationError("only contextless conversations can be upgraded")

        root = self._unique_workspace_root(name or conv.get("title") or "project")
        location = self._scaffold(root, name or conv.get("title") or root.name, "project")
        dst = self.store_for(location)
        src = self.registry

        # Conversation row (preserve id + title; re-home to the new location).
        dst.insert_conversation_row({
            "id": conv["id"], "location_id": location["id"], "title": conv.get("title"),
            "created_at": conv.get("created_at"), "updated_at": time.time()})

        # Attachments: copy files into the project, remap id -> new record/path.
        path_remap: dict = {}
        for att in src.list_attachments(conversation_id):
            new_path = att.get("path")
            try:
                if att.get("path") and Path(att["path"]).exists():
                    from . import attachments as att_mod
                    dest = att_mod.attach_dir(location, conv["id"]) / Path(att["path"]).name
                    shutil.copy2(att["path"], str(dest))
                    new_path = str(dest)
            except OSError:
                pass  # unreadable source — keep the original path, don't block promote
            rec = dict(att)
            rec["path"] = new_path
            dst.insert_attachment_row(rec)
            path_remap[att.get("path")] = new_path

        # Runs: copy rows; copy artifact files into the project root, rewrite paths.
        for run in src.list_runs(conversation_id):
            new_artifacts = []
            for ap in (run.get("artifact_paths") or []):
                np = ap
                try:
                    if ap and Path(ap).exists():
                        dest = Path(location["root_path"]) / Path(ap).name
                        shutil.copy2(ap, str(dest))
                        np = str(dest)
                except OSError:
                    pass  # unreadable artifact — keep its original path
                new_artifacts.append(np)
            run = dict(run)
            run["artifact_paths"] = new_artifacts or run.get("artifact_paths")
            dst.insert_run_row(run)

        # Messages: copy rows, remapping any inline attachment paths.
        for msg in src.list_messages(conversation_id):
            atts = []
            for a in (msg.get("attachments") or []):
                a = dict(a)
                if a.get("path") in path_remap:
                    a["path"] = path_remap[a["path"]]
                atts.append(a)
            dst.insert_message_row({
                "id": msg["id"], "conversation_id": conversation_id,
                "role": msg.get("role"), "content": msg.get("content"),
                "capability": msg.get("capability"), "run_id": msg.get("run_id"),
                "attachments": atts, "created_at": msg.get("created_at")})

        # Seed a README from the first user request.
        first_user = next((m for m in src.list_messages(conversation_id)
                           if m.get("role") == "user"), None)
        if first_user:
            try:
                (Path(location["root_path"]) / "README.md").write_text(
                    f"# {location['name']}\n\n"
                    f"Promoted from a scry web scratchpad session.\n\n"
                    f"## Original request\n\n{first_user.get('content', '')}\n")
            except OSError:
                pass  # README is a nicety — never fail the promote over it

        # Move semantics: the conversation now lives in the project, so drop the
        # original rows from the contextless registry (its artifact files are left
        # in place under <base>/runs/ harmlessly; the canonical copies are in-project).
        src.delete_conversation(conversation_id)
        return {"location": location, "conversation_id": conversation_id}
