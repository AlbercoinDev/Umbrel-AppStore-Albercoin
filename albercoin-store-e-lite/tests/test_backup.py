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
    parse_export_output,
    restored_estimate,
    write_sidecar,
    core_chain_paths,
    core_complete,
    core_packed_bytes,
    core_tar_members,
    import_marker,
    import_staging,
    index_complete,
    valid_digest,
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

    def test_phase_progress_is_independent(self) -> None:
        from elite.config_writer import AppPaths
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            manager = BackupManager(paths, supervisor=None, settings_loader=None)
            manager._update(phase="exporting-index", phase_started_at=time.time(), started_at=time.time())
            manager._set_progress(900, 1200, 200, 400)
            status = manager.status()
            self.assertEqual(status["phase_done"], 200)
            self.assertEqual(status["phase_total"], 400)
            self.assertEqual(status["phase_percent"], 50.0)
            self.assertEqual(status["done"], 900)
            self.assertEqual(status["total"], 1200)

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

    def test_parse_export_output(self) -> None:
        parsed = parse_export_output(
            ["snapshot: /x (1.00 MiB)", "network: signet", "tip: 184250 " + "a" * 64, "txnum: 77", "logical_snapshot_hash: " + "b" * 64]
        )
        self.assertEqual(parsed["network"], "signet")
        self.assertEqual(parsed["tip_height"], 184250)
        self.assertEqual(parsed["index_hash"], "a" * 64)
        self.assertEqual(parsed["digest"], "b" * 64)

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

    def test_core_chain_paths_follow_network_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            core = Path(directory) / "core"
            (core / "blocks").mkdir(parents=True)
            (core / "chainstate").mkdir()
            (core / "blocks" / "blk00000.dat").write_bytes(b"x" * 30)
            (core / "chainstate" / "000001.ldb").write_bytes(b"y" * 20)
            (core / "wallets").mkdir()
            (core / "wallets" / "wallet.dat").write_bytes(b"secret")
            (core / "settings.json").write_text("{}")
            self.assertEqual([p.name for p in core_chain_paths(core, "mainnet")], ["blocks", "chainstate"])
            self.assertEqual(core_tar_members(core, "mainnet"), ["./blocks", "./chainstate"])
            self.assertEqual(core_packed_bytes(core, "mainnet"), 50)
            self.assertTrue(core_complete(core, "mainnet"))

            signet = Path(directory) / "signet"
            (signet / "signet" / "blocks").mkdir(parents=True)
            (signet / "signet" / "chainstate").mkdir()
            (signet / "signet" / "blocks" / "blk00000.dat").write_bytes(b"z" * 7)
            (signet / "blocks").mkdir()
            self.assertEqual(core_tar_members(signet, "signet"), ["./signet/blocks", "./signet/chainstate"])
            self.assertEqual(core_packed_bytes(signet, "signet"), 7)
            self.assertTrue(core_complete(signet, "signet"))
            self.assertEqual(core_tar_members(signet, "testnet4"), [])

    def test_valid_digest(self) -> None:
        digest = "a" * 64
        self.assertEqual(valid_digest(digest.upper()), digest)
        self.assertEqual(valid_digest("  " + digest + "\n"), digest)
        self.assertEqual(valid_digest("abc"), "")
        self.assertEqual(valid_digest(""), "")

    def test_import_progress_uses_staging_and_detects_stall(self) -> None:
        from elite.config_writer import AppPaths
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            manager = BackupManager(paths, supervisor=None, settings_loader=None)
            target = paths.network_e_lite_data("signet")
            staging = import_staging(paths, "signet")
            staging.mkdir()
            (staging / "000001.sst").write_bytes(b"x" * (2 * 1024 * 1024))
            manager._phase("extracting-index", "Restaurando el índice…")
            manager._progress_import_index(target, 0, 4 * 1024 * 1024, "signet")
            status = manager.status()
            self.assertEqual(status["phase"], "extracting-index")
            self.assertEqual(status["phase_done"], 2 * 1024 * 1024)
            self.assertEqual(status["phase_total"], 4 * 1024 * 1024)
            self.assertEqual(status["phase_percent"], 50.0)
            # No growth for PROGRESS_STALL_SECONDS means extraction finished and
            # e-lite is recomputing the digest.
            manager._last_bytes_at = time.monotonic() - 60
            manager._progress_import_index(target, 0, 4 * 1024 * 1024, "signet")
            self.assertEqual(manager.status()["phase"], "verifying-index")
            self.assertEqual(manager.status()["phase_total"], 0)

    def test_bundle_import_passes_expected_digest(self) -> None:
        from elite.config_writer import AppPaths
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            manager = BackupManager(paths, supervisor=None, settings_loader=None)
            index_file = paths.exports_dir / "b.index.tar.gz"
            index_file.write_bytes(b"index")
            digest = "c" * 64
            bundle = {
                "format": BUNDLE_FORMAT,
                "network": "signet",
                "index_file": index_file.name,
                "core_file": "",
                "index_bytes": len(b"index"),
                "index_raw_bytes": 100,
                "core_bytes": 0,
                "index_height": 42,
                "index_digest": digest,
            }
            bundle_file = paths.exports_dir / "b.umbrel.json"
            bundle_file.write_text(json.dumps(bundle), encoding="utf-8")
            commands: list[list[str]] = []
            manager._run_process = lambda command, progress, timeout: commands.append(command) or []
            manager._stop = lambda network: None
            manager._restart = lambda network, enabled: None
            manager._import_bundle({"network": "signet", "delete_previous": False}, False, bundle_file)
            index_command = [c for c in commands if "import-snapshot" in c][0]
            self.assertIn("--expect-digest", index_command)
            self.assertEqual(index_command[index_command.index("--expect-digest") + 1], digest)
            self.assertEqual(manager.status()["phase"], "done")

    def test_export_end_to_end_keeps_chain_data_only(self) -> None:
        import tarfile

        from elite.config_writer import AppPaths
        from elite.backup import _elite_version

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            manager = BackupManager(paths, supervisor=None, settings_loader=None)
            manager._stop = lambda network: None
            manager._restart = lambda network, enabled: None
            manager._facts = lambda network: {"tip_height": 10, "prune_height": 0, "tip_hash": "f" * 64}
            index_dir = paths.network_e_lite_data("signet")
            (index_dir / "CURRENT").write_bytes(b"0123456789")
            core_dir = paths.network_core_data("signet")
            blocks = core_dir / "signet" / "blocks"
            blocks.mkdir(parents=True)
            (blocks / "blk00000.dat").write_bytes(b"chaindata")
            (core_dir / "wallets").mkdir()
            (core_dir / "wallets" / "wallet.dat").write_bytes(b"secret")
            (core_dir / "settings.json").write_text("{}")
            paths.network_config_file("signet").parent.mkdir(parents=True, exist_ok=True)
            paths.network_config_file("signet").write_text("[network]\n", encoding="utf-8")
            binary = paths.runtime / f"e-lite-{_elite_version()}" / "e-lite"
            binary.parent.mkdir(parents=True)
            digest = "b" * 64
            binary.write_text(
                "#!/bin/sh\n"
                'prev=""; out=""\n'
                'for a in "$@"; do if [ "$prev" = "--output" ]; then out="$a"; fi; prev="$a"; done\n'
                'printf index > "$out"\n'
                'echo "network: signet"\n'
                'echo "tip: 7 ' + "c" * 64 + '"\n'
                'echo "txnum: 1"\n'
                'echo "logical_snapshot_hash: ' + digest + '"\n',
                encoding="utf-8",
            )
            binary.chmod(0o755)
            destination = Path(directory) / "backups"
            destination.mkdir()
            manager._run_export({"network": "signet", "destination": destination, "compress": "3"}, False)
            bundles = list(destination.glob("*.umbrel.json"))
            self.assertEqual(len(bundles), 1)
            bundle = json.loads(bundles[0].read_text(encoding="utf-8"))
            self.assertEqual(bundle["index_digest"], digest)
            self.assertEqual(bundle["index_height"], 7)
            core_file = destination / bundle["core_file"]
            with tarfile.open(core_file, "r") as tar:
                names = tar.getnames()
            cleaned = [name.removeprefix("./") for name in names]
            self.assertTrue(any(name.startswith("signet/blocks") for name in cleaned), cleaned)
            self.assertFalse(any("wallet" in name or "settings.json" in name for name in names), names)
            self.assertEqual(manager.status()["phase"], "done")
            self.assertEqual(manager.status()["digest"], digest)

    def test_bundle_import_end_to_end_with_staged_e_lite(self) -> None:
        import os
        import tarfile
        from unittest import mock

        from elite.config_writer import AppPaths
        from elite.backup import _elite_version

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            manager = BackupManager(paths, supervisor=None, settings_loader=None)
            manager._stop = lambda network: None
            manager._restart = lambda network, enabled: None
            # A fake e-lite 29.11: extracts into .<name>.import-partial and
            # renames only at the end.
            binary = paths.runtime / f"e-lite-{_elite_version()}" / "e-lite"
            binary.parent.mkdir(parents=True)
            binary.write_text(
                "#!/bin/sh\n"
                'root="$E_LITE_TEST_ROOT"\n'
                'target="$root/networks/signet/e-lite"\n'
                'staging="$(dirname "$target")/.$(basename "$target").import-partial"\n'
                'rm -rf "$staging"; mkdir -p "$staging"\n'
                'echo ok > "$staging/CURRENT"\n'
                'sleep 0.2\n'
                'mv "$staging" "$target"\n',
                encoding="utf-8",
            )
            binary.chmod(0o755)
            core_file = paths.exports_dir / "x.core.tar"
            source = Path(directory) / "core-src" / "signet" / "blocks"
            source.mkdir(parents=True)
            (source / "blk00000.dat").write_bytes(b"chain")
            with tarfile.open(core_file, "w") as tar:
                tar.add(source, arcname="signet/blocks")
            index_file = paths.exports_dir / "x.index.tar.gz"
            index_file.write_bytes(b"index")
            bundle = {
                "format": BUNDLE_FORMAT,
                "network": "signet",
                "index_file": index_file.name,
                "core_file": core_file.name,
                "index_bytes": len(b"index"),
                "index_raw_bytes": 1024,
                "core_bytes": core_file.stat().st_size,
                "index_height": 7,
                "index_digest": "a" * 64,
            }
            bundle_file = paths.exports_dir / "x.umbrel.json"
            bundle_file.write_text(json.dumps(bundle), encoding="utf-8")
            with mock.patch.dict(os.environ, {"E_LITE_TEST_ROOT": str(paths.data)}):
                manager._import_bundle({"network": "signet", "delete_previous": True}, False, bundle_file)
            self.assertEqual(manager.status()["phase"], "done")
            self.assertTrue((paths.network_e_lite_data("signet") / "CURRENT").is_file())
            self.assertTrue((paths.network_core_data("signet") / "signet" / "blocks" / "blk00000.dat").is_file())
            self.assertFalse(import_staging(paths, "signet").exists())
            self.assertFalse(import_marker(paths, "signet").exists())

    def test_failed_bundle_import_rolls_back_and_cleans_staging(self) -> None:
        import os
        from unittest import mock

        from elite.config_writer import AppPaths
        from elite.backup import _elite_version

        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            manager = BackupManager(paths, supervisor=None, settings_loader=None)
            manager._stop = lambda network: None
            manager._restart = lambda network, enabled: None
            target = paths.network_e_lite_data("signet")
            (target / "CURRENT").write_text("old")
            binary = paths.runtime / f"e-lite-{_elite_version()}" / "e-lite"
            binary.parent.mkdir(parents=True)
            binary.write_text(
                "#!/bin/sh\n"
                'root="$E_LITE_TEST_ROOT"\n'
                'target="$root/networks/signet/e-lite"\n'
                'staging="$(dirname "$target")/.$(basename "$target").import-partial"\n'
                'mkdir -p "$staging"; echo partial > "$staging/CURRENT"\n'
                'echo boom >&2; exit 1\n',
                encoding="utf-8",
            )
            binary.chmod(0o755)
            index_file = paths.exports_dir / "y.index.tar.gz"
            index_file.write_bytes(b"index")
            bundle = {
                "format": BUNDLE_FORMAT,
                "network": "signet",
                "index_file": index_file.name,
                "core_file": "",
                "index_bytes": len(b"index"),
                "index_raw_bytes": 1024,
                "core_bytes": 0,
                "index_height": 7,
            }
            bundle_file = paths.exports_dir / "y.umbrel.json"
            bundle_file.write_text(json.dumps(bundle), encoding="utf-8")
            with mock.patch.dict(os.environ, {"E_LITE_TEST_ROOT": str(paths.data)}):
                with self.assertRaises(RuntimeError):
                    manager._import_bundle({"network": "signet", "delete_previous": False}, False, bundle_file)
            self.assertEqual((target / "CURRENT").read_text(), "old")
            self.assertFalse(import_staging(paths, "signet").exists())
            self.assertFalse(import_marker(paths, "signet").exists())

    def test_recovery_restores_previous_after_interrupted_import(self) -> None:
        from elite.config_writer import AppPaths
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            manager = BackupManager(paths, supervisor=None, settings_loader=None)
            target = paths.network_e_lite_data("signet")
            core_target = paths.network_core_data("signet")
            previous_index = target.with_name(f"{target.name}.pre-import-1")
            previous_core = core_target.with_name(f"{core_target.name}.pre-import-1")
            previous_index.mkdir()
            (previous_index / "CURRENT").write_text("ok")
            (previous_core / "signet" / "blocks").mkdir(parents=True)
            if target.exists():
                target.rmdir()
            if core_target.exists():
                core_target.rmdir()
            import_staging(paths, "signet").mkdir()
            import_marker(paths, "signet").write_text("{}", encoding="utf-8")
            self.assertEqual(manager.recover_interrupted(), 2)
            self.assertTrue(index_complete(target))
            self.assertTrue(core_complete(core_target, "signet"))
            self.assertFalse(import_staging(paths, "signet").exists())
            self.assertFalse(import_marker(paths, "signet").exists())
            notice = manager.recovery_notice()
            self.assertIsNotNone(notice)
            self.assertEqual(notice["network"], "signet")
            self.assertTrue(notice["recovered"])

    def test_recovery_keeps_a_complete_target(self) -> None:
        from elite.config_writer import AppPaths
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            manager = BackupManager(paths, supervisor=None, settings_loader=None)
            target = paths.network_e_lite_data("signet")
            (target / "CURRENT").write_text("new")
            previous = target.with_name(f"{target.name}.pre-import-1")
            previous.mkdir()
            (previous / "CURRENT").write_text("old")
            import_marker(paths, "signet").write_text(json.dumps({"has_core": False}), encoding="utf-8")
            manager.recover_interrupted()
            self.assertEqual((target / "CURRENT").read_text(), "new")
            self.assertTrue(previous.exists())
            self.assertFalse(import_marker(paths, "signet").exists())

    def test_cleanup_previous_refuses_while_active(self) -> None:
        from elite.config_writer import AppPaths
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            manager = BackupManager(paths, supervisor=None, settings_loader=None)
            leftover = paths.network_root("signet") / "e-lite.pre-import-1"
            leftover.mkdir()
            manager._state["active"] = True
            self.assertEqual(manager.cleanup_previous("signet"), 0)
            self.assertTrue(leftover.exists())

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
