"""Offline tests of the actual Python and shell embedded in Compose (stdlib only)."""
import json
import os
from pathlib import Path
import signal
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


def _healthcheck():
    body = TEXT.split("    healthcheck:\n", 1)[1].split("        - >-\n", 1)[1]
    lines = []
    for line in body.splitlines():
        if not line.startswith("          "):
            break
        lines.append(line.strip())
    return " ".join(lines)


COMPOSE_HEALTHCHECK = _healthcheck()


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

    def test_status_page_has_log_controls_without_meta_refresh(self):
        self.web["ensure_settings"]()
        settings = self.web["read_settings"]()
        settings["SETUP_REQUIRED"] = "false"
        self.web["write_settings"](settings)
        self.web["LOG_FILE"].write_text("2026-11-24 10:00:00 [main] INFO ready\n")
        body = self.web["status_body"]("es")
        self.assertIn('id="frigate-log"', body)
        self.assertIn('class="copy-button log-copy"', body)
        self.assertIn('class="copy-button log-end"', body)
        self.assertIn('href="/log"', body)
        self.assertIn("refreshPanel", body)
        html = self.web["page"]("Frigate", body, "es")
        self.assertNotIn('http-equiv="refresh"', html)

    def test_current_run_filter_and_watchdog_notice(self):
        import urllib.request
        self.web["ensure_settings"]()
        settings = self.web["read_settings"]()
        settings["SETUP_REQUIRED"] = "false"
        settings["START_HEIGHT"] = "800000"
        self.web["write_settings"](settings)
        root = self.web["DATA_DIR"]
        (root / ".efficient-profile").write_text("1\n")
        (root / ".run-start").write_text("2026-11-25 12:00:00\n")
        self.web["LOG_FILE"].write_text(
            "2026-11-24 08:00:00,000 INFO [main] c.s.f.e.ElectrumServerRunnable Electrum server listening on tcp://0.0.0.0:50005\n"
            "2026-11-24 09:00:00,000 ERROR [main] c.s.f.Frigate old failure\n"
            "2026-11-25 12:00:05,000 INFO [main] c.s.f.e.ElectrumServerRunnable Electrum server listening on tcp://0.0.0.0:50005\n"
        )
        body = self.web["status_body"]("es")
        self.assertIn("Listo", body)
        self.assertNotIn("Revisar", body)
        (root / ".watchdog-restart").write_text("2026-11-25 12:30:00\n")
        body = self.web["status_body"]("es")
        self.assertIn("se reinició automáticamente", body)
        server = self.web["ThreadingHTTPServer"](("127.0.0.1", 0), self.web["Handler"])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/config", data=b"START_HEIGHT=800000") as response:
            self.assertEqual(response.status, 200)
        self.assertFalse((root / ".watchdog-restart").exists())

    def test_log_download_and_widget(self):
        import urllib.error
        import urllib.request
        self.web["ensure_settings"]()
        settings = self.web["read_settings"]()
        settings["SETUP_REQUIRED"] = "false"
        settings["START_HEIGHT"] = "800000"
        self.web["write_settings"](settings)
        (self.web["DATA_DIR"] / ".efficient-profile").write_text("1\n")
        self.web["LOG_FILE"].write_text("line one\nElectrum server listening on tcp://0.0.0.0:50005\n")
        server = self.web["ThreadingHTTPServer"](("127.0.0.1", 0), self.web["Handler"])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/log") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("attachment", response.headers.get("content-disposition", ""))
            self.assertIn(b"Electrum server listening", response.read())
        with urllib.request.urlopen(base + "/api/widget/status") as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["type"], "three-stats")
        self.assertEqual(payload["items"][0]["text"], "Listo")
        self.assertEqual(payload["items"][1]["text"], "100.00%")
        settings["SETUP_REQUIRED"] = "true"
        self.web["write_settings"](settings)
        self.assertEqual(self.web["widget_payload"]()["items"][0]["text"], "Configurando")
        self.web["LOG_FILE"].unlink()
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/log")
        self.assertEqual(error.exception.code, 404)

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


    def test_diagnosis_separates_node_from_container(self):
        diagnosis = self.web["diagnosis"]
        self.assertIn("no tiene red", diagnosis("es", {"code": "no_route"}, {"core": True}))
        self.assertIn("no route", diagnosis("en", {"code": "no_host_route"}, {"core": True}))
        self.assertIn("ni Frigate ni este panel", diagnosis("es", {"code": "no_route"}, {"core": False}))
        self.assertIn("reinicia", diagnosis("es", {"code": "ok"}, {"core": False}))
        self.assertIn("Bitcoin Core", diagnosis("es", {"code": "refused"}, {"core": True}))
        self.assertEqual(diagnosis("es", {"code": "ok"}, {"core": True}), "")
        self.assertEqual(diagnosis("es", None, {"core": False}), "")

    def test_status_page_and_widget_report_lost_network(self):
        self.web["ensure_settings"]()
        settings = self.web["read_settings"]()
        settings["SETUP_REQUIRED"] = "false"
        settings["START_HEIGHT"] = "800000"
        self.web["write_settings"](settings)
        root = self.web["DATA_DIR"]
        (root / ".efficient-profile").write_text("1\n")
        self.web["LOG_FILE"].write_text("2026-11-27 11:00:00,000 INFO [main] c.s.f.e.ElectrumServerRunnable Electrum server listening on tcp://0.0.0.0:50005\n")
        (root / ".health.json").write_text('{"code":"no_route","core":"no_route","tor_dns":"fail","at":"2026-11-27T11:00:00Z"}\n')
        self.web.update(web_probe=lambda: {"at": 0.0, "core": True})
        body = self.web["status_body"]("es")
        self.assertIn("no tiene red", body)
        self.assertIn("Comprobación de red", body)
        self.assertIn("sin ruta", body)
        self.assertEqual(self.web["widget_payload"]()["items"][0]["text"], "Sin red")
        (root / ".health.json").write_text('{"code":"ok","core":"ok","tor_dns":"ok","at":"2026-11-27T11:00:00Z"}\n')
        body = self.web["status_body"]("es")
        self.assertNotIn("no tiene red", body)
        self.assertIn("Listo", body)
        # Older containers write no .health.json; the Java route errors still warn.
        (root / ".health.json").unlink()
        self.web["LOG_FILE"].write_text(
            "2026-11-27 11:00:00,000 INFO [main] c.s.f.Frigate Starting Frigate v1.6.1 on mainnet\n"
            "2026-11-27 11:00:01,000 ERROR [main] c.s.f.Frigate Fatal error\n"
            "Caused by: java.net.SocketException: Network is unreachable\n"
        )
        body = self.web["status_body"]("es")
        self.assertIn("no tiene red", body)

    def test_health_check_uses_route_state_and_admin_port(self):
        import socket
        self.web["ensure_settings"]()
        settings = self.web["read_settings"]()
        settings["SETUP_REQUIRED"] = "false"
        self.web["write_settings"](settings)
        root = self.web["DATA_DIR"]
        log = root / "frigate.log"
        log.write_text("line\n")
        command = COMPOSE_HEALTHCHECK.replace("$$", "$").replace("/data/frigate", str(root))
        health = root / ".health.json"
        health.write_text('{"code":"no_route","core":"no_route","tor_dns":"fail","at":"now"}\n')
        self.assertNotEqual(subprocess.run(["sh", "-c", command]).returncode, 0)
        health.write_text('{"code":"ok","core":"ok","tor_dns":"ok","at":"now"}\n')
        self.assertEqual(subprocess.run(["sh", "-c", command]).returncode, 0)
        old = time.time() - 3600
        os.utime(log, (old, old))
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        admin = command.replace("127.0.0.1/50003", f"127.0.0.1/{listener.getsockname()[1]}")
        self.assertEqual(subprocess.run(["sh", "-c", admin]).returncode, 0)
        listener.close()
        self.assertNotEqual(subprocess.run(["sh", "-c", admin]).returncode, 0)


