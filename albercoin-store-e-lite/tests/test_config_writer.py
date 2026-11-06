from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from elite.config_writer import (
    AppPaths,
    build_config,
    network_metrics_port,
    network_ports,
    toml_string,
    toml_value,
)
from elite.settings import NetworkConfig


class ConfigWriterTests(unittest.TestCase):
    def _paths(self, root: Path) -> AppPaths:
        paths = AppPaths.from_root(root)
        paths.ensure()
        return paths

    def _build(self, network: str, config: NetworkConfig, paths: AppPaths) -> tuple[str, dict]:
        text = build_config(
            network,
            config,
            paths,
            bitcoin_binary=paths.runtime / "bitcoin-1" / "bin" / "bitcoind",
            tor_binary=paths.runtime / "tor-1" / "tor",
        )
        return text, tomllib.loads(text)

    def test_ports_and_structural_values(self) -> None:
        expected = {
            "mainnet": (55011, 55022, 9184),
            "testnet4": (55111, 55122, 9185),
            "signet": (55211, 55222, 9186),
        }
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            for network, (plain, tls, metrics) in expected.items():
                self.assertEqual(network_ports(network), (plain, tls))
                self.assertEqual(network_metrics_port(network), metrics)
                text, data = self._build(network, NetworkConfig(), paths)
                self.assertEqual(data["network"]["name"], network)
                self.assertEqual(data["network"]["data_dir"], str(paths.network_e_lite_data(network)))
                self.assertEqual(data["server"]["listen"], f"0.0.0.0:{plain}")
                self.assertEqual(data["server"]["tls_listen"], f"0.0.0.0:{tls}")
                self.assertEqual(data["core"]["core_datadir"], str(paths.network_core_data(network)))
                self.assertEqual(data["metrics"]["listen"], f"127.0.0.1:{metrics}")
                self.assertEqual(data["tor"]["mode"], "managed")
                self.assertEqual(data["tor"]["data_dir"], str(paths.network_tor_dir(network)))
                self.assertEqual(data["tor"]["onion_port"], 0)
                self.assertEqual(data["tor"]["onion_tls_port"], 0)
                self.assertGreater(data["tor"]["socks_port"], 0)
                self.assertNotIn("tor_socks", data.get("rawtx", {}))
                self.assertIn("[server]", text)

    def test_mainnet_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            _, data = self._build("mainnet", NetworkConfig(), paths)
            self.assertTrue(data["safety"]["allow_mainnet"])
            _, data = self._build("signet", NetworkConfig(), paths)
            self.assertFalse(data["safety"]["allow_mainnet"])

    def test_defaults_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            _, data = self._build("signet", NetworkConfig(), paths)
            self.assertNotIn("index", data)
            self.assertNotIn("pruning", data)
            self.assertNotIn("max_clients", data["server"])
            self.assertNotIn("prune_mib", data["core"])
            self.assertNotIn("rawtx", data)

    def test_overrides_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            config = NetworkConfig(
                overrides={
                    "index.profile": "nvme",
                    "index.cache_mib": 4096,
                    "server.max_clients": 64,
                    "core.prune_mib": 0,
                    "core.extra_args": ["-dbcache=256", "-maxmempool=128"],
                    "logging.max_mib": 32,
                }
            )
            _, data = self._build("testnet4", config, paths)
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


if __name__ == "__main__":
    unittest.main()
