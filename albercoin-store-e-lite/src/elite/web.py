"""Multi-network web panel for the e-lite Umbrel app."""

from __future__ import annotations

import html
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from . import APP_VERSION
from .cache import CacheError, cache_clear, cache_status
from .config_writer import AppPaths, network_metrics_port, network_ports
from .i18n import field_help, field_label, select_language, t
from .runtime import BITCOIN_VERSION, E_LITE_VERSION, TOR_VERSION, RuntimeManager
from .settings import (
    FIELDS,
    FIELDS_BY_KEY,
    NETWORKS,
    NetworkConfig,
    Settings,
    default_label,
    format_value,
    load_settings,
    parse_form,
    parse_networks_form,
    parse_setup_form,
    save_settings,
)
from .supervisor import SupervisorManager
from .tls import TLSManager, read_onion

DEMO_STATUS: dict[str, Any] = {
    "version": E_LITE_VERSION,
    "network": "signet",
    "state": "BUILDING_INDEX",
    "indexed_height": 184250,
    "core_height": 190430,
    "core_headers": 190430,
    "core_verification_progress": 1.0,
    "ready": False,
    "fully_synced": False,
    "offline": False,
    "lag_blocks": 6180,
    "clients": 2,
    "max_clients": 32,
    "db_bytes": 21 * 1024**3,
    "disk_free_bytes": 412 * 1024**3,
    "uptime_seconds": 6 * 3600 + 42 * 60,
    "tuning": {"profile": "ssd", "cache_mib": 1024, "blocks_per_sec": 418},
    "memory": {"available_mib": 9300, "effective_mib": 8200, "dynamic_reserve_mib": 2048, "memtable_bytes_mib": 512, "table_readers_mib": 96},
    "file_descriptors": {"soft_limit": 8192, "reserve": 256, "max_open_files_requested": 8192, "max_open_files_effective": 7936},
    "tor": {"managed": True, "socks_port": 9050, "control_port": 0},
    "pipeline": {"prepared": 190500, "applied": 184250, "discarded": 620},
    "rocksdb": {"pending_compaction_bytes": 73400320, "properties_ok": True, "running_compactions": 1, "level0_files": 23, "block_cache_bytes": 838860800},
    "scheduler": {"mode": "overlap", "index_paused": False, "backlog_bytes": 12884901888, "high_bytes": 32212254720, "low_bytes": 7516192768},
    "machine": {
        "cpu": {"physical": 4, "logical": 8, "effective": 4, "physical_effective": 4, "smt": True},
        "disk": {"data": {"kind": "nvme", "mount": "/data"}, "core": {"kind": "nvme", "mount": "/data"}, "same_device": True},
        "plan": {"readers": 4, "jobs": 2, "cache_mib": 1024, "commit_ms": 20000},
    },
    "core": {"connections": 9, "initial_block_download": False, "pruned": True, "pruneheight": 176000},
}

DEMO_ONIONS = {
    "mainnet": "elitemainnet" + "a" * 43 + ".onion",
    "testnet4": "elitetestnet4" + "a" * 43 + ".onion",
    "signet": "elitesignet" + "a" * 43 + ".onion",
}
DEMO_LOGS = [
    "[e-lite] BUILDING_INDEX",
    "[e-lite] Electrum TCP/TLS listeners ready",
    "[e-lite] managed Tor bootstrapped",
    "[e-lite] height 184250/190430 · lag 6180",
]


