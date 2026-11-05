from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from elite.config_writer import AppPaths, build_config, toml_string, toml_value
from elite.settings import Settings


class ConfigWriterTests(unittest.TestCase):
    def _paths(self, root: Path) -> AppPaths:
        paths = AppPaths.from_root(root, root / "onion" / "hostname")
        paths.ensure()
        return paths

    def _build(self, settings: Settings, paths: AppPaths) -> tuple[str, dict]:
        text = build_config(
            settings,
            paths,
            e_lite_binary=paths.runtime / "e-lite-1" / "e-lite",
            bitcoin_binary=paths.runtime / "bitcoin-1" / "bin" / "bitcoind",
            tor_socks="tor:9050",
        )
        return text, tomllib.loads(text)

    def test_structural_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            text, data = self._build(Settings(network="signet", setup_complete=True), paths)
            self.assertEqual(data["network"]["name"], "signet")
            self.assertEqual(data["network"]["data_dir"], str(paths.network_data("signet")))
            self.assertEqual(data["server"]["listen"], "0.0.0.0:50011")
            self.assertEqual(data["server"]["tls_listen"], "0.0.0.0:50012")
            self.assertEqual(data["core"]["manage"], "managed")
            self.assertEqual(data["core"]["core_datadir"], str(paths.core_data))
            self.assertEqual(data["metrics"]["listen"], "127.0.0.1:9184")
            self.assertEqual(data["rawtx"]["tor_socks"], "tor:9050")
            self.assertEqual(data["tor"]["mode"], "off")
            self.assertFalse(data["safety"]["allow_mainnet"])
            self.assertIn("[server]", text)

    def test_mainnet_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            _, data = self._build(Settings(network="mainnet", setup_complete=True), paths)
            self.assertTrue(data["safety"]["allow_mainnet"])

    def test_defaults_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            _, data = self._build(Settings(network="signet", setup_complete=True), paths)
            self.assertNotIn("index", data)
            self.assertNotIn("pruning", data)
            self.assertNotIn("max_clients", data["server"])
            self.assertNotIn("prune_mib", data["core"])
            self.assertEqual(set(data["rawtx"]), {"tor_socks"})

    def test_overrides_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            settings = Settings(
                network="testnet4",
                setup_complete=True,
                overrides={
                    "index.profile": "nvme",
                    "index.cache_mib": 4096,
                    "server.max_clients": 64,
                    "core.prune_mib": 0,
                    "core.extra_args": ["-dbcache=256", "-maxmempool=128"],
                    "logging.max_mib": 32,
                },
            )
            _, data = self._build(settings, paths)
            self.assertEqual(data["index"]["profile"], "nvme")
            self.assertEqual(data["index"]["cache_mib"], 4096)
            self.assertEqual(data["server"]["max_clients"], 64)
            self.assertEqual(data["core"]["prune_mib"], 0)
            self.assertEqual(data["core"]["extra_args"], ["-dbcache=256", "-maxmempool=128"])
            self.assertEqual(data["logging"]["max_mib"], 32)

    def test_toml_escaping(self) -> None:
        self.assertEqual(toml_string('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(toml_value(True), "true")
        self.assertEqual(toml_value(["a", "b"]), '["a", "b"]')

    def test_every_network_parses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            for network in ("mainnet", "testnet4", "signet"):
                _, data = self._build(Settings(network=network, setup_complete=True), paths)
                self.assertEqual(data["network"]["name"], network)


if __name__ == "__main__":
    unittest.main()
