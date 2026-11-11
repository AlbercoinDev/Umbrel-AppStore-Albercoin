from __future__ import annotations

import io
import json
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

from elite.backup import (
    BUNDLE_FORMAT,
    bundle_space_needed,
    space_margin,
    validate_bundle,
    EXCLUDE_INDEX,
    BackupManager,
    _du,
    cleanup_tmp_files,
    export_tmp_candidates,
    network_leftovers,
    read_manifest,
    _bundle_files,
    restored_estimate,
    write_sidecar,
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

    def test_sidecar_listing_avoids_scan(self) -> None:
        from elite.backup import _snapshot_files
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            for name, with_sidecar in (("oldest.tar.gz", False), ("old.tar.gz", False), ("new.tar.gz", True)):
                archive = destination / name
                manifest = {"format": 2, "network": "signet", "height": 7, "core": {"bytes": 1, "tip_height": 9}}
                payload = json.dumps(manifest).encode()
                with tarfile.open(archive, "w:gz") as tar:
                    info = tarfile.TarInfo("e-lite-snapshot.json")
                    info.size = len(payload)
                    tar.addfile(info, io.BytesIO(payload))
                if with_sidecar:
                    write_sidecar(archive, manifest)
            items = _snapshot_files(destination)
            self.assertEqual(len(items), 3)
            self.assertEqual(items[0]["name"], "new.tar.gz")
            self.assertEqual(items[0]["height"], 7)
            self.assertTrue(items[0]["manifest_read"])
            self.assertTrue(items[1]["manifest_read"])
            self.assertFalse(items[2]["manifest_read"])

    def test_sizes_cache_computes_once(self) -> None:
        from elite.config_writer import AppPaths
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            (paths.network_e_lite_data("signet") / "db.dat").write_bytes(b"x" * 5)
            manager = BackupManager(paths, supervisor=None, settings_loader=None)
            first = manager.sizes("signet")
            self.assertTrue(first["computing"])
            for _ in range(100):
                second = manager.sizes("signet")
                if second["ready"]:
                    break
                time.sleep(0.05)
            self.assertTrue(second["ready"])
            self.assertEqual(second["index"], 5)
            self.assertEqual(second["core"], 0)

    def test_debug_log_parsing_and_facts_persistence(self) -> None:
        from elite.backup import load_facts, parse_debug_log, save_facts
        from elite.config_writer import AppPaths
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "bitcoind.log"
            log.write_text(
                "Bitcoin Core version v32.1.0\n"
                "UpdateTip: new best=" + "a" * 64 + " height=100 version=0x20000000\n"
                "UpdateTip: new best=" + "b" * 64 + " height=123 version=0x20000000\n"
            )
            parsed = parse_debug_log(log)
            self.assertEqual(parsed["tip_height"], 123)
            self.assertEqual(parsed["tip_hash"], "b" * 64)
            self.assertEqual(parsed["core_version"], "32.1.0")
            paths = AppPaths.from_root(Path(directory) / "data")
            paths.ensure()
            save_facts(paths, "signet", {"tip_height": 123, "prune_height": 0})
            self.assertEqual(load_facts(paths, "signet")["tip_height"], 123)

    def test_space_helpers_are_proportional(self) -> None:
        self.assertEqual(space_margin(1024 * 1024), 256 * 1024 * 1024)
        big = 50 * 1024**3
        self.assertEqual(space_margin(big), int(big * 0.10))
        needed = bundle_space_needed({"index_raw_bytes": big, "core_bytes": big})
        self.assertEqual(needed, 2 * big + int(2 * big * 0.10))

    def test_bundle_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "i.tar.gz"
            core = root / "c.tar"
            index.write_bytes(b"1234")
            core.write_bytes(b"12")
            good = {
                "format": BUNDLE_FORMAT,
                "index_bytes": 4,
                "core_bytes": 2,
                "core_file": "c.tar",
                "index_height": 100,
                "core_tip_height": 110,
                "core_prune_height": 0,
            }
            self.assertEqual(validate_bundle(good, index, core), [])
            self.assertTrue(validate_bundle({**good, "index_bytes": 99}, index, core))
            self.assertTrue(validate_bundle({**good, "index_height": 120, "core_tip_height": 110}, index, core))
            self.assertTrue(validate_bundle({**good, "core_prune_height": 99}, index, core))

    def test_bundle_listing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            (destination / "e-lite-signet-x.index.tar.gz").write_bytes(b"i")
            (destination / "e-lite-signet-x.core.tar").write_bytes(b"c")
            (destination / "e-lite-signet-x.umbrel.json").write_text(
                json.dumps(
                    {
                        "format": BUNDLE_FORMAT,
                        "network": "signet",
                        "index_file": "e-lite-signet-x.index.tar.gz",
                        "core_file": "e-lite-signet-x.core.tar",
                        "index_bytes": 1,
                        "core_bytes": 1,
                        "index_height": 42,
                        "core_tip": 45,
                    }
                )
            )
            items = _bundle_files(destination)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["network"], "signet")
            self.assertEqual(items[0]["height"], 42)
            self.assertTrue(items[0]["has_core"])
            self.assertEqual(items[0]["kind"], "bundle")

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
