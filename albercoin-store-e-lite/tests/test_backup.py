from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from elite.backup import _du, read_manifest


class BackupTests(unittest.TestCase):
    def test_du_skips_excluded_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "db").mkdir()
            (root / "db" / "a.dat").write_bytes(b"x" * 10)
            (root / "rawtx-cache").mkdir()
            (root / "rawtx-cache" / "b.dat").write_bytes(b"y" * 100)
            (root / "e-lite.log").write_bytes(b"z" * 50)
            self.assertEqual(_du(root, {"rawtx-cache", "e-lite.log"}), 10)

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


if __name__ == "__main__":
    unittest.main()
