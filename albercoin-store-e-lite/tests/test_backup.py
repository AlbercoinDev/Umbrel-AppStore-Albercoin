from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from elite.backup import (
    EXCLUDE_INDEX,
    _du,
    cleanup_tmp_files,
    export_tmp_candidates,
    network_leftovers,
    read_manifest,
    restored_estimate,
)


class BackupTests(unittest.TestCase):
    def test_du_skips_excluded_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "db").mkdir()
            (root / "db" / "a.dat").write_bytes(b"x" * 10)
            (root / "rawtx-cache").mkdir()
            (root / "rawtx-cache" / "b.dat").write_bytes(b"y" * 100)
            (root / "e-lite.log").write_bytes(b"z" * 50)
            self.assertEqual(_du(root, EXCLUDE_INDEX), 10)

    def test_read_manifest_from_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "snap.tar.gz"
            payload = json.dumps({"format": 2, "network": "signet", "height": 123, "core": {"tip_height": 130}}).encode()
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo("e-lite-snapshot.json")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            manifest = read_manifest(archive)
            self.assertEqual(manifest["network"], "signet")
            self.assertEqual(manifest["core"]["tip_height"], 130)

    def test_tmp_candidates_and_cleanup(self) -> None:
        output = Path("/tmp/e-lite-x.tar.gz")
        self.assertEqual(export_tmp_candidates(output)[0], Path("/tmp/e-lite-x.tar.tmp"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snap.tar.gz"
            tmp = output.with_suffix(".tmp")
            tmp.write_bytes(b"partial")
            cleanup_tmp_files(output)
            self.assertFalse(tmp.exists())

    def test_restored_estimate(self) -> None:
        self.assertEqual(restored_estimate({"core": {"bytes": 1000}}, 1500), 2000)
        self.assertEqual(restored_estimate({}, 500), 1000)

    def test_leftovers_detection(self) -> None:
        from elite.config_writer import AppPaths
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            (paths.network_root("signet") / "e-lite.pre-import-1").mkdir()
            (paths.network_root("signet") / "core.pre-import-1").mkdir()
            leftovers = network_leftovers(paths, "signet")
            self.assertEqual(len(leftovers), 2)


if __name__ == "__main__":
    unittest.main()
