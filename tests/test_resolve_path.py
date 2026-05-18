"""
Unit tests for the _resolve_path helper used in build_modflow_model.py
and run_coupled_model.py.

Both scripts define an identical 5-line helper; we test the logic directly
here rather than importing the scripts (which pull in scipy/geopandas at
module level and are not importable in a lean test environment).
"""

import os
import unittest
from pathlib import Path


def _resolve_path(arg_val, env_var: str, default: str) -> Path:
    """Mirror of the helper defined in both coupling scripts."""
    if arg_val:
        return Path(arg_val)
    env = os.environ.get(env_var)
    if env:
        return Path(env)
    return Path(default)


class TestResolvePath(unittest.TestCase):
    """Verify the CLI-arg → env-var → default fallback chain."""

    DEFAULT = "/default/path"
    ENV_VAR = "VERDE_TEST_PATH_XYZ"

    def _clean_env(self):
        os.environ.pop(self.ENV_VAR, None)

    def test_explicit_arg_wins(self):
        self._clean_env()
        self.assertEqual(_resolve_path("/explicit/arg", self.ENV_VAR, self.DEFAULT),
                         Path("/explicit/arg"))

    def test_env_var_used_when_no_arg(self):
        os.environ[self.ENV_VAR] = "/from/env"
        try:
            self.assertEqual(_resolve_path(None, self.ENV_VAR, self.DEFAULT),
                             Path("/from/env"))
        finally:
            self._clean_env()

    def test_default_used_when_nothing_set(self):
        self._clean_env()
        self.assertEqual(_resolve_path(None, self.ENV_VAR, self.DEFAULT),
                         Path(self.DEFAULT))

    def test_arg_wins_over_env(self):
        os.environ[self.ENV_VAR] = "/from/env"
        try:
            self.assertEqual(_resolve_path("/explicit/arg", self.ENV_VAR, self.DEFAULT),
                             Path("/explicit/arg"))
        finally:
            self._clean_env()

    def test_returns_path_object(self):
        self._clean_env()
        self.assertIsInstance(_resolve_path(None, self.ENV_VAR, self.DEFAULT), Path)

    def test_empty_string_arg_falls_through_to_env(self):
        os.environ[self.ENV_VAR] = "/from/env"
        try:
            result = _resolve_path("", self.ENV_VAR, self.DEFAULT)
            self.assertEqual(result, Path("/from/env"))
        finally:
            self._clean_env()


if __name__ == "__main__":
    unittest.main()
