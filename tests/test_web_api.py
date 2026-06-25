"""scry_web API routes via FastAPI's TestClient, driven by the fake engine
(SCRY_WEB_FAKE_ENGINE) so no scry or model is ever invoked. Skips cleanly when the
optional web deps (fastapi/httpx) aren't installed, keeping the suite hermetic."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    from fastapi.testclient import TestClient
    HAVE_WEB = True
except Exception:  # noqa: BLE001
    HAVE_WEB = False

BASE = "http://127.0.0.1:8765"


@unittest.skipUnless(HAVE_WEB, "web deps (fastapi/httpx) not installed")
class WebApiTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="scry-web-api-")
        self._saved = {k: os.environ.get(k) for k in ("SCRY_WEB_HOME",
                                                       "SCRY_WEB_FAKE_ENGINE")}
        os.environ["SCRY_WEB_HOME"] = self.home
        os.environ["SCRY_WEB_FAKE_ENGINE"] = "1"
        from scry_web.server import create_app
        self.app = create_app(host="127.0.0.1", port=8765)
        # Enter the client context so the portal event loop stays alive and reliably
        # pumps the background asyncio run tasks between requests.
        self.c = TestClient(self.app, base_url=BASE)
        self.c.__enter__()

    def tearDown(self):
        self.c.__exit__(None, None, None)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _poll(self, run_id, want, tries=120):
        for _ in range(tries):
            r = self.c.get(f"/api/runs/{run_id}").json()["run"]
            if r["status"] in want:
                return r
            time.sleep(0.02)
        return r

    def _conv(self, location_id="contextless"):
        return self.c.post("/api/conversations",
                           json={"location_id": location_id, "title": "Untitled"}
                           ).json()["conversation"]

    # -- status / setup gate ---------------------------------------------- #
    def test_status_reports_fake_engine_ready(self):
        s = self.c.get("/api/status").json()
        self.assertTrue(s["fake_engine"])
        self.assertTrue(s["ready"])

    # -- one-shot scry ----------------------------------------------------- #
    def test_scry_message_runs_to_done_and_persists(self):
        conv = self._conv()
        run = self.c.post(f"/api/conversations/{conv['id']}/messages",
                          json={"capability": "scry", "content": "what is 2+2"}
                          ).json()["run"]
        self.assertEqual(run["status"], "running")
        done = self._poll(run["id"], ("done", "error"))
        self.assertEqual(done["status"], "done")
        self.assertIn("fake-engine", done["final"])
        self.assertTrue(done["artifact_paths"])
        # the conversation now holds user + assistant messages, and the run persisted
        full = self.c.get(f"/api/conversations/{conv['id']}").json()
        roles = [m["role"] for m in full["messages"]]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertEqual(full["runs"][0]["status"], "done")

    def test_unknown_capability_rejected(self):
        conv = self._conv()
        r = self.c.post(f"/api/conversations/{conv['id']}/messages",
                        json={"capability": "bogus", "content": "x"})
        self.assertEqual(r.status_code, 400)

    # -- interactive plan loop -------------------------------------------- #
    def test_plan_questions_answer_finalize(self):
        conv = self._conv()
        run = self.c.post(f"/api/conversations/{conv['id']}/messages",
                          json={"capability": "plan", "content": "build a web app"}
                          ).json()["run"]
        q = self._poll(run["id"], ("questions", "ready", "done", "error"))
        self.assertEqual(q["status"], "questions")
        self.assertTrue(q["questions"])
        eid = q["engine_run_id"]
        # answer -> ready
        self.c.post(f"/api/runs/{run['id']}/answers",
                    json={"answers": [{"q": q["questions"][0]["q"], "a": "macos"}]})
        ready = self._poll(run["id"], ("ready", "done", "error"))
        self.assertIn(ready["status"], ("ready", "done"))
        # finalize -> done; one engine_run_id reused across the whole interview
        self.c.post(f"/api/runs/{run['id']}/answers", json={"done": True})
        done = self._poll(run["id"], ("done", "error"))
        self.assertEqual(done["status"], "done")
        self.assertTrue(done["final"])
        self.assertEqual(done["engine_run_id"], eid)

    # -- attachments ------------------------------------------------------- #
    def test_attachment_upload_copies_into_storage(self):
        conv = self._conv()
        r = self.c.post(f"/api/conversations/{conv['id']}/attachments",
                        files={"file": ("notes.txt", b"hello body", "text/plain")})
        att = r.json()["attachment"]
        self.assertEqual(att["filename"], "notes.txt")
        self.assertTrue(att["is_text"])
        self.assertTrue(os.path.exists(att["path"]))

    # -- locations --------------------------------------------------------- #
    def test_create_workspace_and_open_project(self):
        ws = self.c.post("/api/locations", json={"name": "Research WS"}).json()["location"]
        self.assertEqual(ws["type"], "workspace")
        d = tempfile.mkdtemp(prefix="scry-open-")
        pr = self.c.post("/api/locations/open", json={"path": d}).json()["location"]
        self.assertEqual(pr["type"], "project")
        locs = self.c.get("/api/locations").json()["locations"]
        types = sorted({l["type"] for l in locs})
        self.assertEqual(types, ["contextless", "project", "workspace"])

    def test_open_project_bad_path_400(self):
        r = self.c.post("/api/locations/open", json={"path": "/no/such/dir/zzz"})
        self.assertEqual(r.status_code, 400)

    # -- conversation listing / message_count ----------------------------- #
    def test_conversation_listing_reports_message_count(self):
        # The web UI reuses an empty conversation (message_count == 0) instead of
        # minting a fresh blank one on every load, so the listing API must expose it.
        empty = self._conv()
        used = self._conv()
        self.c.post(f"/api/conversations/{used['id']}/messages",
                    json={"capability": "scry", "content": "hello there"})
        convs = self.c.get("/api/locations/contextless/conversations").json()["conversations"]
        by_id = {c["id"]: c for c in convs}
        self.assertEqual(by_id[empty["id"]]["message_count"], 0)
        # the user message is recorded immediately on send, so the count is >= 1
        self.assertGreaterEqual(by_id[used["id"]]["message_count"], 1)

    # -- upgrade ----------------------------------------------------------- #
    def test_upgrade_contextless_conversation_to_project(self):
        conv = self._conv()
        run = self.c.post(f"/api/conversations/{conv['id']}/messages",
                          json={"capability": "scry", "content": "build a parser"}
                          ).json()["run"]
        self._poll(run["id"], ("done", "error"))
        up = self.c.post(f"/api/conversations/{conv['id']}/upgrade",
                         json={"name": "Parser"}).json()
        self.assertEqual(up["location"]["type"], "project")
        # the conversation is now reachable in the new project location
        full = self.c.get(f"/api/conversations/{conv['id']}").json()
        self.assertEqual(full["location"]["type"], "project")

    # -- download + export ------------------------------------------------- #
    def test_download_artifact_and_export(self):
        conv = self._conv()
        run = self.c.post(f"/api/conversations/{conv['id']}/messages",
                          json={"capability": "research", "content": "study X"}
                          ).json()["run"]
        done = self._poll(run["id"], ("done", "error"))
        self.assertTrue(done["artifact_paths"])
        dl = self.c.get(f"/api/runs/{run['id']}/download?index=0")
        self.assertEqual(dl.status_code, 200)
        self.assertIn("study X", dl.text)
        ex = self.c.get(f"/api/conversations/{conv['id']}/export").json()
        self.assertIn("study X", ex["markdown"])

    def test_run_not_found_404(self):
        self.assertEqual(self.c.get("/api/runs/nope").status_code, 404)

    # -- reveal (by run artifact, never an arbitrary path) ----------------- #
    def _research_run(self):
        conv = self._conv()
        run = self.c.post(f"/api/conversations/{conv['id']}/messages",
                          json={"capability": "research", "content": "study X"}
                          ).json()["run"]
        done = self._poll(run["id"], ("done", "error"))
        self.assertTrue(done["artifact_paths"])
        return done

    def test_reveal_unknown_run_returns_404(self):
        self.assertEqual(self.c.post("/api/runs/nope/reveal").status_code, 404)

    def test_reveal_out_of_range_index_returns_404(self):
        done = self._research_run()
        r = self.c.post(f"/api/runs/{done['id']}/reveal?index=99")
        self.assertEqual(r.status_code, 404)

    def test_reveal_opens_only_the_runs_own_artifact(self):
        # The path is taken from the run record, never from the client — so reveal
        # can't be steered to an arbitrary file. Patch the macOS `open` so the test
        # never pops Finder; assert it targets exactly the recorded artifact.
        from unittest import mock
        done = self._research_run()
        art = done["artifact_paths"][0]
        with mock.patch("scry_web.api.subprocess.run") as m:
            r = self.c.post(f"/api/runs/{done['id']}/reveal?index=0")
        self.assertEqual(r.status_code, 200)
        if m.called:  # darwin only; elsewhere the endpoint no-ops with ok:False
            self.assertIn(art, m.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