class ShellTests(unittest.TestCase):
    @staticmethod
    def _prepare(data, binary_script):
        runtime = data / "runtime/frigate-1.6.1-x86_64/bin"
        runtime.mkdir(parents=True)
        binary = runtime / "frigate"
        binary.write_text(binary_script)
        binary.chmod(0o700)
        (data / "settings.env").write_text("START_HEIGHT=\nSETUP_REQUIRED=false\nAUTO_PROFILE=false\n")
        fake_bin = data.parent / "bin"
        fake_bin.mkdir(exist_ok=True)
        getent = fake_bin / "getent"
        getent.write_text("#!/bin/sh\necho 203.0.113.42 tor\n")
        getent.chmod(0o755)
        script = SHELL.replace("/data/frigate", str(data)).replace('arch="$(dpkg --print-architecture)"', 'arch="amd64"')
        env = dict(os.environ, APP_BITCOIN_NODE_IP="bitcoin", APP_BITCOIN_RPC_PORT="8332", APP_BITCOIN_RPC_USER="test", APP_BITCOIN_RPC_PASS="test", APP_ELECTRS_NODE_IP="electrs", APP_ELECTRS_NODE_PORT="50001", APP_FRIGATE_ELECTRUM_PORT="50005", APP_BITCOIN_NETWORK="mainnet", FRIGATE_NET_PREFLIGHT="0")
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
        return script, env

    @staticmethod
    def _wait_until(predicate, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.2)
        return False

    @staticmethod
    def _stop(process, data):
        (data / "stop").write_text("")
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()

    def test_log_rotation_keeps_recent_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            binary = f'''#!/bin/sh
echo "new-run-line" >> "{data}/frigate.log"
while [ ! -f "{data}/stop" ]; do sleep 0.2; done
exit 0
'''
            script, env = self._prepare(data, binary)
            env["FRIGATE_LOG_MAX_BYTES"] = "1200"
            env["FRIGATE_LOG_KEEP_BYTES"] = "600"
            (data / "frigate.log").write_text("\n".join(f"old-line-{i:04d} padding-padding-padding" for i in range(200)) + "\n")
            process = subprocess.Popen(["sh", "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(self._wait_until(lambda: (data / ".run-start").exists()))
                self.assertTrue(self._wait_until(lambda: (data / "frigate.log").exists() and "new-run-line" in (data / "frigate.log").read_text()))
                text = (data / "frigate.log").read_text()
                self.assertIn("old-line-0199", text)
                self.assertNotIn("old-line-0000", text)
                self.assertLess((data / "frigate.log").stat().st_size, 2000)
            finally:
                self._stop(process, data)

    def test_readiness_ignores_previous_run_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            binary = f'''#!/bin/sh
while [ ! -f "{data}/release" ]; do sleep 0.2; done
echo "$(date '+%Y-%m-%d %H:%M:%S'),000 INFO [main] c.s.f.e.ElectrumServerRunnable Electrum server listening on tcp://0.0.0.0:50005" >> "{data}/frigate.log"
while [ ! -f "{data}/stop" ]; do sleep 0.2; done
exit 0
'''
            script, env = self._prepare(data, binary)
            (data / "frigate.log").write_text("2026-11-24 08:00:00,000 INFO [main] c.s.f.e.ElectrumServerRunnable Electrum server listening on tcp://0.0.0.0:50005\n")
            process = subprocess.Popen(["sh", "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                time.sleep(3)
                self.assertFalse((data / ".service-ready").exists())
                (data / "release").write_text("")
                self.assertTrue(self._wait_until(lambda: (data / ".service-ready").exists()))
            finally:
                self._stop(process, data)

    def test_watchdog_restarts_stalled_frigate(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            binary = f'''#!/bin/sh
echo "start" >> "{data}/runs"
echo "$(date '+%Y-%m-%d %H:%M:%S'),000 INFO [main] c.s.f.e.ElectrumServerRunnable Electrum server listening on tcp://0.0.0.0:50005" >> "{data}/frigate.log"
while [ ! -f "{data}/stop" ]; do sleep 0.2; done
exit 0
'''
            script, env = self._prepare(data, binary)
            env["FRIGATE_WATCHDOG_SECONDS"] = "1"
            process = subprocess.Popen(["sh", "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(self._wait_until(lambda: (data / ".service-ready").exists()))
                old = time.time() - 120
                os.utime(data / "frigate.log", (old, old))
                self.assertTrue(self._wait_until(lambda: (data / ".watchdog-restart").exists()))
                self.assertTrue(self._wait_until(lambda: (data / "runs").exists() and len((data / "runs").read_text().splitlines()) >= 2))
            finally:
                self._stop(process, data)

    def test_shell_syntax(self):
        subprocess.run(["sh", "-n"], input=SHELL, text=True, check=True)
        # The whole script is passed as a single-quoted sh -c argument, so an
        # unescaped quote anywhere inside would silently truncate it.
        self.assertEqual(SHELL.count("'"), 2)

    @staticmethod
    def _kill(process):
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        try:
            return process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.communicate()

    def test_config_has_admin_port_and_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            binary = f'''#!/bin/sh
while [ ! -f "{data}/stop" ]; do sleep 0.2; done
exit 0
'''
            script, env = self._prepare(data, binary)
            process = subprocess.Popen(["sh", "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                self.assertTrue(self._wait_until(lambda: (data / "config.toml").exists()))
                text = (data / "config.toml").read_text()
                self.assertIn("[server]", text)
                self.assertIn("adminPort = 50003", text)
                self.assertIn("[limits]", text)
                self.assertFalse((data / "config.toml.tmp").exists())
                self.assertTrue(self._wait_until(lambda: (data / ".health.json").exists()))
                health = json.loads((data / ".health.json").read_text())
                self.assertEqual(health["code"], "ok")
                self.assertEqual(health["core"], "unknown")
                self.assertEqual(health["tor_dns"], "ok")
            finally:
                self._stop(process, data)

    def test_preflight_no_route_waits_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            script, env = self._prepare(data, f'''#!/bin/sh
touch "{data}/binary-ran"
exit 0
''')
            env["FRIGATE_NET_PREFLIGHT"] = "1"
            fake_bin = Path(env["PATH"].split(os.pathsep)[0])
            fake_bash = fake_bin / "bash"
            fake_bash.write_text("#!/bin/sh\necho 'bash: connect: Network is unreachable' >&2\necho 'bash: /dev/tcp/bitcoin/8332: Connection failed.' >&2\nexit 1\n")
            fake_bash.chmod(0o755)
            process = subprocess.Popen(["sh", "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                self.assertTrue(self._wait_until(lambda: (data / ".health.json").exists()))
                health = json.loads((data / ".health.json").read_text())
                self.assertEqual(health["code"], "no_route")
                self.assertEqual(health["core"], "no_route")
                self.assertFalse((data / "config.toml").exists())
                self.assertFalse((data / "binary-ran").exists())
            finally:
                _, stderr = self._kill(process)
            self.assertIn("Restart the Frigate app", stderr)

    def test_preflight_ok_starts_frigate(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            binary = f'''#!/bin/sh
echo "started" >> "{data}/runs"
while [ ! -f "{data}/stop" ]; do sleep 0.2; done
exit 0
'''
            script, env = self._prepare(data, binary)
            env["FRIGATE_NET_PREFLIGHT"] = "1"
            fake_bin = Path(env["PATH"].split(os.pathsep)[0])
            fake_bash = fake_bin / "bash"
            fake_bash.write_text("#!/bin/sh\nexit 0\n")
            fake_bash.chmod(0o755)
            process = subprocess.Popen(["sh", "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                self.assertTrue(self._wait_until(lambda: (data / "runs").exists()))
                health = json.loads((data / ".health.json").read_text())
                self.assertEqual(health["code"], "ok")
                self.assertEqual(health["core"], "ok")
            finally:
                self._stop(process, data)

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
            fake_bin = root / "bin"
            fake_bin.mkdir()
            getent = fake_bin / "getent"
            getent.write_text("#!/bin/sh\necho 203.0.113.42 tor\n")
            getent.chmod(0o755)
            settings = load_web()["DEFAULTS"].copy()
            settings["START_HEIGHT"] = "800000"
            setting_file = data / "settings.env"
            def save():
                setting_file.write_text("".join(f"{k}={v}\n" for k, v in settings.items()))
            save()
            script = SHELL.replace("/data/frigate", str(data)).replace('arch="$(dpkg --print-architecture)"', 'arch="amd64"')
            env = dict(os.environ, APP_BITCOIN_NODE_IP="bitcoin", APP_BITCOIN_RPC_PORT="8332", APP_BITCOIN_RPC_USER="test", APP_BITCOIN_RPC_PASS="test", APP_ELECTRS_NODE_IP="electrs", APP_ELECTRS_NODE_PORT="50001", APP_FRIGATE_ELECTRUM_PORT="50005", APP_BITCOIN_NETWORK="mainnet", FRIGATE_NET_PREFLIGHT="0")
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            process = subprocess.Popen(["sh", "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                time.sleep(0.2)
                self.assertFalse((data / "config.toml").exists())
                self.assertIsNone(process.poll())
                settings["SETUP_REQUIRED"] = "false"
                save()
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 1, stdout + stderr)
                self.assertIn("exited unexpectedly", stderr)
                initial = (data / "config-0.toml").read_text()
                steady = (data / "config-1.toml").read_text()
                self.assertIn('cacheSize = "10M"', initial)
                self.assertIn('memoryLimit = "2GB"', initial)
                self.assertIn('cacheSize = "1M"', steady)
                self.assertIn('memoryLimit = "1GB"', steady)
                self.assertIn("maxConnections = 15", steady)
                self.assertIn('excludedSubnets = ["127.0.0.1/32", "::1/128", "203.0.113.42/32"]', steady)
                self.assertIn("startHeight = 800000", steady)
                self.assertTrue((data / ".efficient-profile").exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == "__main__":
    unittest.main()
