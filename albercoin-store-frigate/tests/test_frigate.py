"""Offline tests of the actual Python and shell embedded in Compose (stdlib only)."""
import json
import os
from pathlib import Path
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"
TEXT = COMPOSE.read_text()
WEB = "\n".join(line[8:] for line in TEXT.split("      - |\n", 1)[1].split("    volumes:\n", 1)[0].splitlines())
SHELL = "\n".join(line[6:] for line in TEXT.split("    command: |\n", 1)[1].split("    volumes:\n", 1)[0].splitlines()).replace("$$", "$")


def load_web():
    namespace = {"__name__": "frigate_test"}
    exec(compile(WEB.rsplit("\nensure_settings()", 1)[0], str(COMPOSE), "exec"), namespace)
    return namespace


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.web = load_web()
        root = Path(self.tmp.name)
        self.web.update(DATA_DIR=root, SETTINGS_FILE=root / "settings.env", LOG_FILE=root / "frigate.log", RESTART_FILE=root / ".restart-requested", TOR_HOSTNAME_FILE=root / "hostname")

    def test_first_install_waits_and_legacy_is_preserved(self):
        settings = self.web["read_settings"]()
        self.assertEqual(settings["SETUP_REQUIRED"], "true")
        self.assertEqual(settings["CACHE_SIZE"], "1M")
        self.assertEqual(settings["MEMORY_LIMIT"], "1GB")
        self.assertEqual(settings["MAX_SUBSCRIPTIONS"], "5")
        html = self.web["status_body"]("es")
        self.assertIn("Confirmar e iniciar", html)
        self.assertIn("type=\"date\"", html)
        self.web["SETTINGS_FILE"].write_text("CACHE_SIZE=3M\nCOMPUTE_BACKEND=AUTO\nMEMORY_LIMIT=\n")
        legacy = self.web["read_settings"]()
        self.assertEqual(legacy["SETUP_REQUIRED"], "false")
        self.assertEqual(legacy["AUTO_PROFILE"], "false")
        self.assertEqual(legacy["CACHE_SIZE"], "3M")
        self.assertEqual(legacy["MEMORY_LIMIT"], "")

    def test_log_is_bounded_and_ready_survives_tail(self):
        self.web["LOG_FILE"].write_text("x" * 300000 + "\nlast entry\n")
        lines = self.web["read_log"](self.web["LOG_FILE"])
        self.assertEqual(lines, ["last entry"])
        (self.web["DATA_DIR"] / ".service-ready").touch()
        self.assertEqual(self.web["status_from_log"](lines)[0], "status_ready")

    def test_progress_from_tail_only(self):
        result = self.web["progress_info"](["Indexing progress: 100 / 200 blocks (50.0%, height 709732)"], "mainnet", "", False, "es")
        self.assertEqual(result[0], "50.00%")
        self.assertEqual(result[-1], "709832")

    def test_date_race_returns_first_success_not_first_failure(self):
        def unavailable(*args):
            raise OSError("secret RPC credentials")
        def success(*args):
            return 709632, 1636866927
        self.web.update(find_with_core=unavailable, find_with_electrum=success)
        result = self.web["find_date"]("2022-01-01")
        self.assertEqual(result["source"], "Electrs")
        self.web["find_with_electrum"] = unavailable
        with self.assertRaisesRegex(ValueError, "sincronizados") as error:
            self.web["find_date"]("2022-01-01")
        self.assertNotIn("secret", str(error.exception))

    def test_core_http_authentication_and_payload(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        received = []
        class Backend(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.headers["Authorization"], json.loads(self.rfile.read(int(self.headers["content-length"])))))
                body = b'{"result": "test-hash", "error": null}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        with HTTPServer(("127.0.0.1", 0), Backend) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict(os.environ, APP_BITCOIN_NODE_IP="127.0.0.1", APP_BITCOIN_RPC_PORT=str(server.server_port), APP_BITCOIN_RPC_USER="test", APP_BITCOIN_RPC_PASS="password"):
                    self.assertEqual(self.web["core_rpc"]("getblockhash", [800000]), "test-hash")
                self.assertEqual(received[0][0], "Basic dGVzdDpwYXNzd29yZA==")
                self.assertEqual(received[0][1]["params"], [800000])
            finally:
                server.shutdown()
                thread.join()

    def test_slow_loser_does_not_delay_result(self):
        finished = threading.Event()
        def slow(*args):
            finished.wait(1)
            return 10, 1000
        self.web.update(find_with_core=slow, find_with_electrum=lambda *args: (20, 2000))
        started = time.monotonic()
        try:
            self.assertEqual(self.web["find_date"]("2022-01-01")["height"], 20)
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            finished.set()

    def test_future_and_invalid_dates(self):
        for date in ("2100-01-01", "not-a-date", "2026-02-31"):
            with self.assertRaises(ValueError):
                self.web["find_date"](date)

    def test_search_uses_median_and_lookback(self):
        header = lambda height: (height * 600 + (300 if height % 2 else -300), height * 600)
        result = self.web["search_height"](600000, 2000, header, 0, time.monotonic() + 5)
        self.assertEqual(result[0], 856)
        self.assertEqual(self.web["search_height"](-100, 2000, header, 100, time.monotonic() + 5)[0], 100)

    def test_core_rejects_sync_and_finds_height(self):
        info = {"blocks": 2000, "headers": 2000, "initialblockdownload": False}
        def rpc(method, params):
            if method == "getblockchaininfo":
                return info
            if method == "getblockhash":
                return params[0]
            return {"time": params[0] * 600, "mediantime": params[0] * 600}
        self.web["core_rpc"] = rpc
        self.assertEqual(self.web["find_with_core"](600000, 0, time.monotonic() + 5)[0], 856)
        info["initialblockdownload"] = True
        with self.assertRaises(ValueError):
            self.web["find_with_core"](600000, 0, time.monotonic() + 5)

    def test_electrum_protocol_and_median(self):
        class Backend(socketserver.StreamRequestHandler):
            def handle(self):
                for line in self.rfile:
                    request = json.loads(line)
                    method, params = request["method"], request["params"]
                    def header(height):
                        return (b"\0" * 68 + (height * 600).to_bytes(4, "little") + b"\0" * 8).hex()
                    if method == "server.version":
                        result = ["test", "1.4"]
                    elif method == "blockchain.headers.subscribe":
                        result = {"height": 2000, "hex": header(2000)}
                    else:
                        start, count = params
                        result = {"count": count, "hex": "".join(header(h) for h in range(start, start + count))}
                    self.wfile.write((json.dumps({"id": request["id"], "result": result}) + "\n").encode())
                    self.wfile.flush()
        with socketserver.TCPServer(("127.0.0.1", 0), Backend) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict(os.environ, APP_ELECTRS_NODE_IP="127.0.0.1", APP_ELECTRS_NODE_PORT=str(server.server_address[1]), APP_BITCOIN_NETWORK="regtest"):
                    # Median time at h is (h-5)*600; first match is h=1005.
                    result = self.web["find_with_electrum"](600000, 0, time.monotonic() + 5)
                self.assertEqual(result[0], 861)
            finally:
                server.shutdown()
                thread.join()

    def test_http_confirmation_and_manual_changes(self):
        self.web["ensure_settings"]()
        server = self.web["ThreadingHTTPServer"](("127.0.0.1", 0), self.web["Handler"])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        import urllib.request
        url = f"http://127.0.0.1:{server.server_port}/config"
        with urllib.request.urlopen(url.rsplit("/", 1)[0]) as response:
            self.assertNotIn('http-equiv="refresh"', response.read().decode())
        for automatic in ("true", "false"):
            with urllib.request.urlopen(url, data=b"START_HEIGHT=800000") as response:
                self.assertEqual(response.status, 200)
            settings = self.web["read_settings"]()
            self.assertEqual(settings["SETUP_REQUIRED"], "false")
            self.assertEqual(settings["AUTO_PROFILE"], automatic)
            self.assertEqual(settings["START_HEIGHT"], "800000")
        self.assertTrue(self.web["RESTART_FILE"].exists())


