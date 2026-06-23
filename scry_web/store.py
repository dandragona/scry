"""SQLite persistence for the scry web app — stdlib `sqlite3` only.

One `Store` wraps one database file. The global registry DB (``web.db``) holds the
``locations`` table plus every contextless conversation; each opened project /
workspace keeps its own history DB under ``<root>/.scry/web/history.db`` so it stays
self-contained and CLI-openable.

Threading model: FastAPI dispatches blocking work onto a thread pool, so a single
shared connection is unsafe. Every method opens its own short-lived connection
(WAL mode → concurrent readers don't block the writer) and closes it. Schema is
created/migrated idempotently on first open.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA_VERSION = 1


def new_id(prefix: str = "") -> str:
    return (prefix + uuid.uuid4().hex[:20]) if prefix else uuid.uuid4().hex[:24]


def _dumps(v) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False)


def _loads(v):
    if v is None:
        return None
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return None


class Store:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    # -- connection -------------------------------------------------------- #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _migrate(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA_SQL)
            cur = conn.execute("SELECT MAX(version) AS v FROM schema_migrations")
            row = cur.fetchone()
            if not row or row["v"] is None:
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, time.time()))
            conn.commit()
        finally:
            conn.close()

    # -- locations (registry DB only) ------------------------------------- #
    def upsert_location(self, loc: dict) -> dict:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO locations(id, type, name, root_path, db_path, config_path, "
                "created_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET type=excluded.type, name=excluded.name, "
                "root_path=excluded.root_path, db_path=excluded.db_path, "
                "config_path=excluded.config_path",
                (loc["id"], loc["type"], loc.get("name"), loc.get("root_path"),
                 loc.get("db_path"), loc.get("config_path"),
                 loc.get("created_at") or time.time()))
            conn.commit()
        finally:
            conn.close()
        return self.get_location(loc["id"])

    def get_location(self, location_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM locations WHERE id=?",
                               (location_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_location_by_root(self, root_path: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM locations WHERE root_path=?",
                               (root_path,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_locations(self) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM locations ORDER BY created_at ASC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # -- conversations ----------------------------------------------------- #
    def create_conversation(self, location_id: str, title: str,
                            conversation_id: str | None = None) -> dict:
        cid = conversation_id or new_id("c_")
        ts = time.time()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO conversations(id, location_id, title, created_at, "
                "updated_at) VALUES (?,?,?,?,?)",
                (cid, location_id, title or "Untitled", ts, ts))
            conn.commit()
        finally:
            conn.close()
        return self.get_conversation(cid)

    def get_conversation(self, conversation_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM conversations WHERE id=?",
                               (conversation_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_conversations(self, location_id: str) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE location_id=? "
                "ORDER BY updated_at DESC", (location_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def touch_conversation(self, conversation_id: str, title: str | None = None) -> None:
        conn = self._connect()
        try:
            if title is not None:
                conn.execute("UPDATE conversations SET updated_at=?, title=? WHERE id=?",
                             (time.time(), title, conversation_id))
            else:
                conn.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                             (time.time(), conversation_id))
            conn.commit()
        finally:
            conn.close()

    # -- messages ---------------------------------------------------------- #
    def add_message(self, conversation_id: str, role: str, content: str,
                    capability: str | None = None, run_id: str | None = None,
                    attachments: list | None = None) -> dict:
        mid = new_id("m_")
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO messages(id, conversation_id, role, content, capability, "
                "run_id, attachments_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (mid, conversation_id, role, content, capability, run_id,
                 _dumps(attachments), time.time()))
            conn.commit()
        finally:
            conn.close()
        return self.get_message(mid)

    def get_message(self, message_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM messages WHERE id=?",
                               (message_id,)).fetchone()
            return _message_out(row) if row else None
        finally:
            conn.close()

    def list_messages(self, conversation_id: str) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC",
                (conversation_id,)).fetchall()
            return [_message_out(r) for r in rows]
        finally:
            conn.close()

    # -- runs -------------------------------------------------------------- #
    def create_run(self, run_id: str, conversation_id: str, capability: str,
                   status: str, prompt: str, options: dict | None) -> dict:
        ts = time.time()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO runs(id, conversation_id, capability, status, prompt, "
                "options_json, round, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, conversation_id, capability, status, prompt,
                 _dumps(options), 0, ts, ts))
            conn.commit()
        finally:
            conn.close()
        return self.get_run(run_id)

    def update_run(self, run_id: str, **fields) -> dict | None:
        if not fields:
            return self.get_run(run_id)
        cols = []
        vals = []
        json_cols = {"questions", "responses", "analysis", "cost", "transcript",
                     "artifact_paths"}
        for k, v in fields.items():
            col = k + "_json" if k in json_cols else k
            cols.append(f"{col}=?")
            vals.append(_dumps(v) if k in json_cols else v)
        cols.append("updated_at=?")
        vals.append(time.time())
        vals.append(run_id)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE runs SET {', '.join(cols)} WHERE id=?", vals)
            conn.commit()
        finally:
            conn.close()
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return _run_out(row) if row else None
        finally:
            conn.close()

    # -- attachments ------------------------------------------------------- #
    def add_attachment(self, conversation_id: str, filename: str, path: str,
                       size: int, is_text: bool) -> dict:
        aid = new_id("a_")
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO attachments(id, conversation_id, filename, path, size, "
                "is_text, created_at) VALUES (?,?,?,?,?,?,?)",
                (aid, conversation_id, filename, path, size, 1 if is_text else 0,
                 time.time()))
            conn.commit()
        finally:
            conn.close()
        return self.get_attachment(aid)

    def get_attachment(self, attachment_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM attachments WHERE id=?",
                               (attachment_id,)).fetchone()
            return _attachment_out(row) if row else None
        finally:
            conn.close()

    def get_attachments(self, ids: list) -> list:
        if not ids:
            return []
        conn = self._connect()
        try:
            qmarks = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT * FROM attachments WHERE id IN ({qmarks})", ids).fetchall()
            by_id = {r["id"]: _attachment_out(r) for r in rows}
            return [by_id[i] for i in ids if i in by_id]
        finally:
            conn.close()

    def list_attachments(self, conversation_id: str) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM attachments WHERE conversation_id=? ORDER BY created_at ASC",
                (conversation_id,)).fetchall()
            return [_attachment_out(r) for r in rows]
        finally:
            conn.close()

    def list_runs(self, conversation_id: str) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM runs WHERE conversation_id=? ORDER BY created_at ASC",
                (conversation_id,)).fetchall()
            return [_run_out(r) for r in rows]
        finally:
            conn.close()

    # -- raw row copies (faithful migration, ids/timestamps preserved) ----- #
    def insert_conversation_row(self, d: dict) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO conversations(id, location_id, title, "
                "created_at, updated_at) VALUES (?,?,?,?,?)",
                (d["id"], d["location_id"], d.get("title"), d.get("created_at"),
                 d.get("updated_at")))
            conn.commit()
        finally:
            conn.close()

    def insert_message_row(self, d: dict) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO messages(id, conversation_id, role, content, "
                "capability, run_id, attachments_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (d["id"], d["conversation_id"], d.get("role"), d.get("content"),
                 d.get("capability"), d.get("run_id"),
                 _dumps(d.get("attachments")), d.get("created_at")))
            conn.commit()
        finally:
            conn.close()

    def insert_attachment_row(self, d: dict) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO attachments(id, conversation_id, filename, "
                "path, size, is_text, created_at) VALUES (?,?,?,?,?,?,?)",
                (d["id"], d["conversation_id"], d.get("filename"), d.get("path"),
                 d.get("size"), 1 if d.get("is_text") else 0, d.get("created_at")))
            conn.commit()
        finally:
            conn.close()

    def delete_conversation(self, conversation_id: str) -> None:
        """Remove a conversation and all its messages/runs/attachments from THIS db
        (used when a contextless conversation is moved into a promoted project)."""
        conn = self._connect()
        try:
            for table in ("messages", "runs", "attachments"):
                conn.execute(f"DELETE FROM {table} WHERE conversation_id=?",
                             (conversation_id,))
            conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            conn.commit()
        finally:
            conn.close()

    def insert_run_row(self, d: dict) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO runs(id, conversation_id, capability, status, "
                "prompt, options_json, engine_run_id, round, questions_json, final, "
                "responses_json, analysis_json, cost_json, transcript_json, "
                "artifact_paths_json, error, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d["id"], d["conversation_id"], d.get("capability"), d.get("status"),
                 d.get("prompt"), _dumps(d.get("options")), d.get("engine_run_id"),
                 d.get("round"), _dumps(d.get("questions")), d.get("final"),
                 _dumps(d.get("responses")), _dumps(d.get("analysis")),
                 _dumps(d.get("cost")), _dumps(d.get("transcript")),
                 _dumps(d.get("artifact_paths")), d.get("error"),
                 d.get("created_at"), d.get("updated_at")))
            conn.commit()
        finally:
            conn.close()


def _message_out(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["attachments"] = _loads(d.pop("attachments_json", None)) or []
    return d


def _attachment_out(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["is_text"] = bool(d.get("is_text"))
    return d


def _run_out(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in ("options", "questions", "responses", "analysis", "cost", "transcript",
              "artifact_paths"):
        d[k] = _loads(d.pop(k + "_json", None))
    return d


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at REAL
);
CREATE TABLE IF NOT EXISTS locations (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT,
    root_path TEXT,
    db_path TEXT,
    config_path TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL,
    title TEXT,
    created_at REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    capability TEXT,
    run_id TEXT,
    attachments_json TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    capability TEXT,
    status TEXT,
    prompt TEXT,
    options_json TEXT,
    engine_run_id TEXT,
    round INTEGER,
    questions_json TEXT,
    final TEXT,
    responses_json TEXT,
    analysis_json TEXT,
    cost_json TEXT,
    transcript_json TEXT,
    artifact_paths_json TEXT,
    error TEXT,
    created_at REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    filename TEXT,
    path TEXT,
    size INTEGER,
    is_text INTEGER,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_conv_location ON conversations(location_id);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_run_conv ON runs(conversation_id);
CREATE INDEX IF NOT EXISTS idx_att_conv ON attachments(conversation_id);
"""
