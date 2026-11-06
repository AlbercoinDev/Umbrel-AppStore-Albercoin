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
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:  # type: ignore[attr-defined]
            return error.code

    def test_status_page(self) -> None:
        status, content_type, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("e-lite", body)
        self.assertIn("184,250", body)
        self.assertIn("umbrel.local:55011", body)
        self.assertIn("umbrel.local:55022", body)
        self.assertIn("Configuración", body)
        self.assertIn("Modo demostración", body)

    def test_config_page_has_default_controls(self) -> None:
        _, _, body = self.get("/config")
        self.assertIn('name="default__index.profile"', body)
        self.assertIn('name="value__index.profile"', body)
        self.assertIn("Usar valor predeterminado", body)
        self.assertIn("index.cache_mib", body)
        self.assertIn('name="public_hostnames"', body)

    def test_api_status(self) -> None:
        _, content_type, body = self.get("/api/status")
        self.assertIn("application/json", content_type)
        data = json.loads(body)
        self.assertEqual(data["network"], "signet")
        self.assertEqual(data["indexed_height"], 184250)
        self.assertEqual(data["app"], "e-lite")

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

    def test_setup_post_saves_network(self) -> None:
        status = self.post("/setup", {"network": "testnet4"})
        self.assertEqual(status, 200)  # urllib follows the 303 redirect
        settings = load_settings(self.paths.settings_file)
        self.assertEqual(settings.network, "testnet4")
        self.assertTrue(settings.setup_complete)

    def test_setup_mainnet_requires_ack(self) -> None:
        data = urlencode({"network": "mainnet"}).encode()
        request = urllib.request.Request(self.base + "/setup", data=data, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode()
        self.assertIn("mainnet_ack", body)
        self.assertIn("Confirma el aviso", body)

    def test_config_post(self) -> None:
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
        self.assertEqual(settings.overrides["index.profile"], "nvme")
        self.assertEqual(settings.overrides["server.max_clients"], 64)
        self.assertEqual(settings.public_hostnames, ["elite.example.org"])
        self.assertTrue(settings.setup_complete)


if __name__ == "__main__":
    unittest.main()
