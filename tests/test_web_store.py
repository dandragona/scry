"""scry_web.store — SQLite schema, migrations, JSON round-trips, per-location
isolation. Stdlib only (no FastAPI required)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scry_web.store import Store, new_id  # noqa: E402


class StoreSchemaTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="scry-web-store-")
        self.db = os.path.join(self.dir, "a.db")
        self.s = Store(self.db)

    def test_migrations_recorded_and_idempotent(self):
        conn = self.s._connect()
        try:
            rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        finally:
            conn.close()
        self.assertTrue(rows)
        # Re-opening the same DB must not error or duplicate the migration row.
        Store(self.db)
        conn = self.s._connect()
        try:
            n = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(n, 1)

    def test_wal_enabled(self):
        conn = self.s._connect()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(mode.lower(), "wal")

    def test_conversation_message_run_roundtrip(self):
        self.s.upsert_location({"id": "L", "type": "workspace", "name": "n",
                                "db_path": self.db})
        conv = self.s.create_conversation("L", "Title")
        self.assertEqual(conv["title"], "Title")
        m = self.s.add_message(conv["id"], "user", "hi", capability="scry",
                               attachments=[{"filename": "a.txt"}])
        self.assertEqual(m["attachments"][0]["filename"], "a.txt")
        run = self.s.create_run("r1", conv["id"], "scry", "running", "hi", {"mode": "fusion"})
        self.assertEqual(run["options"]["mode"], "fusion")
        self.s.update_run("r1", status="done", final="answer",
                          responses=[{"label": "x", "ok": True}],
                          analysis={"consensus": ["c"]}, cost={"calls": 3},
                          artifact_paths=["/tmp/p.md"])
        r = self.s.get_run("r1")
        self.assertEqual(r["status"], "done")
        self.assertEqual(r["final"], "answer")
        self.assertEqual(r["responses"][0]["label"], "x")
        self.assertEqual(r["analysis"]["consensus"], ["c"])
        self.assertEqual(r["cost"]["calls"], 3)
        self.assertEqual(r["artifact_paths"], ["/tmp/p.md"])

    def test_list_messages_runs_attachments_scoped_to_conversation(self):
        self.s.upsert_location({"id": "L", "type": "workspace", "name": "n",
                                "db_path": self.db})
        c1 = self.s.create_conversation("L", "one")
        c2 = self.s.create_conversation("L", "two")
        self.s.add_message(c1["id"], "user", "a")
        self.s.add_message(c2["id"], "user", "b")
        self.assertEqual(len(self.s.list_messages(c1["id"])), 1)
        self.assertEqual(len(self.s.list_messages(c2["id"])), 1)

    def test_attachment_roundtrip(self):
        self.s.upsert_location({"id": "L", "type": "contextless", "name": "n",
                                "db_path": self.db})
        c = self.s.create_conversation("L", "x")
        a = self.s.add_attachment(c["id"], "f.txt", "/tmp/f.txt", 12, True)
        got = self.s.get_attachments([a["id"]])
        self.assertEqual(got[0]["filename"], "f.txt")
        self.assertTrue(got[0]["is_text"])


class StoreIsolationTest(unittest.TestCase):
    def test_two_stores_are_independent(self):
        d = tempfile.mkdtemp(prefix="scry-web-iso-")
        a = Store(os.path.join(d, "a.db"))
        b = Store(os.path.join(d, "b.db"))
        a.upsert_location({"id": "L", "type": "project", "name": "n", "db_path": "x"})
        ca = a.create_conversation("L", "in-a")
        # b shares no rows with a
        self.assertIsNone(b.get_conversation(ca["id"]))
        self.assertEqual(b.list_locations(), [])

    def test_raw_row_copy_preserves_ids(self):
        d = tempfile.mkdtemp(prefix="scry-web-copy-")
        a = Store(os.path.join(d, "a.db"))
        b = Store(os.path.join(d, "b.db"))
        a.upsert_location({"id": "L", "type": "contextless", "name": "n", "db_path": "x"})
        c = a.create_conversation("L", "t")
        run = a.create_run("rid", c["id"], "plan", "done", "p", {"mode": "fusion"})
        a.update_run("rid", final="F", responses=[{"label": "y"}])
        # copy the run row verbatim into b
        b.insert_conversation_row({"id": c["id"], "location_id": "L2", "title": "t",
                                   "created_at": 1.0, "updated_at": 2.0})
        b.insert_run_row(a.get_run("rid"))
        copied = b.get_run("rid")
        self.assertEqual(copied["id"], "rid")
        self.assertEqual(copied["final"], "F")
        self.assertEqual(copied["responses"][0]["label"], "y")


if __name__ == "__main__":
    unittest.main()
