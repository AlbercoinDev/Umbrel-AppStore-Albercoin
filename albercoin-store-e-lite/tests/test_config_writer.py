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

    def _build(
        self,
        network: str,
        config: NetworkConfig,
        paths: AppPaths,
        active_networks: int = 1,
        total_mib: int = 7909,
    ) -> tuple[str, dict]:
        text = build_config(
            network,
            config,
            paths,
            bitcoin_binary=paths.runtime / "bitcoin-1" / "bin" / "bitcoind",
            tor_binary=paths.runtime / "tor-1" / "tor",
            active_networks=active_networks,
            total_mib=total_mib,
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
                self.assertTrue(data["server"]["tls"])
                self.assertFalse(data["server"]["tls_auto_cert"])
                self.assertNotIn("tls_auto_cert", data["tor"])
                self.assertEqual(data["core"]["core_datadir"], str(paths.network_core_data(network)))
                self.assertTrue(data["core"]["require_loopback_rpc"])
                self.assertEqual(data["public_servers"]["file"], str(paths.network_public_servers(network)))
                self.assertEqual(data["metrics"]["listen"], f"127.0.0.1:{metrics}")
                self.assertEqual(data["tor"]["mode"], "managed")
                self.assertEqual(data["tor"]["data_dir"], str(paths.network_tor_dir(network)))
                self.assertEqual(data["tor"]["onion_port"], 0)
                self.assertEqual(data["tor"]["onion_tls_port"], 0)
                self.assertGreater(data["tor"]["socks_port"], 0)
                self.assertNotIn("tor_socks", data.get("rawtx", {}))
                self.assertIn("[server]", text)

    def test_memory_fraction_split_between_networks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            _, data = self._build("signet", NetworkConfig(), paths, active_networks=2)
            self.assertEqual(data["index"]["memory_fraction"], 0.3)
            _, data = self._build("signet", NetworkConfig(), paths, active_networks=3)
            self.assertEqual(data["index"]["memory_fraction"], 0.2)
            _, data = self._build("signet", NetworkConfig(), paths, active_networks=1)
            self.assertNotIn("index", data)
            override = NetworkConfig(overrides={"index.memory_fraction": 0.5})
            _, data = self._build("signet", override, paths, active_networks=3)
            self.assertEqual(data["index"]["memory_fraction"], 0.5)

    def test_multi_network_dbcache_cap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            _, data = self._build("signet", NetworkConfig(), paths, active_networks=3, total_mib=7909)
            self.assertEqual(data["core"]["extra_args"], ["-dbcache=659"])
            _, data = self._build("signet", NetworkConfig(), paths, active_networks=2, total_mib=7909)
            self.assertEqual(data["core"]["extra_args"], ["-dbcache=988"])
            _, data = self._build("signet", NetworkConfig(), paths, active_networks=1, total_mib=7909)
            self.assertNotIn("extra_args", data["core"])
            override = NetworkConfig(overrides={"core.extra_args": ["-dbcache=1200"]})
            _, data = self._build("signet", override, paths, active_networks=3, total_mib=7909)
            self.assertEqual(data["core"]["extra_args"], ["-dbcache=1200"])

    def test_byte_watermarks_and_legacy_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            _, data = self._build("signet", NetworkConfig(), paths)
            self.assertNotIn("pruning", data)
            legacy = NetworkConfig(overrides={"pruning.max_bulk_lag_blocks": 12000})
            _, data = self._build("signet", legacy, paths)
            self.assertEqual(data["pruning"]["max_bulk_lag_blocks"], 12000)
            self.assertEqual(data["pruning"]["max_bulk_lag_gib"], 0)
            resume = NetworkConfig(overrides={"pruning.core_resume_ahead_blocks": 2500})
            _, data = self._build("signet", resume, paths)
            self.assertEqual(data["pruning"]["core_resume_ahead_blocks"], 2500)
            self.assertEqual(data["pruning"]["core_resume_ahead_gib"], 0)
            modern = NetworkConfig(overrides={"pruning.max_bulk_lag_gib": 50, "pruning.scheduler": "alternate"})
            _, data = self._build("signet", modern, paths)
            self.assertEqual(data["pruning"]["max_bulk_lag_gib"], 50)
            self.assertEqual(data["pruning"]["scheduler"], "alternate")

    def test_opt_in_io_and_prefetch_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            _, data = self._build("signet", NetworkConfig(), paths)
            self.assertNotIn("io_priority", data["core"])
            self.assertNotIn("cgroup_io_weight", data["core"])
            override = NetworkConfig(
                overrides={
                    "core.io_priority": 5,
                    "core.cgroup_io_weight": 100,
                    "index.prefetch_deadline_ms": 250,
                    "index.adaptive_memtables": True,
                }
            )
            _, data = self._build("signet", override, paths)
            self.assertEqual(data["core"]["io_priority"], 5)
            self.assertEqual(data["core"]["cgroup_io_weight"], 100)
            self.assertEqual(data["index"]["prefetch_deadline_ms"], 250)
            self.assertTrue(data["index"]["adaptive_memtables"])

    def test_allow_low_memory_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._paths(Path(directory))
            _, data = self._build("signet", NetworkConfig(), paths)
            self.assertNotIn("index", data)
            override = NetworkConfig(overrides={"index.allow_low_memory": True})
            _, data = self._build("signet", override, paths)
            self.assertTrue(data["index"]["allow_low_memory"])

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
                    "server.rate_limit": 100,
                    "server.rate_burst": 600,
                    "core.prune_mib": 0,
                    "core.extra_args": ["-dbcache=256", "-maxmempool=128"],
                    "logging.max_mib": 32,
                }
            )
            _, data = self._build("testnet4", config, paths)
            self.assertEqual(data["index"]["profile"], "nvme")
            self.assertEqual(data["index"]["cache_mib"], 4096)
            self.assertEqual(data["server"]["max_clients"], 64)
            self.assertEqual(data["server"]["rate_limit"], 100)
            self.assertIsInstance(data["server"]["rate_limit"], int)
            self.assertEqual(data["server"]["rate_burst"], 600)
            self.assertIsInstance(data["server"]["rate_burst"], int)
            self.assertEqual(data["core"]["prune_mib"], 0)
            self.assertEqual(data["core"]["extra_args"], ["-dbcache=256", "-maxmempool=128"])
            self.assertEqual(data["logging"]["max_mib"], 32)

    def test_toml_escaping(self) -> None:
        self.assertEqual(toml_string('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(toml_value(True), "true")
        self.assertEqual(toml_value(["a", "b"]), '["a", "b"]')


if __name__ == "__main__":
    unittest.main()