class ShellTests(unittest.TestCase):
    def test_shell_syntax(self):
        subprocess.run(["sh", "-n"], input=SHELL, text=True, check=True)

    def test_wait_and_one_time_profile_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            runtime = data / "runtime/frigate-1.6.1-x86_64/bin"
            runtime.mkdir(parents=True)
            binary = runtime / "frigate"
            binary.write_text(f'''#!/bin/sh
cp "{data}/config.toml" "{data}/config-$(cat "{data}/count" 2>/dev/null || echo 0).toml"
if [ -f "{data}/count" ]; then exit 0; fi
echo 1 > "{data}/count"
echo "Electrum server listening" > "{data}/frigate.log"
trap 'exit 0' TERM
while true; do sleep 1; done
''')
            binary.chmod(0o700)
            settings = load_web()["DEFAULTS"].copy()
            settings["START_HEIGHT"] = "800000"
            setting_file = data / "settings.env"
            def save():
                setting_file.write_text("".join(f"{k}={v}\n" for k, v in settings.items()))
            save()
            script = SHELL.replace("/data/frigate", str(data)).replace('arch="$(dpkg --print-architecture)"', 'arch="amd64"')
            env = dict(os.environ, APP_BITCOIN_NODE_IP="bitcoin", APP_BITCOIN_RPC_PORT="8332", APP_BITCOIN_RPC_USER="test", APP_BITCOIN_RPC_PASS="test", APP_ELECTRS_NODE_IP="electrs", APP_ELECTRS_NODE_PORT="50001", APP_FRIGATE_ELECTRUM_PORT="50005", APP_BITCOIN_NETWORK="mainnet")
            process = subprocess.Popen(["sh", "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                time.sleep(0.2)
                self.assertFalse((data / "config.toml").exists())
                self.assertIsNone(process.poll())
                settings["SETUP_REQUIRED"] = "false"
                save()
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, stdout + stderr)
                initial = (data / "config-0.toml").read_text()
                steady = (data / "config-1.toml").read_text()
                self.assertIn('cacheSize = "10M"', initial)
                self.assertIn('memoryLimit = "2GB"', initial)
                self.assertIn('cacheSize = "1M"', steady)
                self.assertIn('memoryLimit = "1GB"', steady)
                self.assertIn("maxConnections = 15", steady)
                self.assertIn("startHeight = 800000", steady)
                self.assertTrue((data / ".efficient-profile").exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == "__main__":
    unittest.main()
