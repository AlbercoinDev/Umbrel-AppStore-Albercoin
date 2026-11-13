from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from elite.cache import CacheError, cache_clear, cache_status
from elite.config_writer import AppPaths
from elite.runtime import E_LITE_VERSION


class CacheTests(unittest.TestCase):
    def _paths(self, directory: Path) -> AppPaths:
        paths = AppPaths.from_root(directory)
        paths.ensure()
        binary = paths.runtime / f"e-lite-{E_LITE_VERSION}" / "e-lite"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(0o755)
        return paths

    def test_status_parses_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            payload = {
                "network": "signet",
                "path": str(paths.data / "networks" / "signet" / "rawtx-cache"),
                "enabled": True,
                "blocks": {"files": 2, "bytes": 2048, "limit_bytes": 64 * 1024 * 1024},
                "txs": {"files": 3, "bytes": 4096, "limit_bytes": 192 * 1024 * 1024},
                "total": {"files": 5, "bytes": 6144},
                "skipped": 0,
                "free_bytes": 10**9,
                "refused": None,
            }
            completed = subprocess.CompletedProcess(
                [], 0, stdout="[e-lite] log level: debug\n" + json.dumps(payload), stderr=""
            )
            with mock.patch("elite.cache.subprocess.run", return_value=completed) as run:
                data = cache_status(paths, "signet")
            self.assertEqual(data["total"]["files"], 5)
            command = run.call_args.args[0]
            self.assertIn("status", command)
            self.assertIn("--json", command)
            self.assertNotIn("--yes", command)
            self.assertEqual(run.call_args.kwargs["env"]["HOME"], str(paths.data))

    def test_clear_requires_yes_and_returns_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            payload = {
                "network": "signet",
                "path": "/data/rawtx-cache",
                "removed": {"files": 7, "bytes": 8192},
                "remaining": {"files": 0, "bytes": 0},
                "skipped": 0,
            }
            completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
            with mock.patch("elite.cache.subprocess.run", return_value=completed) as run:
                data = cache_clear(paths, "signet")
            self.assertEqual(data["removed"]["files"], 7)
            command = run.call_args.args[0]
            self.assertIn("clear", command)
            self.assertIn("--yes", command)

    def test_failure_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            completed = subprocess.CompletedProcess([], 1, stdout="", stderr="refusing to clear /data")
            with mock.patch("elite.cache.subprocess.run", return_value=completed):
                with self.assertRaises(CacheError) as raised:
                    cache_clear(paths, "signet")
            self.assertIn("refusing", str(raised.exception))

    def test_missing_binary_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            with self.assertRaises(CacheError):
                cache_status(paths, "signet")


if __name__ == "__main__":
    unittest.main()
