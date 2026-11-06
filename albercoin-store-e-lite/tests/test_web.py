from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import urlencode

import support
from elite.settings import load_settings
from elite.web import make_server


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory, self.paths = support.temp_paths()
        self.app = support.demo_app(self.paths)
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    def get(self, path: str) -> tuple[int, str, str]:
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read().decode()

    def post(self, path: str, values: dict[str, str]) -> int:
        data = urlencode(values).encode()
        request = urllib.request.Request(self.base + path, data=data, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status

    def test_status_page_multi_network(self) -> None:
        status, content_type, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("e-lite", body)
        self.assertIn("184,250", body)
        self.assertIn("55211", body)
        self.assertIn("55111", body)
        self.assertIn("55011", body)
        self.assertIn("Configuración", body)
        self.assertIn("Redes", body)

    def test_config_page_has_default_controls(self) -> None:
        _, _, body = self.get("/config?network=signet")
        self.assertIn('name="default__index.profile"', body)
        self.assertIn('name="value__index.profile"', body)
        self.assertIn("Usar valor predeterminado", body)
        self.assertIn("index.cache_mib", body)
        self.assertIn('name="public_hostnames"', body)
        self.assertIn('name="value__server.rate_limit"', body)
        self.assertIn('name="value__server.rate_burst"', body)
        self.assertIn('name="enabled_mainnet"', body)

    def test_api_status(self) -> None:
        _, content_type, body = self.get("/api/status")
        self.assertIn("application/json", content_type)
        data = json.loads(body)
        self.assertEqual(data["active_count"], 2)
        self.assertEqual(data["networks"]["signet"]["indexed_height"], 184250)
        self.assertEqual(data["networks"]["testnet4"]["enabled"], True)
        self.assertFalse(data["networks"]["mainnet"]["enabled"])

    def test_widgets(self) -> None:
        _, _, body = self.get("/api/widget/status")
        data = json.loads(body)
        self.assertEqual(data["type"], "four-stats")
        self.assertEqual(len(data["items"]), 4)
        _, _, body = self.get("/api/widget/sync")
        data = json.loads(body)
        self.assertEqual(data["type"], "text-with-progress")
        self.assertGreaterEqual(data["progress"], 0)

    def test_icon_and_health(self) -> None:
        status, content_type, body = self.get("/icon.svg")
        self.assertEqual(status, 200)
        self.assertIn("svg", content_type)
        self.assertIn("<svg", body)
        status, _, body = self.get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body.strip(), "ok")

    def test_setup_post_saves_networks(self) -> None:
        status = self.post("/setup", {"enabled_testnet4": "1", "enabled_signet": "1"})
        self.assertEqual(status, 200)
        settings = load_settings(self.paths.settings_file)
        self.assertTrue(settings.network("testnet4").enabled)
        self.assertTrue(settings.network("signet").enabled)
        self.assertTrue(settings.setup_complete)

    def test_setup_mainnet_requires_ack(self) -> None:
        data = urlencode({"enabled_mainnet": "1"}).encode()
        request = urllib.request.Request(self.base + "/setup", data=data, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode()
        self.assertIn("mainnet_ack", body)
        self.assertIn("Confirma el aviso", body)

    def test_config_post_per_network(self) -> None:
        self.post(
            "/config",
            {
                "network": "signet",
                "public_hostnames": "elite.example.org",
                "value__index.profile": "nvme",
                "value__server.max_clients": "64",
            },
        )
        settings = load_settings(self.paths.settings_file)
        self.assertEqual(settings.network("signet").overrides["index.profile"], "nvme")
        self.assertEqual(settings.network("signet").overrides["server.max_clients"], 64)
        self.assertEqual(settings.network("signet").public_hostnames, ["elite.example.org"])
        self.assertFalse(settings.network("mainnet").enabled)

    def test_toggle_network(self) -> None:
        self.post("/networks/toggle", {"network": "mainnet", "enabled": "1", "mainnet_ack": "1"})
        settings = load_settings(self.paths.settings_file)
        self.assertTrue(settings.network("mainnet").enabled)
        self.post("/networks/toggle", {"network": "signet", "enabled": "0"})
        settings = load_settings(self.paths.settings_file)
        self.assertFalse(settings.network("signet").enabled)


if __name__ == "__main__":
    unittest.main()
