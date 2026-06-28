"""scry_web security middleware — the unauthenticated localhost server must reject
foreign Host / Origin headers (DNS-rebinding / cross-origin defense). Skips when the
optional web deps aren't installed."""
import os
import sys
import tempfile
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
class WebSecurityTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="scry-web-sec-")
        self._saved = {k: os.environ.get(k) for k in ("SCRY_WEB_HOME",
                                                       "SCRY_WEB_FAKE_ENGINE")}
        os.environ["SCRY_WEB_HOME"] = self.home
        os.environ["SCRY_WEB_FAKE_ENGINE"] = "1"
        from scry_web.server import create_app
        self.app = create_app(host="127.0.0.1", port=8765)
        self.c = TestClient(self.app, base_url=BASE)
        self.c.__enter__()

    def tearDown(self):
        self.c.__exit__(None, None, None)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_allowed_host_ok(self):
        self.assertEqual(self.c.get("/api/status").status_code, 200)

    def test_foreign_host_rejected(self):
        r = self.c.get("/api/status", headers={"host": "evil.example.com"})
        self.assertEqual(r.status_code, 403)

    def test_empty_host_rejected(self):
        # A missing/empty Host header is the canonical DNS-rebinding case — it must
        # NOT bypass the guard.
        r = self.c.get("/api/status", headers={"host": ""})
        self.assertEqual(r.status_code, 403)

    def test_empty_host_rejected_on_state_changing_endpoint(self):
        r = self.c.post("/api/conversations", json={"title": "x"},
                        headers={"host": ""})
        self.assertEqual(r.status_code, 403)

    def test_foreign_host_with_port_rejected(self):
        r = self.c.get("/api/status", headers={"host": "attacker.test:8765"})
        self.assertEqual(r.status_code, 403)

    def test_localhost_host_allowed(self):
        r = self.c.get("/api/status", headers={"host": "localhost:8765"})
        self.assertEqual(r.status_code, 200)

    def test_foreign_origin_rejected(self):
        r = self.c.get("/api/status", headers={"origin": "http://evil.example.com"})
        self.assertEqual(r.status_code, 403)

    def test_same_origin_allowed(self):
        r = self.c.get("/api/status", headers={"origin": BASE})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
