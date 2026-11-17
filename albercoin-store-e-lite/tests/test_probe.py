from __future__ import annotations

import unittest

from elite.probe import electrum_endpoints, esplora_endpoints, probe_network


class ProbeTests(unittest.TestCase):
    def test_electrum_endpoints(self) -> None:
        got = electrum_endpoints(
            ["electrum.emzy.de:50002:s", "plain.example:50001:t", "bad", "[::1]:50002:s"]
        )
        self.assertEqual(got, [("electrum.emzy.de", 50002, True), ("plain.example", 50001, False)])

    def test_esplora_endpoints(self) -> None:
        got = esplora_endpoints(
            ["https://mempool.space/api", "http://insecure.example/api", "https://blockstream.info/signet/api"]
        )
        self.assertEqual(got, [("mempool.space", "/api"), ("blockstream.info", "/signet/api")])

    def test_dead_tor_is_reported_not_raised(self) -> None:
        result = probe_network([("example.com", 50002, True)], [("example.com", "/api")], socks_port=1)
        self.assertFalse(result["tor_ok"])
        self.assertEqual(result["providers"], [])
        self.assertIn("tor_error", result)

    def test_empty_provider_lists(self) -> None:
        result = probe_network([], [], socks_port=1)
        self.assertFalse(result["tor_ok"])
        self.assertEqual(result["providers"], [])


if __name__ == "__main__":
    unittest.main()
