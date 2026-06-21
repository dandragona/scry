"""Unit tests for scry-deepseek's API-key / .env resolution.

Hermetic: every test passes a FAKE script_path under a temp dir and points HOME /
SCRY_ENV_FILE at temp paths via env_vars(), so the repo's real .env is never read
and os.environ is always restored. No network, no real DeepSeek call.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402

ds = h.load_scry_deepseek()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class TestEnvResolution(unittest.TestCase):
    def setUp(self):
        # realpath'd temp so abspath == realpath (macOS /var -> /private/var).
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ds-env-"))
        self.script = os.path.join(self.tmp, "bin", "scry-deepseek")
        os.makedirs(os.path.dirname(self.script), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_real_env_var_wins_over_files(self):
        _write(os.path.join(self.tmp, "bin", ".env"), "DEEPSEEK_API_KEY=from-file\n")
        with h.env_vars(DEEPSEEK_API_KEY="from-env", SCRY_ENV_FILE=None, HOME=self.tmp):
            ds._load_env_file(self.script)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "from-env")

    def test_scry_env_file_override(self):
        envf = os.path.join(self.tmp, "custom.env")
        _write(envf, "DEEPSEEK_API_KEY=from-override\n")
        with h.env_vars(DEEPSEEK_API_KEY=None, SCRY_ENV_FILE=envf, HOME=self.tmp):
            ds._load_env_file(self.script)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "from-override")

    def test_config_dir_env(self):
        _write(os.path.join(self.tmp, ".config", "scry", ".env"),
               "DEEPSEEK_API_KEY=from-config\n")
        with h.env_vars(DEEPSEEK_API_KEY=None, SCRY_ENV_FILE=None, HOME=self.tmp):
            ds._load_env_file(self.script)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "from-config")

    def test_script_dir_env_backward_compat(self):
        _write(os.path.join(self.tmp, "bin", ".env"), "DEEPSEEK_API_KEY=from-scriptdir\n")
        with h.env_vars(DEEPSEEK_API_KEY=None, SCRY_ENV_FILE=None, HOME=self.tmp):
            ds._load_env_file(self.script)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "from-scriptdir")

    def test_precedence_scriptdir_beats_config(self):
        _write(os.path.join(self.tmp, "bin", ".env"), "DEEPSEEK_API_KEY=scriptdir\n")
        _write(os.path.join(self.tmp, ".config", "scry", ".env"), "DEEPSEEK_API_KEY=config\n")
        with h.env_vars(DEEPSEEK_API_KEY=None, SCRY_ENV_FILE=None, HOME=self.tmp):
            ds._load_env_file(self.script)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "scriptdir")

    def test_candidates_skip_realpath_when_equal(self):
        with h.env_vars(SCRY_ENV_FILE=None, HOME=self.tmp):
            cands = ds._env_file_candidates(self.script)
        bindir = os.path.join(self.tmp, "bin", ".env")
        self.assertEqual(cands.count(bindir), 1)
        self.assertEqual(cands[-1], os.path.join(self.tmp, ".config", "scry", ".env"))

    def test_nothing_set_leaves_key_unset(self):
        with h.env_vars(DEEPSEEK_API_KEY=None, SCRY_ENV_FILE=None, HOME=self.tmp):
            ds._load_env_file(self.script)
            self.assertIsNone(os.environ.get("DEEPSEEK_API_KEY"))


if __name__ == "__main__":
    unittest.main()