@dataclass
class NetworkSnapshot:
    network: str
    enabled: bool
    ports: tuple[int, int]
    metrics_port: int
    runtime: dict[str, Any]
    process: dict[str, Any]
    live: dict[str, Any] | None
    onion: str
    logs: list[str]
    core_logs: list[str]
    tls: dict[str, Any]
    demo: bool = False

    @property
    def phase(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.demo:
            return "live"
        if self.runtime.get("phase") != "ready" and self.live is None:
            phase = str(self.runtime.get("phase", "pending"))
            return "runtime_error" if phase == "error" else "runtime"
        if self.live is not None:
            return "live"
        process_state = str(self.process.get("state", "idle"))
        if process_state in ("error", "exited"):
            return "process_error"
        if self.process.get("running"):
            return "starting"
        return "stopped"

    @property
    def state(self) -> str:
        if not self.enabled:
            return "DISABLED"
        if self.live is not None:
            return str(self.live.get("state", "UNKNOWN"))
        if self.phase in ("runtime_error", "process_error"):
            return "ERROR"
        if self.phase == "starting":
            return "STARTING"
        return "STOPPED"

    @property
    def indexed(self) -> int:
        return int((self.live or {}).get("indexed_height", 0) or 0)

    @property
    def core_height(self) -> int:
        return int((self.live or {}).get("core_height", 0) or 0)

    @property
    def headers(self) -> int:
        return int((self.live or {}).get("core_headers", 0) or 0)

    @property
    def core_progress(self) -> float:
        if self.headers <= 0:
            return 0.0
        return max(0.0, min(100.0, self.core_height / self.headers * 100.0))

    @property
    def index_progress(self) -> float:
        if self.headers <= 0:
            return 0.0
        return max(0.0, min(100.0, self.indexed / self.headers * 100.0))

    @property
    def core_ibd(self) -> bool:
        return bool((self.live or {}).get("core", {}).get("initial_block_download", False))

    @property
    def fully_synced(self) -> bool:
        return bool((self.live or {}).get("fully_synced", False))


@dataclass
class AppSnapshot:
    settings: Settings
    networks: dict[str, NetworkSnapshot]
    runtime: dict[str, Any]
    domain: str
    demo: bool = False

    @property
    def setup_complete(self) -> bool:
        return self.settings.setup_complete

    @property
    def active_count(self) -> int:
        return sum(1 for network in self.networks.values() if network.enabled)

    @property
    def enabled_networks(self) -> list[NetworkSnapshot]:
        return [network for network in self.networks.values() if network.enabled]


class WebApp:
    def __init__(
        self,
        paths: AppPaths,
        runtime: RuntimeManager,
        supervisor: SupervisorManager,
        tls_managers: dict[str, TLSManager],
        domain: str,
        hostname: str,
        demo: bool = False,
        backup: Any = None,
    ) -> None:
        self.paths = paths
        self.runtime = runtime
        self.supervisor = supervisor
        self.tls_managers = tls_managers
        self.domain = domain
        self.hostname = hostname
        self.demo = demo
        self.backup = backup
        self.started = time.time()

    def settings(self) -> Settings:
        return load_settings(self.paths.settings_file)

    def snapshot(self) -> AppSnapshot:
        settings = self.settings()
        if self.demo:
            settings = default_settings_demo()
        runtime = self.runtime.status()
        if self.demo:
            runtime = {
                "phase": "ready",
                "message": "Componentes listos",
                "downloaded": 0,
                "total": 0,
                "kind": "",
                "error": "",
                "e_lite_version": E_LITE_VERSION,
                "bitcoin_version": BITCOIN_VERSION,
                "tor_version": TOR_VERSION,
            }
        processes = self.supervisor.status()
        network_snapshots: dict[str, NetworkSnapshot] = {}
        for network in NETWORKS:
            enabled = settings.network(network).enabled
            live = DEMO_STATUS if self.demo and enabled else self._live(network)
            if live is not None:
                live = dict(live)
                live["network"] = network
            onion = DEMO_ONIONS[network] if self.demo and enabled else read_onion(self.paths.network_onion_file(network))
            network_snapshots[network] = NetworkSnapshot(
                network=network,
                enabled=enabled,
                ports=network_ports(network),
                metrics_port=network_metrics_port(network),
                runtime=runtime,
                process=processes.get(network, {}),
                live=live,
                onion=onion,
                logs=DEMO_LOGS if self.demo and enabled else self.supervisor.tail_log(network, limit=1000),
                core_logs=[] if self.demo else self.supervisor.core_log_tail(network, limit=1000),
                tls=self.tls_managers[network].status() if not self.demo else {
                    "ca_available": False,
                    "certificate_available": False,
                    "error": "",
                    "sans": [f"DNS:{DEMO_ONIONS[network]}", "DNS:umbrel.local", "DNS:e-lite"],
                    "public_hostnames": [],
                    "onion": DEMO_ONIONS[network],
                },
                demo=self.demo,
            )
        return AppSnapshot(
            settings=settings,
            networks=network_snapshots,
            runtime=runtime,
            domain=self.domain,
            demo=self.demo,
        )

    def _live(self, network: str) -> dict[str, Any] | None:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    f"http://127.0.0.1:{network_metrics_port(network)}/status",
                    headers={"User-Agent": "e-lite-web"},
                ),
                timeout=2,
            ) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def api_status(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        networks: dict[str, Any] = {}
        for network, data in snapshot.networks.items():
            live = data.live or {}
            networks[network] = {
                "enabled": data.enabled,
                "phase": data.phase,
                "state": data.state,
                "indexed_height": data.indexed,
                "core_height": data.core_height,
                "core_headers": data.headers,
                "index_progress": round(data.index_progress, 2),
                "core_progress": round(data.core_progress, 2),
                "lag_blocks": int(live.get("lag_blocks", 0) or 0),
                "clients": int(live.get("clients", 0) or 0),
                "onion": data.onion,
                "ports": {"plain": data.ports[0], "tls": data.ports[1]},
                "process": data.process,
                "tls": data.tls,
            }
        return {
            "app": "e-lite",
            "version": E_LITE_VERSION,
            "app_version": APP_VERSION,
            "core_version": BITCOIN_VERSION,
            "tor_version": TOR_VERSION,
            "setup_complete": snapshot.setup_complete,
            "active_count": snapshot.active_count,
            "runtime": snapshot.runtime,
            "domain": snapshot.domain,
            "networks": networks,
        }


def default_settings_demo() -> Settings:
    settings = Settings(setup_complete=True)
    settings.network("testnet4").enabled = True
    settings.network("signet").enabled = True
    return settings


def make_server(app: WebApp, port: int = 3000) -> ThreadingHTTPServer:
    handler = type("EliteHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    server.daemon_threads = True
    return server


class Handler(BaseHTTPRequestHandler):
    app: WebApp
    server_version = "e-lite-umbrel"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        lang = self._language(parsed.query)
        app = self.app
        if parsed.path in ("/healthz", "/api/health"):
            self._send_text("ok\n")
            return
        if parsed.path == "/icon.svg":
            self._send_static("icon.svg", "image/svg+xml")
            return
        if parsed.path == "/tls/ca.crt":
            if app.paths.ca_certificate.is_file():
                self._send_file(app.paths.ca_certificate, "application/x-pem-file", "e-lite-ca.crt")
            else:
                self.send_error(404)
            return
        if parsed.path == "/api/status":
            self._send_json(app.api_status())
            return
        if parsed.path == "/api/widget/status":
            self._send_json(self._widget_status())
            return
        if parsed.path == "/api/widget/sync":
            self._send_json(self._widget_sync())
            return
        if parsed.path == "/api/backup/status":
            self._send_json(app.backup.status() if app.backup else {})
            return
        if parsed.path == "/api/backup/sizes":
            params = parse_qs(parsed.query)
            network_param = (params.get("network") or [""])[0]
            self._send_json(app.backup.sizes(network_param) if app.backup else {})
            return
        if parsed.path == "/api/backup/files":
            params = parse_qs(parsed.query)
            destination = (params.get("destination") or [""])[0]
            files = app.backup.files(destination) if app.backup else []
            self._send_json({"files": files})
            return

        snapshot = app.snapshot()
        if parsed.path == "/":
            if not snapshot.setup_complete:
                self._send_html(setup_page(app, snapshot, lang, parsed.query))
            else:
                error = (parse_qs(parsed.query).get("error") or [""])[0]
                self._send_html(status_page(app, snapshot, lang, parsed.query, error=error))
            return
        if parsed.path == "/setup":
            self._send_html(setup_page(app, snapshot, lang, parsed.query))
            return
        if parsed.path == "/config":
            self._send_html(config_page(app, snapshot, lang, parsed.query))
            return
        if parsed.path == "/backup/log":
            path = app.paths.exports_dir / "backup.log"
            try:
                text = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:])
            except OSError:
                text = ""
            self._send_text(text)
            return
        if parsed.path == "/backup":
            if not snapshot.setup_complete:
                self._redirect("/setup?lang=" + lang)
                return
            self._send_html(backup_page(app, snapshot, lang, parsed.query))
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        lang = self._language(parsed.query)
        app = self.app
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length > 1024 * 1024:
            self.send_error(413)
            return
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        form = parse_qs(raw, keep_blank_values=True)

        if parsed.path == "/setup":
            previous = app.settings()
            settings, errors = parse_setup_form(form, previous)
            if errors:
                snapshot = app.snapshot()
                self._send_html(setup_page(app, snapshot, lang, parsed.query, errors=errors, values=settings))
                return
            save_settings(app.paths.settings_file, settings)
            app.supervisor.apply_settings()
            self._redirect("/")
            return

        if parsed.path == "/config":
            network = (form.get("network") or [""])[0]
            previous = app.settings()
            settings, errors = parse_form(form, network, previous)
            if errors:
                snapshot = app.snapshot()
                self._send_html(
                    config_page(app, snapshot, lang, parsed.query, errors=errors, values=settings, selected=network)
                )
                return
            save_settings(app.paths.settings_file, settings)
            app.supervisor.apply_settings(network)
            self._redirect(f"/config?saved=1&network={network}&lang={lang}")
            return

        if parsed.path == "/networks":
            previous = app.settings()
            settings, errors = parse_networks_form(form, previous)
            if errors:
                snapshot = app.snapshot()
                self._send_html(config_page(app, snapshot, lang, parsed.query, errors=errors, values=settings))
                return
            save_settings(app.paths.settings_file, settings)
            app.supervisor.apply_settings()
            self._redirect(f"/config?lang={lang}")
            return

        if parsed.path == "/networks/toggle":
            network = (form.get("network") or [""])[0]
            enabled = (form.get("enabled") or ["0"])[0] in ("1", "on", "true", "yes")
            previous = app.settings()
            toggle_form: dict[str, list[str]] = {}
            for name in NETWORKS:
                toggle_form[f"enabled_{name}"] = ["1"] if (name == network and enabled) else (
                    ["1"] if (name != network and previous.network(name).enabled) else []
                )
            if form.get("mainnet_ack"):
                toggle_form["mainnet_ack"] = form["mainnet_ack"]
            settings, errors = parse_networks_form(toggle_form, previous)
            if errors:
                snapshot = app.snapshot()
                self._send_html(status_page(app, snapshot, lang, parsed.query, error="; ".join(errors)))
                return
            save_settings(app.paths.settings_file, settings)
            app.supervisor.apply_settings(network)
            self._redirect(f"/?network={network}&lang={lang}")
            return

        if parsed.path == "/backup/cancel":
            network = (form.get("network") or [""])[0]
            if app.backup is not None:
                app.backup.cancel()
            self._redirect(f"/backup?network={network}&lang={lang}")
            return

        if parsed.path == "/backup/cleanup":
            network = (form.get("network") or [""])[0]
            confirm = (form.get("confirm") or [""])[0]
            if app.backup is None or confirm.strip() != network:
                self._redirect(f"/backup?network={network}&lang={lang}&error=" + quote("confirmación incorrecta"))
                return
            if app.backup.status().get("active"):
                self._redirect(f"/backup?network={network}&lang={lang}&error=" + quote("hay una copia en curso; espera a que termine"))
                return
            removed = app.backup.cleanup_previous(network)
            self._redirect(f"/backup?network={network}&lang={lang}" + ("&error=" + quote(f"eliminados {removed} directorios") if removed else ""))
            return

        if parsed.path == "/cache/clear":
            network = (form.get("network") or [""])[0]
            confirm = (form.get("confirm") or [""])[0]
            restart = (form.get("restart") or ["0"])[0] in ("1", "on", "true", "yes")
            if network not in NETWORKS:
                self._redirect(f"/config?lang={lang}&cache_error=" + quote("red desconocida"))
                return
            if confirm.strip() != network:
                self._redirect(f"/config?network={network}&lang={lang}&cache_error=" + quote("confirmación incorrecta"))
                return
            if app.backup is not None and app.backup.status().get("active"):
                self._redirect(f"/config?network={network}&lang={lang}&cache_error=" + quote("hay una copia en curso; espera a que termine"))
                return
            try:
                result = cache_clear(app.paths, network)
            except CacheError as exc:
                self._redirect(f"/config?network={network}&lang={lang}&cache_error=" + quote(str(exc)))
                return
            removed = result.get("removed") or {}
            files = int(removed.get("files") or 0)
            size = int(removed.get("bytes") or 0)
            if lang == "es":
                message = f"caché vaciada: {files} ficheros ({fmt_bytes(size)})"
                if restart:
                    message += "; red reiniciada"
                message += ". La caché en RAM se libera al reiniciar la red."
            else:
                message = f"cache cleared: {files} files ({fmt_bytes(size)})"
                if restart:
                    message += "; network restarted"
                message += ". The in-memory cache is released when the network restarts."
            if restart:
                app.supervisor.request_restart(network)
            self._redirect(f"/config?network={network}&lang={lang}&cache=" + quote(message))
            return

        if parsed.path == "/runtime/retry":
            app.runtime.retry()
            self._redirect("/setup?lang=" + lang)
            return

        if parsed.path == "/backup/export":
            network = (form.get("network") or [""])[0]
            destination = (form.get("destination") or [""])[0]
            compress = (form.get("compress") or ["stored"])[0]
            if app.backup is None:
                self._redirect("/?lang=" + lang)
                return
            ok, message = app.backup.start_export(network, destination, compress)
            query = f"network={network}&lang={lang}" + ("&started=1" if ok else "&error=" + quote(message))
            self._redirect("/backup?" + query)
            return

        if parsed.path == "/backup/import":
            network = (form.get("network") or [""])[0]
            destination = (form.get("destination") or [""])[0]
            filename = (form.get("filename") or [""])[0]
            confirm = (form.get("confirm") or [""])[0]
            verify = (form.get("verify") or ["basic"])[0]
            delete_previous = (form.get("delete_previous") or ["0"])[0] in ("1", "on", "true", "yes")
            expect_digest = (form.get("expect_digest") or [""])[0]
            if app.backup is None:
                self._redirect("/?lang=" + lang)
                return
            if confirm.strip() != network:
                self._redirect(f"/backup?network={network}&lang={lang}&error=" + quote("confirmación incorrecta"))
                return
            ok, message = app.backup.start_import(
                network, destination, filename, verify, delete_previous, expect_digest
            )
            query = f"network={network}&lang={lang}" + ("&started=1" if ok else "&error=" + quote(message))
            self._redirect("/backup?" + query)
            return

        self.send_error(404)

    # ------------------------------------------------------------------ utilities
    def _language(self, query: str) -> str:
        params = parse_qs(query)
        query_lang = (params.get("lang") or [None])[0]
        cookie = self._cookie("elite_lang")
        lang = select_language(self.headers.get("Accept-Language"), query_lang, cookie)
        self._set_cookie = query_lang if query_lang in ("es", "en") else None
        return lang

    def _cookie(self, name: str) -> str | None:
        raw = self.headers.get("Cookie", "")
        for chunk in raw.split(";"):
            key, _, value = chunk.strip().partition("=")
            if key == name:
                return value
        return None

    def _send_html(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if getattr(self, "_set_cookie", None):
            self.send_header("Set-Cookie", f"elite_lang={self._set_cookie}; Path=/; Max-Age=31536000; SameSite=Lax")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_static(self, name: str, content_type: str) -> None:
        candidates = [
            Path(__file__).parent / "static" / name,
            Path(__file__).parents[1] / name,
            Path(__file__).parents[2] / name,
        ]
        for candidate in candidates:
            if candidate.is_file():
                self._send_file(candidate, content_type)
                return
        self.send_error(404)

    def _send_file(self, path: Path, content_type: str, filename: str | None = None) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        if getattr(self, "_set_cookie", None):
            self.send_header("Set-Cookie", f"elite_lang={self._set_cookie}; Path=/; Max-Age=31536000; SameSite=Lax")
        self.end_headers()

    def _widget_status(self) -> dict[str, Any]:
        snapshot = self.app.snapshot()
        enabled = snapshot.enabled_networks
        clients = sum(int((n.live or {}).get("clients", 0) or 0) for n in enabled)
        indexed = sum(n.indexed for n in enabled)
        lag = sum(max(0, n.core_height - n.indexed) for n in enabled)
        return {
            "type": "four-stats",
            "refresh": "10s",
            "link": "",
            "items": [
                {"title": "Redes", "text": str(len(enabled)), "subtext": "activas"},
                {"title": "Clientes", "text": str(clients), "subtext": "conectados"},
                {"title": "Altura", "text": f"{indexed:,}", "subtext": "bloques"},
                {"title": "Lag", "text": f"{lag:,}", "subtext": "bloques"},
            ],
        }

    def _widget_sync(self) -> dict[str, Any]:
        snapshot = self.app.snapshot()
        enabled = snapshot.enabled_networks
        if not enabled:
            return {
                "type": "text-with-progress",
                "refresh": "10s",
                "link": "",
                "title": "Sincronización",
                "text": "Sin redes",
                "progressLabel": "Inactiva",
                "progress": 0,
            }
        network = enabled[0]
        progress = network.core_progress if network.core_ibd else network.index_progress
        if network.fully_synced:
            progress = 100.0
        return {
            "type": "text-with-progress",
            "refresh": "10s",
            "link": "",
            "title": f"Sincronización · {network.network}",
            "text": f"{progress:.0f}%",
            "progressLabel": "Indexando" if progress < 99.99 else "Listo",
            "progress": round(max(0.0, min(100.0, progress)) / 100.0, 4),
        }

    def log_message(self, *_args: object) -> None:
        return


# --------------------------------------------------------------------- rendering
def h(value: object) -> str:
    return html.escape(str(value), quote=True)


def fmt_bytes(value: int) -> str:
    size = float(max(0, int(value or 0)))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TiB"


def fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def state_label(lang: str, state: str) -> str:
    mapping = {
        "READY": "state_ready",
        "BUILDING_INDEX": "state_building",
        "INITIALIZING": "state_building",
        "WAITING_FOR_CORE": "state_waiting",
        "CORE_UNAVAILABLE": "state_core_unavailable",
        "PAUSE_DOWNLOAD": "state_pause",
        "PRUNING": "state_pruning",
        "REORG_RECOVERY": "state_reorg",
        "FATAL_INTEGRITY": "state_fatal",
        "DEGRADED_RAWTX": "state_degraded",
        "STARTING": "starting",
        "STOPPED": "stopped",
        "DISABLED": "network_disabled",
        "ERROR": "error",
    }
    return t(lang, mapping.get(state, "state_unknown"))


def status_class(network: NetworkSnapshot) -> str:
    if network.phase == "disabled":
        return "muted"
    if network.phase in ("setup", "runtime"):
        return "warn"
    if network.phase in ("runtime_error", "process_error"):
        return "bad"
    state = network.state
    if state == "READY" and network.fully_synced:
        return "good"
    if state in ("FATAL_INTEGRITY", "CORE_UNAVAILABLE", "DEGRADED_RAWTX"):
        return "bad"
    return "warn"


def _nav(lang: str, active: str) -> str:
    q = f"?lang={lang}"
    status = f'<a class="{"active" if active == "status" else ""}" href="/{q}">{h(t(lang, "status"))}</a>'
    config = f'<a class="{"active" if active == "config" else ""}" href="/config{q}">{h(t(lang, "configuration"))}</a>'
    backups = f'<a class="{"active" if active == "backup" else ""}" href="/backup{q}">{h(t(lang, "backup_nav"))}</a>'
    other = "en" if lang == "es" else "es"
    language = f'<a class="lang" href="?lang={other}">{h(t(lang, "language"))}</a>'
    return f'<nav>{status}{config}{backups}{language}</nav>'


def page(title: str, content: str, lang: str, active: str = "status", refresh: int = 0) -> str:
    refresh_tag = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    copied = json.dumps(t(lang, "copied"))
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh_tag}
<title>{h(title)}</title>
<style>
:root {{
  --bg:#080d18; --card:rgba(255,255,255,.055); --line:rgba(255,255,255,.11);
  --text:#eaf6ff; --muted:#9bb3c8; --accent:#00cd98; --accent2:#5cc8ff;
  --warn:#ffc46b; --bad:#ff7d7d; --good:#00cd98;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; min-height:100vh; color:var(--text); font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:radial-gradient(circle at 12% -10%,rgba(0,205,152,.17),transparent 36%),radial-gradient(circle at 95% 0%,rgba(92,200,255,.14),transparent 38%),var(--bg);
  padding:30px 18px 54px; }}
