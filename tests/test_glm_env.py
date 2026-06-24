"""Unit tests for scry-glm's API-key / .env resolution.

Hermetic: every test passes a FAKE script_path under a temp dir and points HOME /
SCRY_ENV_FILE at temp paths via env_vars(), so the repo's real .env is never read
and os.environ is always restored. No network, no real GLM call.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402

glm = h.load_scry_glm()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class TestEnvResolution(unittest.TestCase):
    def setUp(self):
        # realpath'd temp so abspath == realpath (macOS /var -> /private/var).
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="glm-env-"))
        self.script = os.path.join(self.tmp, "bin", "scry-glm")
        os.makedirs(os.path.dirname(self.script), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_real_env_var_wins_over_files(self):
        _write(os.path.join(self.tmp, "bin", ".env"), "GLM_API_KEY=from-file\n")
        with h.env_vars(GLM_API_KEY="from-env", SCRY_ENV_FILE=None, HOME=self.tmp):
            glm._load_env_file(self.script)
            self.assertEqual(os.environ["GLM_API_KEY"], "from-env")

    def test_scry_env_file_override(self):
        envf = os.path.join(self.tmp, "custom.env")
        _write(envf, "GLM_API_KEY=from-override\n")
        with h.env_vars(GLM_API_KEY=None, SCRY_ENV_FILE=envf, HOME=self.tmp):
            glm._load_env_file(self.script)
            self.assertEqual(os.environ["GLM_API_KEY"], "from-override")

    def test_config_dir_env(self):
        _write(os.path.join(self.tmp, ".config", "scry", ".env"),
               "GLM_API_KEY=from-config\n")
        with h.env_vars(GLM_API_KEY=None, SCRY_ENV_FILE=None, HOME=self.tmp):
            glm._load_env_file(self.script)
            self.assertEqual(os.environ["GLM_API_KEY"], "from-config")

    def test_script_dir_env_backward_compat(self):
        _write(os.path.join(self.tmp, "bin", ".env"), "GLM_API_KEY=from-scriptdir\n")
        with h.env_vars(GLM_API_KEY=None, SCRY_ENV_FILE=None, HOME=self.tmp):
            glm._load_env_file(self.script)
            self.assertEqual(os.environ["GLM_API_KEY"], "from-scriptdir")

    def test_precedence_scriptdir_beats_config(self):
        _write(os.path.join(self.tmp, "bin", ".env"), "GLM_API_KEY=scriptdir\n")
        _write(os.path.join(self.tmp, ".config", "scry", ".env"), "GLM_API_KEY=config\n")
        with h.env_vars(GLM_API_KEY=None, SCRY_ENV_FILE=None, HOME=self.tmp):
            glm._load_env_file(self.script)
            self.assertEqual(os.environ["GLM_API_KEY"], "scriptdir")

    def test_candidates_skip_realpath_when_equal(self):
        with h.env_vars(SCRY_ENV_FILE=None, HOME=self.tmp):
            cands = glm._env_file_candidates(self.script)
        bindir = os.path.join(self.tmp, "bin", ".env")
        self.assertEqual(cands.count(bindir), 1)
        self.assertEqual(cands[-1], os.path.join(self.tmp, ".config", "scry", ".env"))

    def test_nothing_set_leaves_key_unset(self):
        with h.env_vars(GLM_API_KEY=None, SCRY_ENV_FILE=None, HOME=self.tmp):
            glm._load_env_file(self.script)
            self.assertIsNone(os.environ.get("GLM_API_KEY"))


class TestMissingKeyMessage(unittest.TestCase):
    def test_message_names_locations_and_zshenv(self):
        msg = glm.MISSING_KEY_MSG
        self.assertIn("~/.config/scry/.env", msg)
        self.assertIn("SCRY_ENV_FILE", msg)
        self.assertIn("~/.zshenv", msg)
        self.assertIn("GLM_API_KEY", msg)

    def test_subprocess_missing_key_exits_2(self):
        # Run a COPY of the adapter (so its script-dir has no .env) with a scrubbed
        # env and empty HOME -> no key anywhere -> exit 2 + the guidance message.
        copydir = os.path.realpath(tempfile.mkdtemp(prefix="glm-copy-"))
        try:
            dest = os.path.join(copydir, "scry-glm")
            shutil.copy2(h.REPO_ROOT / "scry-glm", dest)
            os.chmod(dest, 0o700)
            empty_home = os.path.join(copydir, "home")
            os.makedirs(empty_home)
            env = os.environ.copy()
            env.pop("GLM_API_KEY", None)
            env.pop("SCRY_ENV_FILE", None)
            env["HOME"] = empty_home
            r = subprocess.run([sys.executable, dest, "--model", "glm-5.2"],
                               input="", env=env, capture_output=True, text=True,
                               timeout=30)
            self.assertEqual(r.returncode, 2)
            self.assertIn("~/.config/scry/.env", r.stderr)
            self.assertIn("~/.zshenv", r.stderr)
        finally:
            shutil.rmtree(copydir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
