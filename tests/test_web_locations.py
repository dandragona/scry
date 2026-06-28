"""scry_web.locations — contextless default, workspace scaffolding (dir + git +
config + history DB), opening arbitrary projects, and promoting a contextless
session into a CLI-openable scry project. Stdlib only.

Every test isolates SCRY_WEB_HOME to a throwaway dir so nothing touches the real
~/.config/scry."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class LocationsTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="scry-web-home-")
        self._old = os.environ.get("SCRY_WEB_HOME")
        os.environ["SCRY_WEB_HOME"] = self.home
        from scry_web.locations import LocationManager
        self.LM = LocationManager

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SCRY_WEB_HOME", None)
        else:
            os.environ["SCRY_WEB_HOME"] = self._old

    def test_contextless_exists_by_default(self):
        lm = self.LM()
        loc = lm.get("contextless")
        self.assertIsNotNone(loc)
        self.assertEqual(loc["type"], "contextless")

    def test_create_workspace_scaffolds_dir_git_and_db(self):
        lm = self.LM()
        ws = lm.create_workspace("My Research")
        root = Path(ws["root_path"])
        self.assertTrue(root.is_dir())
        self.assertEqual(ws["type"], "workspace")
        self.assertTrue(Path(ws["db_path"]).exists())
        self.assertTrue((root / ".scry" / "web" / "history.db").exists())
        # workspace is listed in the registry
        types = [l["type"] for l in lm.list()]
        self.assertIn("workspace", types)

    def test_scaffold_gitignores_dot_scry(self):
        lm = self.LM()
        ws = lm.create_workspace("Private WS")
        gi = Path(ws["root_path"]) / ".gitignore"
        self.assertTrue(gi.exists())
        entries = gi.read_text().splitlines()
        self.assertIn(".scry/", entries)
        # no duplicate entry
        self.assertEqual(entries.count(".scry/"), 1)

    def test_scaffold_appends_to_existing_gitignore(self):
        # If the scaffold target already ships a .gitignore, _scaffold must append
        # .scry/ without clobbering the existing contents or duplicating the entry.
        lm = self.LM()
        d = Path(tempfile.mkdtemp(prefix="scry-existing-gi-"))
        (d / ".gitignore").write_text("node_modules/\n*.pyc\n")
        lm._scaffold(d, "Has Gitignore", "workspace")
        entries = (d / ".gitignore").read_text().splitlines()
        self.assertIn("node_modules/", entries)
        self.assertIn("*.pyc", entries)
        self.assertIn(".scry/", entries)
        self.assertEqual(entries.count(".scry/"), 1)

    def test_scaffold_no_duplicate_when_already_ignored(self):
        lm = self.LM()
        d = Path(tempfile.mkdtemp(prefix="scry-already-ig-"))
        (d / ".gitignore").write_text(".scry/\n")
        lm._scaffold(d, "Already", "workspace")
        entries = (d / ".gitignore").read_text().splitlines()
        self.assertEqual(entries.count(".scry/"), 1)

    def test_create_workspace_unique_slugs(self):
        lm = self.LM()
        a = lm.create_workspace("dup")
        b = lm.create_workspace("dup")
        self.assertNotEqual(a["root_path"], b["root_path"])

    def test_open_project_accepts_any_directory(self):
        lm = self.LM()
        d = tempfile.mkdtemp(prefix="scry-proj-")
        loc = lm.open_project(d)
        self.assertEqual(loc["type"], "project")
        self.assertEqual(loc["root_path"], str(Path(d).resolve()))
        self.assertTrue((Path(d) / ".scry" / "web" / "history.db").exists())

    def test_open_project_rejects_nonexistent(self):
        from scry_web.locations import LocationError
        lm = self.LM()
        with self.assertRaises(LocationError):
            lm.open_project("/no/such/dir/12345")

    def test_open_project_idempotent_by_root(self):
        lm = self.LM()
        d = tempfile.mkdtemp(prefix="scry-proj2-")
        a = lm.open_project(d)
        b = lm.open_project(d)
        self.assertEqual(a["id"], b["id"])

    def test_upgrade_contextless_to_project_migrates_history(self):
        lm = self.LM()
        store = lm.store_for(lm.get("contextless"))
        conv = store.create_conversation("contextless", "build a rate limiter")
        store.add_message(conv["id"], "user", "build a rate limiter", capability="scry")
        # a finished run with an artifact on disk
        store.create_run("r9", conv["id"], "scry", "done", "build", {})
        from scry_web import artifacts
        ap = artifacts.write_chat(lm.get("contextless"), conv["id"], "r9",
                                  "build", "the answer")
        store.update_run("r9", artifact_paths=[ap], final="the answer")

        result = lm.upgrade_contextless_to_project(conv["id"], "Rate Limiter")
        new_loc = result["location"]
        self.assertEqual(new_loc["type"], "project")
        root = Path(new_loc["root_path"])
        # scaffold artifacts: git + README + history DB
        self.assertTrue((root / "README.md").exists())
        self.assertTrue(Path(new_loc["db_path"]).exists())
        # migrated conversation + message + run land in the project DB
        dst = lm.store_for(new_loc)
        self.assertIsNotNone(dst.get_conversation(conv["id"]))
        self.assertEqual(len(dst.list_messages(conv["id"])), 1)
        copied_run = dst.get_run("r9")
        self.assertIsNotNone(copied_run)
        # artifact copied into the project root (in place going forward)
        self.assertTrue(copied_run["artifact_paths"])
        self.assertTrue(Path(copied_run["artifact_paths"][0]).exists())
        self.assertEqual(Path(copied_run["artifact_paths"][0]).parent, root)

    def test_upgrade_rejects_non_contextless_conversation(self):
        from scry_web.locations import LocationError
        lm = self.LM()
        ws = lm.create_workspace("ws")
        store = lm.store_for(ws)
        conv = store.create_conversation(ws["id"], "x")
        with self.assertRaises(LocationError):
            lm.upgrade_contextless_to_project(conv["id"], "nope")


if __name__ == "__main__":
    unittest.main()