main {{ width:min(1040px,100%); margin:0 auto; }}
header {{ display:flex; align-items:center; gap:16px; margin-bottom:22px; }}
header img {{ width:64px; height:64px; border-radius:16px; box-shadow:0 12px 40px rgba(0,0,0,.35); }}
h1 {{ margin:0; font-size:clamp(28px,5vw,46px); line-height:1.05; letter-spacing:-.02em; }}
.sub {{ color:var(--muted); margin-top:6px; font-size:14px; }}
nav {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:22px; }}
nav a {{ color:var(--muted); text-decoration:none; padding:9px 14px; border-radius:999px; border:1px solid transparent; font-weight:650; font-size:14px; }}
nav a:hover {{ color:var(--text); background:rgba(255,255,255,.06); }}
nav a.active {{ color:#041b15; background:var(--accent); }}
nav a.lang {{ margin-left:auto; border-color:var(--line); color:var(--text); }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:20px; padding:22px; margin:16px 0; box-shadow:0 18px 50px rgba(0,0,0,.18); }}
.card h2 {{ margin:0 0 14px; font-size:17px; letter-spacing:.01em; }}
.card h3 {{ margin:0 0 8px; font-size:15px; }}
.pill {{ display:inline-flex; align-items:center; gap:9px; padding:9px 14px; border-radius:999px; font-weight:700; font-size:14px; border:1px solid var(--line); }}
.pill .dot {{ width:9px; height:9px; border-radius:50%; background:currentColor; box-shadow:0 0 16px currentColor; }}
.pill.good {{ color:var(--good); background:rgba(0,205,152,.1); border-color:rgba(0,205,152,.3); }}
.pill.warn {{ color:var(--warn); background:rgba(255,196,107,.1); border-color:rgba(255,196,107,.3); }}
.pill.bad {{ color:var(--bad); background:rgba(255,125,125,.1); border-color:rgba(255,125,125,.3); }}
.pill.muted {{ color:var(--muted); background:rgba(255,255,255,.05); border-color:var(--line); }}
.grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
.grid.three {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
.stat {{ padding:16px; border:1px solid var(--line); border-radius:14px; background:rgba(0,0,0,.16); }}
.stat .k {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.08em; }}
.stat .v {{ font-size:22px; font-weight:750; margin-top:6px; overflow-wrap:anywhere; }}
.progress {{ height:12px; border-radius:999px; background:rgba(255,255,255,.1); overflow:hidden; margin:8px 0 4px; border:1px solid rgba(255,255,255,.06); }}
.progress > span {{ display:block; height:100%; border-radius:inherit; background:linear-gradient(90deg,var(--accent),var(--accent2)); min-width:3px; transition:width .5s ease; }}
.progress.indeterminate > span {{ width:34%; animation:pulse 1.8s ease-in-out infinite alternate; }}
@keyframes pulse {{ from {{ transform:translateX(-10%); opacity:.5; }} to {{ transform:translateX(210%); opacity:1; }} }}
.row {{ display:flex; justify-content:space-between; gap:16px; align-items:baseline; padding:9px 0; border-bottom:1px solid rgba(255,255,255,.06); }}
.row:last-child {{ border-bottom:0; }}
.row .k {{ color:var(--muted); }}
.row .v {{ text-align:right; overflow-wrap:anywhere; }}
code {{ background:rgba(92,200,255,.1); border:1px solid rgba(92,200,255,.24); color:#b9ecff; padding:3px 7px; border-radius:7px; font-size:.9em; overflow-wrap:anywhere; }}
.endpoint {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
.endpoint code {{ display:inline-block; max-width:100%; overflow-wrap:anywhere; word-break:break-all; }}
button, .button {{ appearance:none; border:1px solid rgba(0,205,152,.35); background:rgba(0,205,152,.16); color:#a9ffe9; font-weight:700; padding:10px 14px; border-radius:11px; cursor:pointer; font-size:14px; text-decoration:none; }}
button:hover, .button:hover {{ background:rgba(0,205,152,.26); }}
button.secondary {{ border-color:var(--line); background:rgba(255,255,255,.07); color:var(--text); }}
button.danger {{ border-color:rgba(255,125,125,.4); background:rgba(255,125,125,.12); color:#ffb3b3; }}
button:disabled {{ opacity:.45; cursor:not-allowed; }}
.warning {{ border-color:rgba(255,196,107,.35); background:rgba(255,196,107,.08); color:#ffe2b0; }}
.error {{ border-color:rgba(255,125,125,.4); background:rgba(255,125,125,.1); color:#ffc0c0; }}
.help {{ color:var(--muted); font-size:13px; line-height:1.5; }}
pre {{ margin:0; padding:16px; border-radius:14px; background:rgba(0,0,0,.32); border:1px solid var(--line); overflow:auto; max-height:360px; color:#cfeaff; font-size:12.5px; line-height:1.5; }}
.logs {{ margin-top:24px; }}
.logs pre {{ height:320px; max-height:320px; overflow:auto; overflow-anchor:none; }}
.logs h3 {{ margin-top:18px; }}
.log-end {{ margin-bottom:8px; }}
details {{ margin-top:14px; }}
summary {{ cursor:pointer; color:var(--muted); font-weight:650; }}
label {{ font-weight:650; }}
input[type=text], input[type=number], select, textarea {{ width:100%; background:rgba(0,0,0,.25); border:1px solid var(--line); color:var(--text); border-radius:10px; padding:10px 12px; font:inherit; }}
input:focus, select:focus, textarea:focus {{ outline:2px solid rgba(92,200,255,.35); border-color:rgba(92,200,255,.5); }}
input:disabled, select:disabled, textarea:disabled {{ opacity:.55; }}
.field {{ display:grid; grid-template-columns:minmax(220px,1fr) minmax(260px,1.25fr); gap:18px; padding:16px 0; border-top:1px solid rgba(255,255,255,.07); }}
.field:first-of-type {{ border-top:0; }}
.field .label {{ display:flex; flex-direction:column; gap:5px; }}
.field .label code {{ align-self:flex-start; font-size:11px; }}
.checkline {{ display:flex; align-items:center; gap:9px; margin-bottom:8px; color:var(--muted); font-size:13px; }}
.checkline input {{ width:16px; height:16px; accent-color:var(--accent); }}
.network-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }}
.network {{ position:relative; display:block; padding:18px; border:1px solid var(--line); border-radius:16px; background:rgba(0,0,0,.16); cursor:pointer; }}
.network input {{ position:absolute; opacity:0; }}
.network:has(input:checked) {{ border-color:rgba(0,205,152,.65); box-shadow:0 0 0 2px rgba(0,205,152,.18) inset; background:rgba(0,205,152,.08); }}
.network strong {{ display:block; font-size:17px; margin-bottom:6px; }}
.network span {{ color:var(--muted); font-size:13px; line-height:1.45; }}
.inline {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
.badge {{ display:inline-block; padding:4px 8px; border-radius:999px; background:rgba(92,200,255,.1); border:1px solid rgba(92,200,255,.25); color:#b9ecff; font-size:12px; font-weight:700; }}
.network-head {{ display:flex; gap:14px; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; }}
.network-head h2 {{ margin:0; font-size:22px; }}
.ports {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:6px; }}
.port {{ font-size:12px; color:var(--muted); }}
.tabbar {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }}
.tabbar a {{ padding:8px 12px; border-radius:10px; border:1px solid var(--line); color:var(--muted); text-decoration:none; font-size:13px; font-weight:650; }}
.tabbar a.active {{ color:#041b15; background:var(--accent); border-color:var(--accent); }}
.footer {{ color:var(--muted); font-size:12.5px; margin-top:26px; display:flex; flex-wrap:wrap; gap:14px; }}
.footer a {{ color:var(--muted); }}
@media (max-width: 780px) {{
  .grid, .grid.three, .network-grid {{ grid-template-columns:1fr; }}
  .field {{ grid-template-columns:1fr; }}
  header {{ align-items:flex-start; }}
  nav a.lang {{ margin-left:0; }}
}}
</style>
</head>
<body><main>
<header>
  <img src="/icon.svg" alt="e-lite">
  <div>
    <h1>{h(t(lang, "app_name"))}</h1>
    <div class="sub">{h(t(lang, "version_label", version=E_LITE_VERSION, core=BITCOIN_VERSION, app=APP_VERSION))}</div>
  </div>
</header>
{_nav(lang, active)}
{content}
<div class="footer">
  <span>{h(t(lang, "footer"))}</span>
  <a href="https://github.com/AlbercoinDev/e-lite" target="_blank" rel="noreferrer">{h(t(lang, "upstream"))}</a>
  <a href="https://github.com/AlbercoinDev/e-lite/issues" target="_blank" rel="noreferrer">{h(t(lang, "support"))}</a>
</div>
</main></body>
<script>
function copyValue(value, button) {{
  const done = () => {{ const old = button.textContent; button.textContent = {copied}; setTimeout(() => button.textContent = old, 1200); }};
  if (navigator.clipboard && window.isSecureContext) {{
    navigator.clipboard.writeText(value).then(done).catch(() => fallbackCopy(value, done));
  }} else {{
    fallbackCopy(value, done);
  }}
}}
function fallbackCopy(value, done) {{
  const area = document.createElement('textarea');
  area.value = value;
  document.body.appendChild(area);
  area.select();
  try {{ document.execCommand('copy'); done(); }} catch (e) {{}}
  document.body.removeChild(area);
}}
</script>
</html>"""


def _runtime_card(snapshot: AppSnapshot, lang: str) -> str:
    runtime = snapshot.runtime
    phase = str(runtime.get("phase", "pending"))
    if phase == "ready":
        return ""
    if phase == "error":
        return f"""<div class="card error"><h2>{h(t(lang, "runtime_error"))}</h2>
<p>{h(runtime.get("error", ""))}</p>
<form method="post" action="/runtime/retry"><button type="submit">{h(t(lang, "retry"))}</button></form></div>"""
    downloaded = int(runtime.get("downloaded", 0) or 0)
    total = int(runtime.get("total", 0) or 0)
    percent = (downloaded / total * 100.0) if total else 0.0
    label = h(runtime.get("message", ""))
    if runtime.get("kind"):
        label += f' <span class="badge">{h(runtime["kind"])}</span>'
    bar = f'<div class="progress"><span style="width:{percent:.1f}%"></span></div>' if total else '<div class="progress indeterminate"><span></span></div>'
    size = f'<p class="help">{fmt_bytes(downloaded)} / {fmt_bytes(total)}</p>' if total else ""
    return f'<div class="card"><h2>{h(t(lang, "runtime_preparing"))}</h2><p>{label}</p>{bar}{size}</div>'


def _toggle_form(app: WebApp, network: NetworkSnapshot, lang: str) -> str:
    enabled = "0" if network.enabled else "1"
    label = t(lang, "deactivate") if network.enabled else t(lang, "activate")
    css = "danger" if network.enabled else ""
    ack = ""
    if not network.enabled and network.network == "mainnet":
        ack = f'<label class="checkline"><input type="checkbox" name="mainnet_ack" value="1"> {h(t(lang, "setup_mainnet_ack"))}</label>'
    return f"""
<form method="post" action="/networks/toggle" class="inline" onsubmit="return confirm({h(json.dumps(t(lang, "toggle_network_confirm")))})">
  <input type="hidden" name="network" value="{network.network}">
  <input type="hidden" name="enabled" value="{enabled}">
  {ack}
  <button type="submit" class="{css}">{h(label)}</button>
</form>"""


def _logs_block(network: NetworkSnapshot, lang: str) -> str:
    end = "Ir al final" if lang == "es" else "Go to end"
    result = '<section class="logs">'
    for name, label, lines in (
        ("elite-log", t(lang, "logs") + " · e-lite", network.logs),
        ("core-log", "Bitcoin Core", network.core_logs),
    ):
        text = "\n".join(lines) if lines else t(lang, "no_logs")
        result += f'<h3>{h(label)}</h3><button class="secondary log-end" type="button">{h(end)}</button><pre id="{name}" tabindex="0">{h(text)}</pre>'
    return result + '</section>'


def _network_card(app: WebApp, network: NetworkSnapshot, lang: str, domain: str) -> str:
    plain, tls_port = network.ports
    try:
        plain_lan = bool(app.settings().network(network.network).overrides.get("server.plain_lan", True))
    except Exception:
        plain_lan = True
    plain_label = str(plain) if plain_lan else f"{plain} ({'solo local' if lang == 'es' else 'localhost only'})"
    net_name = t(lang, f"network_{network.network}")
    ports = f'<div class="ports"><span class="port">TCP <code>{plain_label}</code></span><span class="port">TLS <code>{tls_port}</code></span></div>'
    if network.phase == "disabled":
        body = f"""
<div class="network-head">
  <div><h2>{h(net_name)}</h2>{ports}<p class="help">{h(t(lang, "network_inactive_help"))}</p></div>
  {_toggle_form(app, network, lang)}
</div>
<div class="inline"><span class="pill muted"><span class="dot"></span>{h(t(lang, "network_disabled"))}</span>
<a class="button secondary" href="/config?network={network.network}">{h(t(lang, "configure"))}</a></div>"""
        return f'<div class="card">{body}{_logs_block(network, lang)}</div>'

    status = status_class(network)
    state_text = state_label(lang, network.state) if network.phase == "live" else (
        t(lang, "starting") if network.phase == "starting" else t(lang, "not_ready")
    )
    if network.phase == "runtime":
        state_text = t(lang, "not_ready")
    live = network.live or {}
    tuning = live.get("tuning", {}) if isinstance(live.get("tuning"), dict) else {}
    memory = live.get("memory", {}) if isinstance(live.get("memory"), dict) else {}
    overall = network.core_progress if network.core_ibd else network.index_progress
    if network.fully_synced:
        overall = 100.0
    message = t(lang, "waiting_core_sync") if network.core_ibd else (
        t(lang, "fully_synced") if network.fully_synced else t(lang, "index_sync")
    )
    clearnet_tcp = domain
    onion_tcp = network.onion
    error = ""
    if network.phase in ("runtime_error", "process_error"):
        detail = network.runtime.get("error") or network.process.get("last_error") or ""
        error = f'<div class="card error"><p>{h(detail)}</p></div>'
    if network.tls.get("error"):
        error += f'<div class="card error"><p>{h(network.tls["error"])}</p></div>'
    onion_html = (
        f'<div class="endpoint"><code>{h(onion_tcp)}</code><button class="secondary" onclick="copyValue(this.dataset.value, this)" data-value="{h(onion_tcp)}">{h(t(lang, "copy"))}</button></div>'
        if onion_tcp else f'<p class="help">{h(t(lang, "onion_generating"))}</p>'
    )
    return f"""
<div class="card">
  {error}
  <div class="network-head">
    <div><h2>{h(net_name)}</h2>{ports}</div>
    <div class="inline">
      <span class="pill {status}"><span class="dot"></span>{h(state_text)}</span>
      {_toggle_form(app, network, lang)}
    </div>
  </div>
  <div class="inline" style="justify-content:space-between;margin-top:12px"><span>{h(message)}</span><strong>{overall:.1f}%</strong></div>
  <div class="progress"><span style="width:{overall:.1f}%"></span></div>
  <div class="grid" style="margin-top:12px">
    <div><div class="help">{h(t(lang, "core_sync"))} · {network.core_progress:.1f}%</div><div class="progress"><span style="width:{network.core_progress:.1f}%"></span></div><div class="help">{network.core_height:,} / {network.headers:,}</div></div>
    <div><div class="help">{h(t(lang, "index_sync"))} · {network.index_progress:.1f}%</div><div class="progress"><span style="width:{network.index_progress:.1f}%"></span></div><div class="help">{network.indexed:,} / {network.headers:,}</div></div>
  </div>
  <div class="grid" style="margin-top:12px">
    <div class="stat"><div class="k">{h(t(lang, "clearnet"))}</div>
      <div class="endpoint"><code>{h(clearnet_tcp)}</code><button class="secondary" onclick="copyValue(this.dataset.value, this)" data-value="{h(clearnet_tcp)}">{h(t(lang, "copy"))}</button></div>
      <div class="ports">Plain: <code>{plain_label}</code> TLS: <code>{tls_port}</code></div>
      <p class="help">{h(t(lang, "clearnet_help"))}</p>
    </div>
    <div class="stat"><div class="k">{h(t(lang, "tor"))}</div>{onion_html}<div class="ports">Plain: <code>{plain_label}</code> TLS: <code>{tls_port}</code></div><p class="help">{h(t(lang, "tor_help"))}</p></div>
  </div>
  <div class="grid three" style="margin-top:12px">
    <div class="stat"><div class="k">{h(t(lang, "height"))}</div><div class="v">{network.indexed:,}</div></div>
    <div class="stat"><div class="k">{h(t(lang, "lag"))}</div><div class="v">{max(0, network.core_height - network.indexed):,}</div></div>
    <div class="stat"><div class="k">{h(t(lang, "clients"))}</div><div class="v">{int(live.get("clients", 0) or 0)} / {int(live.get("max_clients", 0) or 0)}</div></div>
  </div>
  <div class="card" style="background:rgba(0,0,0,.12)">
    <div class="row"><span class="k">{h(t(lang, "uptime"))}</span><span class="v">{h(fmt_duration(int(live.get("uptime_seconds", 0) or 0)))}</span></div>
    <div class="row"><span class="k">{h(t(lang, "disk_free"))}</span><span class="v">{h(fmt_bytes(int(live.get("disk_free_bytes", 0) or 0)))}</span></div>
    <div class="row"><span class="k">{h(t(lang, "db_size"))}</span><span class="v">{h(fmt_bytes(int(live.get("db_bytes", 0) or 0)))}</span></div>
    <div class="row"><span class="k">{h(t(lang, "memory"))}</span><span class="v">{int(memory.get("available_mib", 0) or 0):,} MiB</span></div>
    <div class="row"><span class="k">{h(t(lang, "profile"))}</span><span class="v">{h(str(tuning.get("profile", "—")))}</span></div>
    {_detail_rows(network, lang)}
  </div>
  <div class="inline" style="margin-top:12px"><a class="button secondary" href="/config?network={network.network}">{h(t(lang, "configure"))}</a></div>
  {_logs_block(network, lang)}
</div>"""


def _detail_rows(network: NetworkSnapshot, lang: str) -> str:
    live = network.live or {}
    core = live.get("core") or {}
    tuning = live.get("tuning") or {}
    memory = live.get("memory") or {}
    disk = live.get("disk") or {}
    descriptors = live.get("file_descriptors") or {}
    tor = live.get("tor") or {}
    pipeline = live.get("pipeline") or {}
    rocksdb = live.get("rocksdb") or {}
    machine = live.get("machine") or {}
    cpu = machine.get("cpu") or {}
    machine_disk = (machine.get("disk") or {}).get("data") or {}
    machine_plan = machine.get("plan") or {}
    scheduler = live.get("scheduler") or {}
    missing = "No disponible" if lang == "es" else "Unavailable"
    def value(source: dict, key: str) -> object:
        result = source.get(key)
        if result is None:
            return missing
        if isinstance(result, bool):
            return ("Sí" if lang == "es" else "Yes") if result else "No"
        return result
    rows = [
        ("Altura Core / cabeceras", "Core height / headers", f"{network.core_height:,} / {network.headers:,}" if network.live else missing),
        ("Conexiones Core", "Core connections", value(core, "connections")),
        ("Descarga inicial Core", "Core initial download", value(core, "initial_block_download")),
        ("Poda activa", "Pruned", value(core, "pruned")),
        ("Altura de poda", "Prune height", value(core, "pruneheight")),
        ("Bloques/s", "Blocks/s", value(tuning, "blocks_per_sec")),
        ("Caché (MiB)", "Cache (MiB)", value(tuning, "cache_mib")),
        ("Límite peticiones/s (0 = desactivado)", "Rate limit/s (0 = disabled)", value(live, "rate_limit_rps")),
        ("Ráfaga", "Burst", value(live, "rate_burst")),
        ("Peticiones limitadas", "Rate limited requests", value(live, "rate_limited")),
        ("RPC Core en loopback", "Core loopback RPC", value(live, "core_rpc_loopback")),
        (
            "Memoria efectiva / reserva dinámica",
            "Effective memory / dynamic reserve",
            f"{int(memory['effective_mib']):,} / {int(memory['dynamic_reserve_mib']):,} MiB" if "effective_mib" in memory and "dynamic_reserve_mib" in memory else missing,
        ),
        (
            "Memtables / table readers reales",
            "Actual memtables / table readers",
            f"{int(memory['memtable_bytes_mib']):,} / {int(memory['table_readers_mib']):,} MiB" if "memtable_bytes_mib" in memory and "table_readers_mib" in memory else missing,
        ),
        (
            "Descriptores abiertos (efectivo / límite)",
            "Open files (effective / limit)",
            f"{descriptors['max_open_files_effective']} / {descriptors['soft_limit']}" if descriptors else missing,
        ),
        (
            "SOCKS de Tor",
            "Tor SOCKS",
            f"127.0.0.1:{tor['socks_port']}" if tor.get("managed") and tor.get("socks_port") else missing,
        ),
        (
            "ControlPort de Tor",
            "Tor ControlPort",
            ("No gestionado" if lang == "es" else "Not managed") if not tor.get("control_port") else tor["control_port"],
        ),
        (
            "Bloques del pipeline (preparados / aplicados / descartados)",
            "Pipeline blocks (prepared / applied / discarded)",
            f"{pipeline['prepared']:,} / {pipeline['applied']:,} / {pipeline['discarded']:,}" if pipeline else missing,
        ),
        (
            "Backlog sin indexar",
            "Unindexed backlog",
            fmt_bytes(int(scheduler["backlog_bytes"])) if scheduler.get("backlog_bytes") is not None else missing,
        ),
        (
            "Planificador Core / e-lite",
            "Core / e-lite scheduler",
            (
                f"{scheduler['mode']} · "
                + ("pausado" if scheduler.get("index_paused") else "activo")
                if lang == "es"
                else f"{scheduler['mode']} · " + ("paused" if scheduler.get("index_paused") else "active")
            ) if scheduler else missing,
        ),
        (
            "Núcleos físicos / lógicos",
            "Physical / logical cores",
            f"{cpu['physical']} / {cpu['logical']}" if cpu else missing,
        ),
        (
            "Disco de datos detectado",
            "Detected data disk",
            f"{machine_disk['kind']} ({machine_disk['mount']})" if machine_disk else missing,
        ),
        (
            "Plan de máquina (lectores / jobs / commit)",
            "Machine plan (readers / jobs / commit)",
            f"{machine_plan['readers']} / {machine_plan['jobs']} / {machine_plan['commit_ms']} ms" if machine_plan else missing,
        ),
        (
            "Compactaciones / ficheros L0",
            "Compactions / L0 files",
            f"{rocksdb['running_compactions']} / {rocksdb['level0_files']}" if "running_compactions" in rocksdb and "level0_files" in rocksdb else missing,
        ),
        (
            "Caché de bloques real",
            "Actual block cache",
            fmt_bytes(int(rocksdb["block_cache_bytes"])) if rocksdb.get("block_cache_bytes") is not None else missing,
        ),
        (
            "Compactación pendiente",
            "Pending compaction",
            fmt_bytes(int(rocksdb["pending_compaction_bytes"])) if rocksdb.get("pending_compaction_bytes") is not None else missing,
        ),
        ("Certificado TLS disponible", "TLS certificate available", value(network.tls, "certificate_available")),
        ("Dirección Tor disponible", "Tor address available", bool(network.onion)),
        ("Versiones e-lite / Core / Tor", "Versions e-lite / Core / Tor", f"{E_LITE_VERSION} / {BITCOIN_VERSION} / {TOR_VERSION}"),
    ]
    rawtx = live.get("rawtx") or {}
    server = live.get("server") or {}
    if server.get("banner"):
        rows.append(("Banner Electrum", "Electrum banner", server["banner"]))
    if "electrumx_compat" in server:
        rows.append((
            "Compatibilidad ElectrumX",
            "ElectrumX compatibility",
            ("Sí" if lang == "es" else "Yes") if server.get("electrumx_compat") else ("No" if lang == "es" else "No"),
        ))
    if server.get("batch_lines") is not None:
        rows.append((
            "Lotes JSON-RPC (líneas / items / máximo)",
            "JSON-RPC batches (lines / items / max)",
            f"{int(server.get('batch_lines') or 0)} / {int(server.get('batch_items') or 0)} / {int(server.get('batch_max_items') or 0)}",
        ))
    race = rawtx.get("race") or {}
    if isinstance(race, dict) and any(int(race.get(k) or 0) for k in ("attempts", "hedges", "wins_esplora", "wins_p2p")):
        rows.append((
            "Carrera de bloques (intentos / hedges / Esplora / P2P)",
            "Block race (attempts / hedges / Esplora / P2P)",
            f"{int(race.get('attempts') or 0):,} / {int(race.get('hedges') or 0):,} / {int(race.get('wins_esplora') or 0):,} / {int(race.get('wins_p2p') or 0):,}",
        ))
    if rawtx.get("decoys_pending") is not None:
        rows.append((
            "Decoys pendientes",
            "Pending decoys",
            f"{int(rawtx.get('decoys_pending') or 0):,}",
        ))
    if rawtx.get("cache_blocks") is not None or rawtx.get("cache_txs") is not None:
        rows.append((
            "Caché rawtx (bloques / tx / bytes)",
            "Rawtx cache (blocks / txs / bytes)",
            f"{int(rawtx.get('cache_blocks') or 0):,} / {int(rawtx.get('cache_txs') or 0):,} / {fmt_bytes(int(rawtx.get('cache_bytes') or 0))}",
        ))
    failures = rawtx.get("tx_get_failures") or {}
    if isinstance(failures, dict):
        active = {str(k): int(v) for k, v in failures.items() if isinstance(v, (int, float)) and v}
        if active:
            rows.append((
                "Fallos tx.get",
                "tx.get failures",
                ", ".join(f"{key} {value}" for key, value in active.items()),
            ))
    for key, es, en in (
        ("index_bytes", "Índice en disco", "Index on disk"),
        ("txids_bytes", "TXIDs en disco", "TXIDs on disk"),
        ("cache_bytes", "Caché en disco", "Cache on disk"),
        ("log_bytes", "Logs en disco", "Logs on disk"),
        ("snapshot_bytes", "Snapshots en disco", "Snapshots on disk"),
        ("total_bytes", "Total e-lite en disco", "Total e-lite on disk"),
    ):
        rows.append((es, en, fmt_bytes(disk[key]) if disk.get(key) is not None else missing))
    return "".join(f'<div class="row"><span class="k">{h(es if lang == "es" else en)}</span><span class="v">{h(val)}</span></div>' for es, en, val in rows)


def setup_page(
    app: WebApp,
    snapshot: AppSnapshot,
    lang: str,
    query: str,
    errors: list[str] | None = None,
    values: Settings | None = None,
) -> str:
    errors = errors or []
    selected = values or snapshot.settings
    if not any(selected.network(network).enabled for network in NETWORKS):
        selected = Settings(setup_complete=False)
        selected.network("signet").enabled = True
    error_html = ""
    if errors:
        error_html = '<div class="card error"><ul>' + "".join(f"<li>{h(_error_text(lang, e))}</li>" for e in errors) + "</ul></div>"
    cards = []
    for network in NETWORKS:
        cards.append(
            f"""<label class="network"><input type="checkbox" name="enabled_{network}" value="1" {"checked" if selected.network(network).enabled else ""}>
<strong>{h(t(lang, f"setup_{network}_title"))}</strong><span>{h(t(lang, f"setup_{network}_help"))}</span></label>"""
        )
    runtime = snapshot.runtime
    ready = str(runtime.get("phase")) == "ready" or snapshot.demo
    content = f"""
<div class="card">
  <h2>{h(t(lang, "setup_title"))}</h2>
  <p>{h(t(lang, "setup_intro"))}</p>
  <p class="help">{h(t(lang, "setup_ports"))}</p>
</div>
{error_html}
{_runtime_card(snapshot, lang)}
<form method="post" action="/setup?lang={lang}" class="card" onsubmit="return confirmSetup(this)">
  <h2>{h(t(lang, "networks"))}</h2>
  <div class="network-grid">{''.join(cards)}</div>
  <label class="checkline" id="mainnet-ack"><input type="checkbox" name="mainnet_ack" value="1"> {h(t(lang, "setup_mainnet_ack"))}</label>
  <div class="inline" style="margin-top:18px">
    <button type="submit">{h(t(lang, "save_setup"))}</button>
    <span class="help">{("" if ready else h(t(lang, "setup_runtime_wait")))}</span>
  </div>
</form>
<div class="card warning"><p><strong>{h(t(lang, "alpha_warning"))}</strong></p><p>{h(t(lang, "security_warning"))}</p></div>
<script>
function updateAck() {{
  const form = document.querySelector('form[action^="/setup"]');
  if (!form) return;
  const mainnet = form.querySelector('input[name="enabled_mainnet"]');
  const row = document.getElementById('mainnet-ack');
  if (row) row.style.display = mainnet && mainnet.checked ? 'flex' : 'none';
}}
document.querySelectorAll('input[name="enabled_mainnet"]').forEach(function(input) {{ input.addEventListener('change', updateAck); }});
updateAck();
function confirmSetup(form) {{
  const checked = form.querySelectorAll('input[name^="enabled_"]:checked');
  if (!checked.length) {{ alert({json.dumps(t(lang, "setup_network_required"))}); return false; }}
  if (form.querySelector('input[name="enabled_mainnet"]').checked) {{
    const ack = form.querySelector('input[name="mainnet_ack"]');
    if (!ack || !ack.checked) {{ alert({json.dumps(t(lang, "setup_mainnet_required"))}); return false; }}
    return confirm({json.dumps(t(lang, "setup_mainnet_help"))});
  }}
  return true;
}}
</script>
"""
    return page(t(lang, "setup"), content, lang, active="status", refresh=8 if not ready else 0)


def _error_text(lang: str, error: str) -> str:
    if error.startswith("mainnet_ack:"):
        return t(lang, "setup_mainnet_required")
    if error.startswith("networks:"):
        return t(lang, "setup_network_required")
    return error


def _zfs_arc_warning(lang: str) -> str:
    """Warn when the host uses ZFS but the container cannot manage the ARC cap."""
    if not Path("/proc/spl/kstat/zfs/arcstats").is_file():
        return ""
    try:
        if os.access("/sys/module/zfs/parameters/zfs_arc_max", os.W_OK):
            return ""
    except OSError:
        pass
    text = (
        "ZFS detectado y el contenedor no puede escribir zfs_arc_max. Limita el ARC "
        "en el host (por ejemplo, 25 % de la RAM) para que MemAvailable y la caché "
        "de e-lite no se confundan; e-lite reduce su caché si el ARC ocupa la memoria. "
        "`e-lite arc-setup` es para instalaciones nativas, no para Umbrel."
        if lang == "es" else
        "ZFS detected and the container cannot write zfs_arc_max. Cap the ARC on the "
        "host (for example, 25 % of RAM) so MemAvailable and the e-lite cache agree; "
        "e-lite shrinks its cache when the ARC holds the memory. `e-lite arc-setup` "
        "is for native installs, not Umbrel."
    )
    return f'<div class="card warning"><p>{h(text)}</p></div>'


def status_page(app: WebApp, snapshot: AppSnapshot, lang: str, query: str, error: str = "") -> str:
    notice = ""
    if error:
        notice = f'<div class="card error"><p>{h(error)}</p></div>'
    if "saved=1" in (query or ""):
        notice += f'<div class="card warning"><p>{h(t(lang, "saved"))}</p></div>'
    notice += _zfs_arc_warning(lang)
    runtime = _runtime_card(snapshot, lang)
    enabled = snapshot.enabled_networks
    if enabled:
        clients = sum(int((n.live or {}).get("clients", 0) or 0) for n in enabled)
        best_disk = min(int((n.live or {}).get("disk_free_bytes", 0) or 0) for n in enabled)
        summary = f"""
<div class="grid three">
  <div class="stat"><div class="k">{h(t(lang, "active_networks"))}</div><div class="v">{snapshot.active_count} / 3</div></div>
  <div class="stat"><div class="k">{h(t(lang, "clients"))}</div><div class="v">{clients}</div></div>
  <div class="stat"><div class="k">{h(t(lang, "disk_free"))}</div><div class="v">{h(fmt_bytes(best_disk))}</div></div>
</div>"""
    else:
        summary = f'<div class="card warning"><p>{h(t(lang, "no_networks"))}</p></div>'
    multi_warning = ""
    if snapshot.active_count > 1:
        multi_warning = (
            '<div style="margin-top:14px;padding:12px 14px;border:1px solid rgba(255,196,107,.35);'
            'border-radius:14px;background:rgba(255,196,107,.08);color:#ffe2b0">'
            f'{h(t(lang, "multi_network_performance"))}</div>'
        )
    selected = (parse_qs(query).get("network") or ["mainnet"])[0]
    if selected not in NETWORKS:
        selected = "mainnet"
    tabs = "".join(
        f'<a class="{"active" if n == selected else ""}" aria-current="{"page" if n == selected else "false"}" href="/?network={n}&lang={lang}">{h(t(lang, f"network_{n}"))}</a>'
        for n in NETWORKS
    )
    cards = _network_card(app, snapshot.networks[selected], lang, snapshot.domain)
    content = f"""
{notice}
<div class="card" id="network-summary"><div class="inline" style="justify-content:space-between">
  <h2 style="margin:0">{h(t(lang, "networks"))}</h2>
  <span class="badge">{h(t(lang, "version_label", version=E_LITE_VERSION, core=BITCOIN_VERSION, app=APP_VERSION))}</span>
</div>{summary}{multi_warning}</div>
{runtime}
<div class="card warning"><p><strong>{h(t(lang, "alpha_warning"))}</strong></p><p>{h(t(lang, "security_warning"))}</p></div>
<div class="tabbar" aria-label="{h(t(lang, 'networks'))}">{tabs}</div>
<div id="network-panel">{cards}</div>
<script>
function bindLogs() {{
  document.querySelectorAll('.logs pre').forEach(el => {{ el.scrollTop = el.scrollHeight; }});
  document.querySelectorAll('.log-end').forEach(button => {{
    button.onclick = () => {{ const el = button.nextElementSibling; el.scrollTop = el.scrollHeight; }};
  }});
}}
bindLogs();
let refreshing = false;
async function refreshPanel() {{
  if (refreshing) return;
  refreshing = true;
  try {{
    const response = await fetch(window.location.href, {{cache:'no-store'}});
    if (!response.ok) return;
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    const next = doc.getElementById('network-panel');
    const panel = document.getElementById('network-panel');
    if (!next || !panel || panel.querySelector('input[name="mainnet_ack"]:checked') || panel.contains(document.activeElement) && document.activeElement.matches('input,select,textarea')) return;
    const summary = document.getElementById('network-summary');
    const nextSummary = doc.getElementById('network-summary');
    if (summary && nextSummary) summary.replaceChildren(...nextSummary.childNodes);
    const positions = {{}};
    panel.querySelectorAll('.logs pre').forEach(el => {{
      positions[el.id] = {{top:el.scrollTop, follow:el.scrollHeight-el.clientHeight-el.scrollTop < 24}};
      const replacement = next.querySelector('#' + el.id);
      // Keep historical lines and the user's scroll anchor while reading backwards.
      if (replacement && !positions[el.id].follow) replacement.textContent = el.textContent;
    }});
    panel.replaceChildren(...next.childNodes);
    bindLogs();
    Object.entries(positions).forEach(([id, pos]) => {{
      const el = document.getElementById(id);
      if (el && !pos.follow) el.scrollTop = pos.top;
    }});
  }} catch (error) {{ /* Retain the last visible state on temporary network errors. */ }}
  finally {{ refreshing = false; }}
}}
setInterval(refreshPanel, 10000);
</script>
"""
    return page(t(lang, "status_page"), content, lang, active="status")


PHASE_LABELS = {
    "preparing": ("Preparando", "Preparing"),
    "measuring": ("Midiendo", "Measuring"),
    "stopping": ("Parando", "Stopping"),
    "packing-core": ("Core", "Core"),
    "preparing-index": ("Preparando índice", "Index prep"),
    "exporting-index": ("Índice", "Index"),
    "extracting-core": ("Restaurando Core", "Core restore"),
    "extracting-index": ("Restaurando índice", "Index restore"),
    "verifying-index": ("Verificando", "Verifying"),
    "finalizing": ("Validando", "Validating"),
    "starting": ("Reiniciando", "Restarting"),
    "done": ("Listo", "Done"),
    "error": ("Error", "Error"),
    "cancelled": ("Cancelado", "Cancelled"),
}


def _phase_label(lang: str, phase: str) -> str:
    es, en = PHASE_LABELS.get(phase, (phase, phase))
    return es if lang == "es" else en


def backup_page(app: WebApp, snapshot: AppSnapshot, lang: str, query: str) -> str:
    L = lambda es, en: es if lang == "es" else en
    params = parse_qs(query)
    network = (params.get("network") or [""])[0]
    if network not in NETWORKS:
        network = next((n for n in NETWORKS if snapshot.networks[n].enabled), "mainnet")
    action = (params.get("action") or ["export"])[0]
    error = (params.get("error") or [""])[0]
    destinations = app.backup.destinations() if app.backup else []
    default_destination = str(app.backup.default_destination()) if app.backup else ""
    selected = (params.get("destination") or [default_destination])[0]
    if selected not in [d["path"] for d in destinations]:
        selected = default_destination
    files = app.backup.files(selected) if app.backup else []
    network_files = [f for f in files if f.get("network") == network]
    job = app.backup.status() if app.backup else {"active": False, "phase": "idle"}
    leftovers = app.backup.leftovers(network) if app.backup else []
    sizes = app.backup.sizes(network) if app.backup else {"index": 0, "core": 0, "computing": False, "ready": False}
    index_bytes, core_bytes = int(sizes.get("index") or 0), int(sizes.get("core") or 0)
    estimate = int(index_bytes + core_bytes)
    sizes_text = (
        L("calculando…", "calculating…")
        if sizes.get("computing") and not (index_bytes or core_bytes)
        else f"{L('índice', 'index')} {fmt_bytes(index_bytes)} · Core {fmt_bytes(core_bytes)} · {L('estimado', 'estimated')} ~{fmt_bytes(estimate)}"
    )
    busy_hint = (
        f'<p class="help">{h(L("Hay una copia en curso para la red " + job.get("network", ""), "A backup is running for network " + job.get("network", "")) + ".</p>")}'
        if job.get("active") and job.get("network") and job.get("network") != network else ""
    )
    notice = f'<div class="card error"><p>{h(error)}</p></div>' if error else ""
    recovery = app.backup.recovery_notice() if app.backup else None
    if recovery and not job.get("active"):
        name = recovery.get("network", "")
        if recovery.get("recovered"):
            text = L(
                f"Importación interrumpida en {name}: se han recuperado los datos anteriores.",
                f"Interrupted import on {name}: previous data recovered.",
            )
        else:
            text = L(
                f"Importación interrumpida en {name}: se ha limpiado la restauración incompleta.",
                f"Interrupted import on {name}: incomplete restore cleaned up.",
            )
        notice += f'<div class="card warning"><p>{h(text)}</p></div>'
    tabs = "".join(
        f'<a class="{"active" if n == network else ""}" href="/backup?network={n}&lang={lang}">{h(t(lang, f"network_{n}"))}</a>'
        for n in NETWORKS
    )
    options = "".join(
        f'<option value="{h(d["path"])}" {"selected" if d["path"] == selected else ""} {"disabled" if not d["writable"] else ""}>'
        f'{h(d["path"])} · {h(fmt_bytes(d["free"]))} libres{"" if d["writable"] else " · no escribible"}'
        f'{" · mismo disco" if d.get("same_device") else ""}</option>'
        for d in destinations
    )
    if not destinations:
        options = f'<option value="">{h(L("sin destino disponible", "no destination available"))}</option>'
    has_extra = any(d["path"] == "/exports" for d in destinations)
    destination_hint = "" if has_extra else (
        f'<p class="help">{h(L("Para escribir directamente en un disco externo, concede una carpeta a la app en Ajustes de la app → Almacenamiento. Mientras tanto se usa data/exports; podrás moverlo desde Files → Apps → e-lite.", "To write directly to an external drive, grant the app a folder in App Settings → Storage. Until then data/exports is used; move it from Files → Apps → e-lite."))}</p>'
    )
    file_options = "".join(
        f'<option value="{h(f["name"])}" data-has-core="{str(f.get("has_core")).lower()}" data-kind="{h(f.get("kind", "snapshot"))}">'
        f'[{"Bundle" if f.get("kind") == "bundle" else "Snapshot"}] {h(f["name"])} · {f["height"]:,} · {fmt_bytes(f["bytes"])} · {h(f["network"])}</option>'
        for f in network_files
    ) or f'<option value="">{h(L("no hay copias para esta red", "no backups for this network"))}</option>'
    no_core_hint = (
        f'<p class="help" id="no-core-hint" style="display:none">{h(L("Esta copia no incluye Core. Tras restaurarla, el índice no debe superar la punta de Core.", "This backup does not include Core. After restoring it, the index must not be ahead of Core tip."))}</p>'
    )
    cleanup_card = ""
    if leftovers and not job.get("active"):
        cleanup_card = f"""<div class="card">
  <h2>{h(L("Datos previos sin borrar", "Previous data not removed"))}</h2>
  <p class="help">{h(L("Quedan directorios .pre-import de una importación anterior. Solo bórralos si la red funciona bien.", "There are .pre-import directories from a previous import. Only delete them if the network works fine."))}</p>
  <ul>{"".join(f"<li><code>{h(x['name'])}</code></li>" for x in leftovers)}</ul>
  <form method="post" action="/backup/cleanup" onsubmit="return confirm({h(json.dumps(L('Se borrarán permanentemente. ¿Continuar?', 'They will be deleted permanently. Continue?')))})">
    <input type="hidden" name="network" value="{h(network)}">
    <p><input type="text" name="confirm" placeholder="{h(network)}" autocomplete="off"></p>
    <button type="submit" class="danger">{h(L("Eliminar datos previos", "Delete previous data"))}</button>
  </form>
</div>"""
    progress = ""
    if job.get("active") or job.get("phase") in ("done", "error"):
        done = int(job.get("done") or 0)
        total = int(job.get("total") or 0)
        phase_done = int(job.get("phase_done") or 0)
        phase_total = int(job.get("phase_total") or 0)
        pct = min(100.0, phase_done / phase_total * 100.0) if phase_total else 0.0
        eta = int(job.get("phase_eta_seconds") or 0)
        detail = f"{fmt_bytes(phase_done)} / {fmt_bytes(phase_total)}" if phase_total else ""
        if eta and job.get("active"):
            detail += f" · ~{fmt_duration(eta)}"
        global_pct = min(100.0, done / total * 100.0) if total else 0.0
        global_detail = f"{fmt_bytes(done)} / {fmt_bytes(total)} · {global_pct:.1f}%" if total else ""
        phases = "".join(
            f'<span class="badge">✓ {h(_phase_label(lang, str(x.get("phase", ""))))} {x.get("seconds", 0):g}s</span>'
            for x in reversed(list(job.get("phases") or []))
        )
        elapsed = int(max(0, time.time() - float(job.get("phase_started_at") or time.time()))) if job.get("active") else 0
        elapsed_text = f"{'lleva' if lang == 'es' else 'elapsed'} {fmt_duration(elapsed)}" if job.get("active") else ""
        warnings = "".join(f'<p class="help">{h(w)}</p>' for w in list(job.get("warnings") or []))
        digest = str(job.get("digest") or "")
        digest_line = (
            f'<p class="help">{h(L("Digest del índice", "Index digest"))}: <code title="{h(digest)}" style="word-break:break-all">{h(digest)}</code></p>'
            if digest else ""
        )
        cancel = ""
        if job.get("active"):
            cancel = f"""<form method="post" action="/backup/cancel" style="margin-top:10px">
  <input type="hidden" name="network" value="{h(network)}">
  <button type="submit" class="danger">{h(L("Cancelar", "Cancel"))}</button>
</form>"""
        progress = f"""<div class="card" id="backup-progress">
  <h2>{h(L("Progreso", "Progress"))}</h2>
  <div class="inline" style="justify-content:space-between"><span id="backup-phase"><span id="backup-phase-text">{h(job.get("message", ""))}</span> <span class="badge" id="backup-elapsed">{h(elapsed_text)}</span></span><strong id="backup-percent">{pct:.1f}%</strong></div>
  <div class="progress"><span id="backup-bar" style="width:{pct:.1f}%"></span></div>
  <p class="help" id="backup-detail">{h(detail)}</p>
  <p class="help" id="backup-global">{h(L("Total", "Overall"))}: {h(global_detail)}</p>
  <div class="inline" id="backup-phases">{phases}</div>
  <pre id="backup-tail">{h(chr(10).join(list(job.get("tail") or [])[-5:]))}</pre>
  {warnings}
  {digest_line}
  <p class="error" id="backup-error">{h(job.get("error", ""))}</p>
  <p class="help"><a href="/backup/log?lang={lang}" target="_blank">{h(L("Ver log completo", "View full log"))}</a></p>
  {cancel}
</div>"""
    content = f"""
{notice}
<div class="card"><div class="tabbar">{tabs}</div>
  {busy_hint}
  <p class="help">{h(L("Las copias incluyen el índice de e-lite y los datos de cadena de Bitcoin Core (blocks, chainstate, indexes) en un solo conjunto verificado. Las wallets y la configuración de Core no se copian: guárdalas aparte. La red se detiene durante el proceso y se reactiva al terminar si estaba activa.", "Backups include the e-lite index and Bitcoin Core chain data (blocks, chainstate, indexes) in one verified set. Core wallets and configuration are not copied: keep them separately. The network stops during the process and is restarted afterwards if it was active."))}</p>
  <p class="help">{h(L("Tamaños actuales:", "Current sizes:"))} <span id="backup-sizes">{h(sizes_text)}</span></p>
</div>
<div class="card">
  <h2>{h(L("Destino", "Destination"))}</h2>
  <select id="backup-dest" name="destination" style="max-width:100%">{options}</select>
  {destination_hint}
</div>
<div class="card">
  <h2>{h(L("Exportar", "Export"))}</h2>
  <form method="post" action="/backup/export">
    <input type="hidden" name="network" value="{h(network)}">
    <input type="hidden" name="destination" class="dest-mirror" value="{h(selected)}">
    <p><label>{h(L("Compresión del índice", "Index compression"))}</label><select name="compress">
      <option value="stored" selected>{h(L("sin comprimir, la más rápida (recomendada)", "stored, fastest (recommended)"))}</option>
      <option value="3">gzip 3 · {h(L("más lenta, reduce poco", "slower, little gain"))}</option>
      <option value="6">gzip 6 · {h(L("la más lenta", "slowest"))}</option>
    </select></p>
    <p class="help">{h(L("El índice y el Core se guardan por separado; sin comprimir la copia ocupa casi el tamaño del índice, pero termina antes. La compresión gzip apenas reduce un índice ya comprimido.", "The index and Core are stored separately; uncompressed the backup is almost the index size but finishes sooner. gzip barely shrinks an already-compressed index."))}</p>
    <button type="submit" {"disabled" if job.get("active") or not destinations else ""}>{h(L("Exportar red", "Export network"))}</button>
  </form>
</div>
{progress if job.get("action") == "export" else ""}
<div class="card">
  <h2>{h(L("Importar", "Import"))}</h2>
  <form method="post" action="/backup/import" onsubmit="return confirm({h(json.dumps(L('Se reemplazarán los datos de esta red. ¿Continuar?', 'This network data will be replaced. Continue?')))})">
    <input type="hidden" name="network" value="{h(network)}">
    <input type="hidden" name="destination" class="dest-mirror" value="{h(selected)}">
    <p><label>{h(L("Copia", "Backup"))}</label><select name="filename">{file_options}</select></p>
    {no_core_hint}
    <p id="verify-row"><label>{h(L("Verificación", "Verification"))}</label><select name="verify"><option value="basic" selected>basic</option><option value="full">full (verifychain)</option></select></p>
    <p id="digest-row"><label>{h(L("Digest esperado (opcional)", "Expected digest (optional)"))}</label><input type="text" name="expect_digest" maxlength="64" pattern="[0-9a-fA-F]{64}" placeholder="{h(L("64 caracteres hexadecimales", "64 hex characters"))}" autocomplete="off"></p>
    <label class="checkline"><input type="checkbox" name="delete_previous" value="1"> {h(L("Borrar los datos anteriores cuando termine", "Delete previous data when finished"))}</label>
    <p><label>{h(L("Escribe el nombre de la red para confirmar", "Type the network name to confirm"))}</label><input type="text" name="confirm" placeholder="{h(network)}" autocomplete="off"></p>
    <button type="submit" class="danger" {"disabled" if job.get("active") or not network_files else ""}>{h(L("Importar red", "Import network"))}</button>
  </form>
</div>
{progress if job.get("action") == "import" else ""}
{cleanup_card}
<script>
function fmtBytes(n) {{
  const units = ['B','KiB','MiB','GiB','TiB']; let v = Number(n)||0; let i = 0;
  while (v >= 1024 && i < units.length-1) {{ v /= 1024; i++; }}
  return v.toFixed(i ? 1 : 0) + ' ' + units[i];
}}
function fmtTime(s) {{
  s = Math.max(0, Math.round(Number(s)||0));
  const d = Math.floor(s/86400), h = Math.floor((s%86400)/3600), m = Math.floor((s%3600)/60);
  if (d) return d + 'd ' + h + 'h'; if (h) return h + 'h ' + m + 'm'; if (m) return m + 'm'; return s + 's';
}}
document.querySelectorAll('.dest-mirror').forEach(el => {{ el.value = document.getElementById('backup-dest') ? document.getElementById('backup-dest').value : ''; }});
const destSelect = document.getElementById('backup-dest');
if (destSelect) destSelect.addEventListener('change', () => document.querySelectorAll('.dest-mirror').forEach(el => el.value = destSelect.value));
const fileSelect = document.querySelector('select[name="filename"]');
const coreHint = document.getElementById('no-core-hint');
const verifyRow = document.getElementById('verify-row');
const digestRow = document.getElementById('digest-row');
if (fileSelect && coreHint) fileSelect.addEventListener('change', () => {{
  const option = fileSelect.options[fileSelect.selectedIndex];
  coreHint.style.display = option && option.dataset.hasCore === 'false' ? 'block' : 'none';
  const isBundle = option && option.dataset.kind === 'bundle';
  if (verifyRow) verifyRow.style.display = isBundle ? 'none' : 'block';
  if (digestRow) digestRow.style.display = isBundle ? 'none' : 'block';
}});
fileSelect && fileSelect.dispatchEvent(new Event('change'));
const PHASE_LABELS = {json.dumps({k: (v[0] if lang == "es" else v[1]) for k, v in PHASE_LABELS.items()}, ensure_ascii=False)};
const ELAPSED_LABEL = {json.dumps("lleva" if lang == "es" else "elapsed")};
const PENDING_DETAIL = {json.dumps("tamaño y tiempo estimados cuando empiece la escritura" if lang == "es" else "size and time estimated once writing starts")};
let wasActive = {str(bool(job.get("active"))).lower()};
let sizesComputing = {str(bool(sizes.get("computing"))).lower()};
async function pollSizes() {{
  if (!sizesComputing) return;
  try {{
    const response = await fetch('/api/backup/sizes?network={h(network)}', {{cache:'no-store'}});
    const s = await response.json();
    if (s.computing) {{ setTimeout(pollSizes, 3000); return; }}
    sizesComputing = false;
    const estimate = Math.round(s.index + s.core);
    const label = '{h(L("índice", "index"))} ' + fmtBytes(s.index) + ' · Core ' + fmtBytes(s.core) + ' · {h(L("estimado", "estimated"))} ~' + fmtBytes(estimate);
    const node = document.getElementById('backup-sizes');
    if (node) node.textContent = label;
  }} catch (e) {{ setTimeout(pollSizes, 4000); }}
}}
pollSizes();
async function pollBackup() {{
  if (!wasActive) return;
  try {{
    const response = await fetch('/api/backup/status', {{cache:'no-store'}});
    const s = await response.json();
    const bar = document.getElementById('backup-bar');
    if (!s.phase_total) {{
      bar.classList.add('indeterminate'); bar.style.width = '34%';
      document.getElementById('backup-percent').textContent = '';
      document.getElementById('backup-detail').textContent = PENDING_DETAIL;
    }} else {{
      bar.classList.remove('indeterminate');
      const pct = Math.min(100, s.phase_percent || 0);
      bar.style.width = pct.toFixed(1) + '%';
      document.getElementById('backup-percent').textContent = pct.toFixed(1) + '%';
      document.getElementById('backup-detail').textContent = fmtBytes(s.phase_done) + ' / ' + fmtBytes(s.phase_total) + (s.phase_eta_seconds ? ' · ~' + fmtTime(s.phase_eta_seconds) : '');
    }}
    const globalNode = document.getElementById('backup-global');
    if (globalNode) globalNode.textContent = 'Total: ' + fmtBytes(s.done) + ' / ' + fmtBytes(s.total) + (s.total ? ' · ' + (100 * s.done / s.total).toFixed(1) + '%' : '');
    const phaseText = document.getElementById('backup-phase-text');
    if (phaseText) phaseText.textContent = s.message || s.phase || '';
    document.getElementById('backup-phases').innerHTML = (s.phases || []).slice().reverse().map(x => '<span class="badge">✓ ' + (PHASE_LABELS[x.phase] || x.phase) + ' ' + x.seconds + 's</span>').join(' ');
    const elapsedNode = document.getElementById('backup-elapsed');
    if (elapsedNode) {{
      elapsedNode.textContent = s.active && s.phase_started_at ? ELAPSED_LABEL + ' ' + fmtTime(Date.now() / 1000 - s.phase_started_at) : '';
    }}
    document.getElementById('backup-tail').textContent = (s.tail || []).slice(-5).join(String.fromCharCode(10));
    document.getElementById('backup-error').textContent = s.error || '';
    if (s.active) {{ setTimeout(pollBackup, 1000); }} else {{ wasActive = false; setTimeout(() => location.reload(), 1200); }}
  }} catch (e) {{ setTimeout(pollBackup, 2000); }}
}}
pollBackup();
</script>
"""
    return page(t(lang, "backup_title"), content, lang, active="backup")


def config_page(
    app: WebApp,
    snapshot: AppSnapshot,
    lang: str,
    query: str,
    errors: list[str] | None = None,
    values: Settings | None = None,
    selected: str | None = None,
) -> str:
    settings = values or app.settings()
    errors = errors or []
    params = parse_qs(query)
    selected = selected or (params.get("network") or [""])[0]
    if selected not in NETWORKS:
        selected = next((n for n in NETWORKS if settings.network(n).enabled), "signet")
    error_html = ""
    if errors:
        error_html = '<div class="card error"><ul>' + "".join(f"<li>{h(_error_text(lang, e))}</li>" for e in errors) + "</ul></div>"
    notice = ""
    if "saved=1" in (query or ""):
        notice = f'<div class="card warning"><p>{h(t(lang, "saved"))}</p></div>'
    tr = lambda es, en: es if lang == "es" else en
    cache_notice = (params.get("cache") or [""])[0]
    cache_error_query = (params.get("cache_error") or [""])[0]
    if cache_error_query:
        notice += f'<div class="card error"><p>{h(cache_error_query)}</p></div>'
    elif cache_notice:
        notice += f'<div class="card warning"><p>{h(cache_notice)}</p></div>'
    cache: dict | None = None
    cache_failure = ""
    if app.demo:
        cache_failure = tr("No disponible en modo demostración.", "Not available in demo mode.")
    else:
        try:
            cache = cache_status(app.paths, selected)
        except CacheError as exc:
            cache_failure = str(exc)
    if cache:
        blocks = cache.get("blocks") or {}
        txs = cache.get("txs") or {}
        total = cache.get("total") or {}
        rows = [
            (tr("Directorio", "Directory"), str(cache.get("path") or "—")),
            (
                tr("Bloques (ficheros / tamaño / límite)", "Blocks (files / size / limit)"),
                f"{int(blocks.get('files') or 0):,} / {fmt_bytes(int(blocks.get('bytes') or 0))} / {fmt_bytes(int(blocks.get('limit_bytes') or 0))}",
            ),
            (
                tr("Transacciones (ficheros / tamaño / límite)", "Transactions (files / size / limit)"),
                f"{int(txs.get('files') or 0):,} / {fmt_bytes(int(txs.get('bytes') or 0))} / {fmt_bytes(int(txs.get('limit_bytes') or 0))}",
            ),
            (
                tr("Total (ficheros / tamaño / capacidad)", "Total (files / size / capacity)"),
                f"{int(total.get('files') or 0):,} / {fmt_bytes(int(total.get('bytes') or 0))} / {fmt_bytes(int(cache.get('capacity_bytes') or 0))}",
            ),
            (
                tr("Espacio libre en ese sistema de ficheros", "Free space on that filesystem"),
                fmt_bytes(int(cache["free_bytes"])) if isinstance(cache.get("free_bytes"), (int, float)) else "—",
            ),
        ]
        if int(cache.get("skipped") or 0):
            rows.append((tr("Entradas ignoradas (nunca se tocan)", "Skipped entries (never touched)"), f"{int(cache.get('skipped') or 0):,}"))
        rows_html = "".join(
            f'<div class="row"><span class="k">{h(es)}</span><span class="v">{h(value)}</span></div>'
            for es, value in rows
        )
        refused = str(cache.get("refused") or "")
        enabled = bool(cache.get("enabled", True))
        if refused:
            state = f'<p class="error">{h(tr("Ruta rechazada: ", "Path refused: ") + refused)}</p>'
        elif not enabled:
            state = f'<p class="help">{h(tr("Caché desactivada (rawtx.cache_mib = 0).", "Cache disabled (rawtx.cache_mib = 0)."))}</p>'
        else:
            state = f"""
<form method="post" action="/cache/clear" onsubmit="return confirm({h(json.dumps(tr('Se vaciará la caché de esta red. No toca índice, Core, TLS ni onion. ¿Continuar?', 'This network cache will be cleared. It never touches the index, Core, TLS or onion. Continue?')))})">
  <input type="hidden" name="network" value="{h(selected)}">
  <label class="checkline"><input type="checkbox" name="restart" value="1"> {h(tr("Reiniciar la red al terminar (libera la caché en RAM)", "Restart the network afterwards (releases the in-memory cache)"))}</label>
  <p><input type="text" name="confirm" placeholder="{h(selected)}" autocomplete="off"></p>
  <button type="submit" class="danger">{h(tr("Vaciar caché", "Clear cache"))}</button>
</form>"""
        cache_card = f"""<div class="card">
  <h2>{h(tr("Caché rawtx", "Rawtx cache"))}</h2>
  <p class="help">{h(tr("Solo se borran los ficheros de caché (bloques de reparación y transacciones verificadas). e-lite rechaza cualquier ruta que toque el índice, Core, TLS o Tor.", "Only cache files are deleted (repair blocks and verified transactions). e-lite refuses any path that touches the index, Core, TLS or Tor."))}</p>
  <div>{rows_html}</div>
  {state}
  <p class="help">{h(tr("La caché en RAM no se libera al borrar los ficheros: reinicia la red para medir una primera carga en frío.", "The in-memory cache is not released by deleting files: restart the network to measure a cold first load."))}</p>
</div>"""
    else:
        cache_card = f"""<div class="card">
  <h2>{h(tr("Caché rawtx", "Rawtx cache"))}</h2>
  <p class="error">{h(cache_failure or tr("No disponible.", "Unavailable."))}</p>
  <p class="help">{h(tr("La caché se puede inspeccionar y vaciar cuando e-lite esté instalado y la red tenga configuración.", "The cache can be inspected and cleared once e-lite is installed and the network has a configuration."))}</p>
</div>"""

    tabs = "".join(
        f'<a class="{"active" if n == selected else ""}" href="/config?network={n}&lang={lang}">{h(t(lang, f"network_{n}"))}</a>'
        for n in NETWORKS
    )
    config = settings.network(selected)
    groups: dict[str, list[str]] = {}
    for field_def in FIELDS:
        group = "advanced" if field_def.advanced else field_def.group
        groups.setdefault(group, []).append(field_def.key)
    sections = []
    for group in ("performance", "server", "pruning", "core"):
        if group not in groups:
            continue
        rows = "".join(_field_row(lang, config, key) for key in groups[group])
        sections.append(f'<div class="card"><h2>{h(t(lang, "group_" + group))}</h2>{rows}</div>')
    advanced_rows = "".join(_field_row(lang, config, key) for key in groups.get("advanced", []))
    advanced = f'<div class="card"><details><summary>{h(t(lang, "group_advanced"))}</summary>{advanced_rows}</details></div>' if advanced_rows else ""

    content = f"""
{notice}
{error_html}
<div class="card">
  <div class="tabbar">{tabs}</div>
  <p class="help">{h(t(lang, "config_help"))}</p>
</div>
<form method="post" action="/config?lang={lang}" onsubmit="return confirmSave(this)" data-network="{h(selected)}">
  <input type="hidden" name="network" value="{h(selected)}">
  <h2>{h(t(lang, f"network_{selected}"))}</h2>
  <div class="card">
    <h2>{h(t(lang, "tls_public_title"))}</h2>
    <p class="help">{h(t(lang, "tls_public_help"))}</p>
    <textarea name="public_hostnames" rows="3" placeholder="{h(t(lang, "tls_public_placeholder"))}">{h(chr(10).join(config.public_hostnames))}</textarea>
  </div>
  {''.join(sections)}
  {advanced}
  <div class="card">
    <p class="help">{h(t(lang, "restart_notice"))}</p>
    <div class="inline">
      <button type="submit">{h(t(lang, "save"))}</button>
      <button type="button" class="secondary" onclick="resetDefaults(this.form)">{h(t(lang, "reset_defaults"))}</button>
    </div>
  </div>
</form>
{cache_card}
<script>
function toggleDefault(box) {{
  const target = document.getElementById('value-' + box.dataset.key);
  if (target) target.disabled = box.checked;
}}
function resetDefaults(form) {{
  form.querySelectorAll('input[type=checkbox][data-key]').forEach(function(box) {{
    box.checked = true;
    toggleDefault(box);
  }});
}}
function confirmSave(form) {{
  return confirm({json.dumps(t(lang, "restart_notice"))});
}}
</script>
"""
    return page(t(lang, "settings_page"), content, lang, active="config")


def _field_row(lang: str, config: NetworkConfig, key: str) -> str:
    field_def = FIELDS_BY_KEY[key]
    is_default = config.is_default(key)
    value = config.value(key)
    disabled = " disabled" if is_default else ""
    control_id = "value-" + key
    if field_def.kind == "bool":
        options = "".join(
            f'<option value="{option}" {"selected" if (str(value).lower() == option) else ""}>{option}</option>'
            for option in ("true", "false")
        )
        control = f'<select id="{control_id}" name="value__{key}"{disabled}>{options}</select>'
    elif field_def.kind == "enum":
        options = "".join(
            f'<option value="{option}" {"selected" if str(value) == option else ""}>{option}</option>'
            for option in field_def.options
        )
        control = f'<select id="{control_id}" name="value__{key}"{disabled}>{options}</select>'
    elif field_def.kind == "list":
        control = f'<textarea id="{control_id}" name="value__{key}" rows="3" placeholder="{h(default_label(field_def))}"{disabled}>{h(format_value(field_def, value))}</textarea>'
    else:
        step = "0.01" if field_def.kind == "float" else "1"
        control = f'<input id="{control_id}" type="number" step="{step}" name="value__{key}" value="{h(format_value(field_def, value))}"{disabled}>'
    default_text = default_label(field_def)
    default_hint = f'<code>{h(default_text)}</code>' if default_text else ""
    return f"""
<div class="field">
  <div class="label"><label for="default-{key}">{h(field_label(lang, key))}</label><code>{h(key)}</code></div>
  <div>
    <label class="checkline"><input id="default-{key}" type="checkbox" name="default__{key}" value="1" data-key="{h(key)}" {"checked" if is_default else ""} onchange="toggleDefault(this)"> {h(t(lang, "use_default"))} {default_hint}</label>
    {control}
    <p class="help">{h(field_help(lang, key))}</p>
  </div>
</div>"""
