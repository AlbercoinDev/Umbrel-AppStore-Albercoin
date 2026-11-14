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
        self.assertIn("network=signet", body)
        self.assertIn("network=testnet4", body)
        self.assertIn("55011", body)
        self.assertIn("Configuración", body)
        self.assertIn("Redes", body)
        self.assertIn("Varias redes comparten CPU, RAM y disco", body)

    def test_config_page_has_default_controls(self) -> None:
        _, _, body = self.get("/config?network=signet")
        self.assertIn('name="default__index.profile"', body)
        self.assertIn('name="value__index.profile"', body)
        self.assertIn("Usar valor predeterminado", body)
        self.assertIn("index.cache_mib", body)
        self.assertIn('name="public_hostnames"', body)
        self.assertIn('name="value__server.rate_limit"', body)
        self.assertIn('name="value__server.rate_burst"', body)
        self.assertIn('name="value__server.plain_lan"', body)
        self.assertIn('name="value__rawtx.tor_fallback_clearnet"', body)
        self.assertIn('name="value__rawtx.prefetch"', body)
        self.assertIn('name="value__core.network"', body)
        self.assertIn('name="value__server.advertise_electrumx_compat"', body)
        self.assertIn('name="value__rawtx.tor_request_timeout_seconds"', body)
        self.assertIn('name="value__rawtx.tor_connect_timeout_seconds"', body)
        self.assertIn('name="value__rawtx.tor_stall_timeout_seconds"', body)
        self.assertIn('name="value__rawtx.p2p_tor_timeout_seconds"', body)
        self.assertIn('name="value__rawtx.max_parallel_fetches"', body)
        self.assertIn("Caché rawtx", body)
        self.assertNotIn('name="enabled_mainnet"', body)
        self.assertNotIn('action="/networks?', body)
        self.assertIn('Auto usa 200', body)
        self.assertIn('Auto usa 5000', body)
        self.assertIn('Restaurar valores predeterminados de esta red', body)

    def test_backup_nav_and_no_buttons_on_cards(self) -> None:
        _, _, body = self.get("/")
        self.assertIn('href="/backup?lang=es"', body)
        self.assertNotIn('action=export', body)
        _, _, body = self.get("/backup?network=signet")
        self.assertIn('href="/backup?lang=es"', body)
        self.assertIn('<option value="stored" selected>', body)
        self.assertNotIn('<option value="0">', body)

    def test_backup_page_shows_digest_and_keeps_elapsed_badge(self) -> None:
        from elite.backup import BackupManager

        self.app.backup = BackupManager(
            self.paths, supervisor=None, settings_loader=lambda: load_settings(self.paths.settings_file)
        )
        self.app.backup._update(
            active=False, phase="done", action="export", message="Copia completada",
            done=10, total=10, phase_done=10, phase_total=10, phase_percent=100.0,
            digest="d" * 64,
        )
        _, _, body = self.get("/backup?network=signet")
        self.assertIn('name="expect_digest"', body)
        self.assertIn('id="digest-row"', body)
        self.assertIn('id="backup-phase-text"', body)
        self.assertIn("d" * 64, body)
        # The polling code must update the inner node, never the span that holds the badge.
        self.assertIn("backup-phase-text", body)
        self.assertNotIn("document.getElementById('backup-phase').textContent", body)

    def test_tx_get_failure_row(self) -> None:
        from types import SimpleNamespace
        from elite.web import _detail_rows

        network = SimpleNamespace(
            live={
                "server": {
                    "banner": "e-lite test (electrumx-compatible)",
                    "electrumx_compat": True,
                    "batch_lines": 4,
                    "batch_items": 120,
                    "batch_max_items": 100,
                },
                "rawtx": {
                    "tx_get_failures": {"no_source": 3, "external_failed": 1, "not_found": 0},
                    "decoys_pending": 1,
                    "cache_blocks": 2,
                    "cache_txs": 5,
                    "cache_bytes": 6144,
                },
            },
            core_height=100,
            headers=100,
            tls={},
            onion="",
        )
        body = _detail_rows(network, "es")
        self.assertIn("Fallos tx.get", body)
        self.assertIn("no_source 3", body)
        self.assertIn("external_failed 1", body)
        self.assertNotIn("not_found 0", body)
        self.assertIn("Lotes JSON-RPC", body)
        self.assertIn("Compatibilidad ElectrumX", body)
        self.assertIn("Caché rawtx", body)
        self.assertIn("Decoys pendientes", body)

    def test_tabs_connections_and_visible_logs(self) -> None:
        _, _, body = self.get('/?network=signet')
        self.assertLess(body.index('href="/?network=mainnet'), body.index('href="/?network=testnet4'))
        self.assertLess(body.index('href="/?network=testnet4'), body.index('href="/?network=signet'))
        self.assertIn('id="elite-log"', body)
        self.assertIn('id="core-log"', body)
        self.assertNotIn('<details><summary>Registro', body)
        self.assertNotIn('http-equiv="refresh"', body)
        self.assertIn('scrollHeight-el.clientHeight-el.scrollTop', body)
        self.assertIn('data-value="umbrel.local"', body)
        self.assertNotIn('umbrel.local:55211', body)
        self.assertNotIn('.onion:55222', body)
        self.assertIn('Límite peticiones/s', body)
        self.assertIn('No disponible', body)
        self.assertNotIn('name="network" value="testnet4"', body)

    def test_invalid_tab_defaults_to_mainnet(self) -> None:
        _, _, body = self.get('/?network=invalid')
        self.assertIn('name="network" value="mainnet"', body)
        self.assertIn('id="elite-log"', body)
        self.assertIn('id="core-log"', body)

    def test_api_status(self) -> None:
        _, content_type, body = self.get("/api/status")
        self.assertIn("application/json", content_type)
        data = json.loads(body)
        self.assertEqual(data["active_count"], 2)
        self.assertEqual(data["networks"]["signet"]["indexed_height"], 184250)
        self.assertEqual(data["networks"]["testnet4"]["enabled"], True)
        self.assertFalse(data["networks"]["mainnet"]["enabled"])

    def test_updated_pruning_defaults_and_overrides(self) -> None:
        from elite.settings import NetworkConfig
        config = NetworkConfig()
        self.assertEqual(config.value("pruning.prune_defer_free_gib"), 20)
        self.assertEqual(config.value("pruning.min_gain_blocks"), 5000)
        self.post('/config', {"network": "signet", "value__pruning.prune_defer_free_gib": "50", "value__pruning.min_gain_blocks": "10000"})
        config = load_settings(self.paths.settings_file).network('signet')
        self.assertEqual(config.value("pruning.prune_defer_free_gib"), 50)
        self.assertEqual(config.value("pruning.min_gain_blocks"), 10000)

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
