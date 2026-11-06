"""Small standard-library web UI for the e-lite Umbrel app."""

from __future__ import annotations

import html
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .config_writer import ELECTRUM_PORT, ELECTRUM_TLS_PORT, AppPaths
from .i18n import field_help, field_label, select_language, t
from .runtime import BITCOIN_VERSION, E_LITE_VERSION, RuntimeManager
from .settings import (
    FIELDS,
    FIELDS_BY_KEY,
    NETWORKS,
    Settings,
    default_label,
    format_value,
    load_settings,
    parse_form,
    parse_setup_form,
    save_settings,
)
from .supervisor import Supervisor
from .tls import TLSManager

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
    "memory": {"available_mib": 9300},
    "core": {"connections": 9, "initial_block_download": False, "pruned": True, "pruneheight": 176000},
    "listen": f"0.0.0.0:{ELECTRUM_PORT}",
    "tls_listen": f"0.0.0.0:{ELECTRUM_TLS_PORT}",
}

DEMO_ONION = "elite" + "a" * 51 + ".onion"
DEMO_LOGS = [
    "[e-lite] 0.1.0-alpha.25.1 · signet · BUILDING_INDEX",
    "[e-lite] Electrum TCP listening on 0.0.0.0:55011",
    "[e-lite] Electrum TLS listening on 0.0.0.0:55022",
    "[e-lite] tuning profile=ssd readers=8 commit=2000ms batch=256",
    "[e-lite] height 184250/190430 · lag 6180 · 418 blocks/s",
]
DEMO_CORE_LOGS = [
    "Bitcoin Core version v32.1.0",
    "Signet block height 190430, 9 connections",
    "Prune: manual, pruneheight 176000",
]


@dataclass
class Snapshot:
    setup_complete: bool
    network: str
    runtime: dict[str, Any]
    process: dict[str, Any]
    live: dict[str, Any] | None
    onion: str
    logs: list[str]
    core_logs: list[str]
    tls: dict[str, Any]
    domain: str
    demo: bool = False

    @property
    def phase(self) -> str:
        if not self.setup_complete:
            return "setup"
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
        if self.live is not None:
            return str(self.live.get("state", "UNKNOWN"))
        if self.phase == "runtime_error":
            return "ERROR"
        if self.phase == "process_error":
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


class WebApp:
    def __init__(
        self,
        paths: AppPaths,
        runtime: RuntimeManager,
        supervisor: Supervisor,
        tls: TLSManager,
        domain: str,
        hostname: str,
        demo: bool = False,
    ) -> None:
        self.paths = paths
        self.runtime = runtime
        self.supervisor = supervisor
        self.tls = tls
        self.domain = domain
        self.hostname = hostname
        self.demo = demo
        self.started = time.time()

    def settings(self) -> Settings:
        return load_settings(self.paths.settings_file)

    def snapshot(self) -> Snapshot:
        settings = self.settings()
        live = DEMO_STATUS if self.demo else self._live_status()
        network = settings.network or (live or {}).get("network", "")
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
            }
        return Snapshot(
            setup_complete=True if self.demo else settings.setup_complete,
            network=str(network or "signet"),
            runtime=runtime,
            process=self.supervisor.status(),
            live=live,
            onion=DEMO_ONION if self.demo else self._onion(),
            logs=DEMO_LOGS if self.demo else self.supervisor.tail_log(settings.network),
            core_logs=DEMO_CORE_LOGS if self.demo else self.supervisor.core_log_tail(settings.network),
            tls=self.tls.status(),
            domain=self.domain,
            demo=self.demo,
        )

    def _live_status(self) -> dict[str, Any] | None:
        try:
            with urllib.request.urlopen(
                urllib.request.Request("http://127.0.0.1:9184/status", headers={"User-Agent": "e-lite-web"}),
                timeout=2,
            ) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _onion(self) -> str:
        try:
            value = self.paths.onion_file.read_text(encoding="utf-8").strip().lower()
        except OSError:
            return ""
        return value if value.endswith(".onion") else ""

    def api_status(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {
            "app": "e-lite",
            "version": (snapshot.live or {}).get("version", E_LITE_VERSION),
            "core_version": BITCOIN_VERSION,
            "network": snapshot.network,
            "phase": snapshot.phase,
            "state": snapshot.state,
            "setup_complete": snapshot.setup_complete,
            "ready": bool((snapshot.live or {}).get("ready", False)),
            "fully_synced": snapshot.fully_synced,
            "core_ibd": snapshot.core_ibd,
            "indexed_height": snapshot.indexed,
            "core_height": snapshot.core_height,
            "core_headers": snapshot.headers,
            "index_progress": round(snapshot.index_progress, 2),
            "core_progress": round(snapshot.core_progress, 2),
            "lag_blocks": int((snapshot.live or {}).get("lag_blocks", 0) or 0),
            "clients": int((snapshot.live or {}).get("clients", 0) or 0),
            "max_clients": int((snapshot.live or {}).get("max_clients", 0) or 0),
            "onion": snapshot.onion,
            "domain": snapshot.domain,
            "listen": (snapshot.live or {}).get("listen", f"0.0.0.0:{ELECTRUM_PORT}"),
            "tls_listen": (snapshot.live or {}).get("tls_listen", f"0.0.0.0:{ELECTRUM_TLS_PORT}"),
            "db_bytes": int((snapshot.live or {}).get("db_bytes", 0) or 0),
            "disk_free_bytes": int((snapshot.live or {}).get("disk_free_bytes", 0) or 0),
            "uptime_seconds": int((snapshot.live or {}).get("uptime_seconds", 0) or 0),
            "runtime": snapshot.runtime,
            "process": snapshot.process,
            "tls": snapshot.tls,
        }


def make_server(app: WebApp, port: int = 3000) -> ThreadingHTTPServer:
    handler = type("EliteHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    server.daemon_threads = True
    return server


class Handler(BaseHTTPRequestHandler):
    app: WebApp
    server_version = "e-lite-umbrel"
    sys_version = ""

    # ---------------------------------------------------------------- HTTP verbs
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

        snapshot = app.snapshot()
        if parsed.path == "/":
            if not snapshot.setup_complete:
                self._send_html(setup_page(app, snapshot, lang, parsed.query))
            else:
                self._send_html(status_page(app, snapshot, lang, parsed.query))
            return
        if parsed.path == "/setup":
            self._send_html(setup_page(app, snapshot, lang, parsed.query))
            return
        if parsed.path == "/config":
            self._send_html(config_page(app, snapshot, lang, parsed.query))
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
            app.supervisor.request_restart()
            self._redirect("/")
            return

        if parsed.path == "/config":
            previous = app.settings()
            settings, errors = parse_form(form, previous)
            if errors:
                snapshot = app.snapshot()
                self._send_html(config_page(app, snapshot, lang, parsed.query, errors=errors, values=settings))
                return
            save_settings(app.paths.settings_file, settings)
            app.supervisor.request_restart()
            self._redirect("/config?saved=1&lang=" + lang)
            return

        if parsed.path == "/restart":
            app.supervisor.request_restart()
            self._redirect("/?restart=1&lang=" + lang)
            return

        if parsed.path == "/runtime/retry":
            app.runtime.retry()
            self._redirect("/setup?lang=" + lang)
            return

        self.send_error(404)

    # ------------------------------------------------------------------ utilities
    def _language(self, query: str) -> str:
        params = parse_qs(query)
        query_lang = (params.get("lang") or [None])[0]
        cookie = self._cookie("elite_lang")
        lang = select_language(self.headers.get("Accept-Language"), query_lang, cookie)
        if query_lang in ("es", "en"):
            self._set_cookie = query_lang
        else:
            self._set_cookie = None
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
        state = state_label("es", snapshot.state)
        return {
            "type": "four-stats",
            "refresh": "10s",
            "link": "",
            "items": [
                {"title": "Estado", "text": state, "subtext": snapshot.network},
                {"title": "Altura", "text": f"{snapshot.indexed:,}", "subtext": "bloques"},
                {"title": "Lag", "text": f"{max(0, snapshot.core_height - snapshot.indexed):,}", "subtext": "bloques"},
                {"title": "Clientes", "text": str((snapshot.live or {}).get("clients", 0)), "subtext": "conectados"},
            ],
        }

    def _widget_sync(self) -> dict[str, Any]:
        snapshot = self.app.snapshot()
        if snapshot.core_ibd:
            progress = snapshot.core_progress
        elif snapshot.fully_synced:
            progress = 100.0
        else:
            progress = snapshot.index_progress
        if snapshot.core_ibd:
            text = f"Core {snapshot.core_progress:.0f}%"
            label = "Bitcoin Core"
        elif snapshot.phase == "live":
            text = f"{progress:.0f}%"
            label = "Indexando" if progress < 99.99 else "Listo"
        else:
            text = snapshot.state
            label = "Estado"
        return {
            "type": "text-with-progress",
            "refresh": "10s",
            "link": "",
            "title": "Sincronización",
            "text": text,
            "progressLabel": label,
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
        "ERROR": "error",
    }
    return t(lang, mapping.get(state, "state_unknown"))


def status_class(snapshot: Snapshot) -> str:
    if snapshot.phase in ("setup", "runtime"):
        return "warn"
    if snapshot.phase in ("runtime_error", "process_error"):
        return "bad"
    state = snapshot.state
    if state == "READY" and snapshot.fully_synced:
        return "good"
    if state in ("FATAL_INTEGRITY", "CORE_UNAVAILABLE", "DEGRADED_RAWTX"):
        return "bad"
    return "warn"


def _nav(lang: str, active: str) -> str:
    q = f"?lang={lang}"
    status = f'<a class="{"active" if active == "status" else ""}" href="/{q}">{h(t(lang, "status"))}</a>'
    config = f'<a class="{"active" if active == "config" else ""}" href="/config{q}">{h(t(lang, "configuration"))}</a>'
    other = "en" if lang == "es" else "es"
    language = f'<a class="lang" href="?lang={other}">{h(t(lang, "language"))}</a>'
    return f'<nav>{status}{config}{language}</nav>'


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
.pill {{ display:inline-flex; align-items:center; gap:9px; padding:9px 14px; border-radius:999px; font-weight:700; font-size:14px; border:1px solid var(--line); }}
.pill .dot {{ width:9px; height:9px; border-radius:50%; background:currentColor; box-shadow:0 0 16px currentColor; }}
.pill.good {{ color:var(--good); background:rgba(0,205,152,.1); border-color:rgba(0,205,152,.3); }}
.pill.warn {{ color:var(--warn); background:rgba(255,196,107,.1); border-color:rgba(255,196,107,.3); }}
.pill.bad {{ color:var(--bad); background:rgba(255,125,125,.1); border-color:rgba(255,125,125,.3); }}
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
code, .mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
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
    <div class="sub">{h(t(lang, "version_label", version=E_LITE_VERSION, core=BITCOIN_VERSION))}</div>
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



def _runtime_card(snapshot: Snapshot, lang: str) -> str:
    runtime = snapshot.runtime
    phase = str(runtime.get("phase", "pending"))
    downloaded = int(runtime.get("downloaded", 0) or 0)
    total = int(runtime.get("total", 0) or 0)
    percent = (downloaded / total * 100.0) if total else 0.0
    kind = str(runtime.get("kind", ""))
    message = str(runtime.get("message", ""))
    if phase == "ready":
        return ""
    if phase == "error":
        return f"""<div class="card error"><h2>{h(t(lang, "runtime_error"))}</h2>
<p>{h(runtime.get("error", ""))}</p>
<form method="post" action="/runtime/retry"><button type="submit">{h(t(lang, "retry"))}</button></form></div>"""
    label = h(message)
    if kind:
        label += f' <span class="badge">{h(kind)}</span>'
    bar = f'<div class="progress"><span style="width:{percent:.1f}%"></span></div>' if total else '<div class="progress indeterminate"><span></span></div>'
    size = f"<p class=\"help\">{fmt_bytes(downloaded)} / {fmt_bytes(total)}</p>" if total else ""
    return f'<div class="card"><h2>{h(t(lang, "runtime_preparing"))}</h2><p>{label}</p>{bar}{size}</div>'


def setup_page(
    app: WebApp,
    snapshot: Snapshot,
    lang: str,
    query: str,
    errors: list[str] | None = None,
    values: Settings | None = None,
) -> str:
    errors = errors or []
    selected = (values.network if values else None) or snapshot.network or "signet"
    runtime = snapshot.runtime
    ready = str(runtime.get("phase")) == "ready" or snapshot.demo
    error_html = ""
    if errors:
        error_html = '<div class="card error"><ul>' + "".join(f"<li>{h(_error_text(lang, e))}</li>" for e in errors) + "</ul></div>"

    cards = []
    for network in NETWORKS:
        cards.append(
            f"""<label class="network"><input type="radio" name="network" value="{network}" {"checked" if selected == network else ""}>
<strong>{h(t(lang, f"setup_{network}_title"))}</strong><span>{h(t(lang, f"setup_{network}_help"))}</span></label>"""
        )

    ack = f'<label class="checkline" id="mainnet-ack"><input type="checkbox" name="mainnet_ack" value="1"> {h(t(lang, "setup_mainnet_ack"))}</label>'

    content = f"""
<div class="card">
  <h2>{h(t(lang, "setup_title"))}</h2>
  <p>{h(t(lang, "setup_intro"))}</p>
  <p class="help">{h(t(lang, "setup_ports"))}</p>
</div>
{error_html}
{_runtime_card(snapshot, lang)}
<form method="post" action="/setup?lang={lang}" class="card" onsubmit="return confirmSetup(this)">
  <h2>{h(t(lang, "network"))}</h2>
  <div class="network-grid">{''.join(cards)}</div>
  <div style="margin-top:16px">{ack}</div>
  <div style="margin-top:18px" class="inline">
    <button type="submit" {"disabled" if not ready else ""}>{h(t(lang, "save_setup"))}</button>
    <span class="help">{("" if ready else h(t(lang, "setup_runtime_wait")))}</span>
  </div>
</form>
<div class="card warning">
  <p><strong>{h(t(lang, "alpha_warning"))}</strong></p>
  <p>{h(t(lang, "security_warning"))}</p>
</div>
<script>
function confirmSetup(form) {{
  const net = form.querySelector('input[name="network"]:checked');
  if (!net) {{ alert({json.dumps(t(lang, "setup_network_required"))}); return false; }}
  if (net.value === 'mainnet') {{
    const ack = form.querySelector('input[name="mainnet_ack"]');
    if (!ack || !ack.checked) {{ alert({json.dumps(t(lang, "setup_mainnet_required"))}); return false; }}
    return confirm({json.dumps(t(lang, "setup_mainnet_help"))});
  }}
  return true;
}}
function updateAck() {{
  const form = document.querySelector('form[action^="/setup"]');
  if (!form) return;
  const net = form.querySelector('input[name="network"]:checked');
  const row = document.getElementById('mainnet-ack');
  if (row) row.style.display = net && net.value === 'mainnet' ? 'flex' : 'none';
}}
document.querySelectorAll('input[name="network"]').forEach(function(input) {{
  input.addEventListener('change', updateAck);
}});
updateAck();
</script>
"""
    return page(t(lang, "setup"), content, lang, active="setup", refresh=8 if not ready else 0)


def _error_text(lang: str, error: str) -> str:
    if error.startswith("network:"):
        return t(lang, "error_network")
    if error.startswith("mainnet_ack:"):
        return t(lang, "setup_mainnet_required")
    return error


def status_page(app: WebApp, snapshot: Snapshot, lang: str, query: str) -> str:
    status = status_class(snapshot)
    state_text = state_label(lang, snapshot.state) if snapshot.phase not in ("setup", "runtime") else t(lang, "not_ready")
    if snapshot.phase == "runtime":
        state_text = t(lang, "not_ready")
    if snapshot.phase == "starting":
        state_text = t(lang, "starting")
    if snapshot.demo:
        state_text = f'{state_text} · {t(lang, "demo_mode")}'

    notice = ""
    if "saved=1" in (query or ""):
        notice = f'<div class="card warning"><p>{h(t(lang, "saved"))}</p></div>'
    elif "restart=1" in (query or ""):
        notice = f'<div class="card warning"><p>{h(t(lang, "restart_requested"))}</p></div>'
    if snapshot.network == "mainnet":
        notice += f'<div class="card warning"><p>{h(t(lang, "mainnet_warning"))}</p></div>'
    if snapshot.phase in ("runtime_error", "process_error"):
        error = snapshot.runtime.get("error") or snapshot.process.get("last_error") or ""
        notice += f'<div class="card error"><p>{h(error)}</p></div>'

    sync = _sync_card(snapshot, lang)
    connections = _connections_card(snapshot, lang)
    details = _details_card(snapshot, lang)
    logs = _logs_card(snapshot, lang)
    runtime = _runtime_card(snapshot, lang)
    aria = "indeterminate" if snapshot.phase in ("starting", "runtime") else ""

    content = f"""
{notice}
<div class="card">
  <div class="inline" style="justify-content:space-between">
    <span class="pill {status}"><span class="dot"></span>{h(state_text)}</span>
    <span class="badge">{h(t(lang, "network_" + snapshot.network) if snapshot.network in NETWORKS else snapshot.network)}</span>
  </div>
  <div class="grid three" style="margin-top:16px">
    <div class="stat"><div class="k">{h(t(lang, "height"))}</div><div class="v">{snapshot.indexed:,}</div></div>
    <div class="stat"><div class="k">{h(t(lang, "core_height"))}</div><div class="v">{snapshot.core_height:,}</div></div>
    <div class="stat"><div class="k">{h(t(lang, "lag"))}</div><div class="v">{max(0, snapshot.core_height - snapshot.indexed):,} <small>{h(t(lang, "blocks"))}</small></div></div>
  </div>
</div>
{runtime}
{sync}
{connections}
{details}
{logs}
<div class="card warning">
  <p><strong>{h(t(lang, "alpha_warning"))}</strong></p>
  <p>{h(t(lang, "security_warning"))}</p>
</div>
"""
    return page(t(lang, "status_page"), content, lang, active="status", refresh=10)


def _sync_card(snapshot: Snapshot, lang: str) -> str:
    if snapshot.phase in ("runtime", "starting", "stopped", "runtime_error", "process_error"):
        return f'<div class="card"><h2>{h(t(lang, "sync"))}</h2><div class="progress indeterminate"><span></span></div><p class="help">{h(t(lang, "connecting"))}</p></div>'
    core_bar = f'<div class="progress"><span style="width:{snapshot.core_progress:.1f}%"></span></div>'
    index_bar = f'<div class="progress"><span style="width:{snapshot.index_progress:.1f}%"></span></div>'
    if snapshot.core_ibd:
        message = t(lang, "waiting_core_sync")
        overall = snapshot.core_progress
    elif snapshot.fully_synced:
        message = t(lang, "fully_synced")
        overall = 100.0
    else:
        message = t(lang, "index_sync")
        overall = snapshot.index_progress
    if str(snapshot.live.get("version") if snapshot.live else "") and snapshot.live and snapshot.live.get("tuning"):
        profile = snapshot.live.get("tuning", {}).get("profile", "auto")
    else:
        profile = "auto"
    return f"""
<div class="card">
  <h2>{h(t(lang, "sync"))}</h2>
  <div class="inline" style="justify-content:space-between"><span>{h(message)}</span><strong>{overall:.1f}%</strong></div>
  <div class="progress"><span style="width:{overall:.1f}%"></span></div>
  <div class="grid" style="margin-top:16px">
    <div><div class="k help">{h(t(lang, "core_sync"))} · {snapshot.core_progress:.1f}%</div>{core_bar}<div class="help">{snapshot.core_height:,} / {snapshot.headers:,}</div></div>
    <div><div class="k help">{h(t(lang, "index_sync"))} · {snapshot.index_progress:.1f}%</div>{index_bar}<div class="help">{snapshot.indexed:,} / {snapshot.headers:,} · {h(str(profile))}</div></div>
  </div>
</div>"""


def _connections_card(snapshot: Snapshot, lang: str) -> str:
    domain = snapshot.domain or "umbrel.local"
    tcp = f"{domain}:{ELECTRUM_PORT}"
    tls = f"{domain}:{ELECTRUM_TLS_PORT}"
    onion_tcp = f"{snapshot.onion}:{ELECTRUM_PORT}" if snapshot.onion else ""
    onion_tls = f"{snapshot.onion}:{ELECTRUM_TLS_PORT}" if snapshot.onion else ""
    ca_button = (
        f'<a class="button secondary" href="/tls/ca.crt">{h(t(lang, "download_ca"))}</a>'
        if snapshot.tls.get("ca_available")
        else ""
    )
    if onion_tcp:
        onion_html = (
            f'<div class="endpoint" style="margin-top:10px"><code>{h(onion_tcp)}</code>'
            f'<button class="secondary" onclick="copyValue(this.dataset.value, this)" data-value="{h(onion_tcp)}">'
            f'{h(t(lang, "copy"))}</button></div>'
            f'<div class="endpoint" style="margin-top:8px"><code>{h(onion_tls)}</code>'
            f'<button class="secondary" onclick="copyValue(this.dataset.value, this)" data-value="{h(onion_tls)}">'
            f'{h(t(lang, "copy"))}</button></div>'
        )
    else:
        onion_html = f'<p class="help">{h(t(lang, "onion_generating"))}</p>'
    return f"""
<div class="card">
  <h2>{h(t(lang, "connections"))}</h2>
  <div class="grid">
    <div class="stat">
      <div class="k">{h(t(lang, "clearnet"))}</div>
      <div class="endpoint" style="margin-top:10px"><code>{h(tcp)}</code><button class="secondary" onclick="copyValue(this.dataset.value, this)" data-value="{h(tcp)}">{h(t(lang, "copy"))}</button></div>
      <div class="endpoint" style="margin-top:8px"><code>{h(tls)}</code><button class="secondary" onclick="copyValue(this.dataset.value, this)" data-value="{h(tls)}">{h(t(lang, "copy"))}</button></div>
      <p class="help">{h(t(lang, "clearnet_help"))}</p>
      <p class="help">{h(t(lang, "tls_help"))}</p>
      <div class="inline">{ca_button}</div>
    </div>
    <div class="stat">
      <div class="k">{h(t(lang, "tor"))}</div>
      {onion_html}
      <p class="help">{h(t(lang, "tor_help"))}</p>
    </div>
  </div>
</div>"""



def _details_card(snapshot: Snapshot, lang: str) -> str:
    live = snapshot.live or {}
    tuning = live.get("tuning", {}) if isinstance(live.get("tuning"), dict) else {}
    memory = live.get("memory", {}) if isinstance(live.get("memory"), dict) else {}
    clients = f"{int(live.get('clients', 0) or 0)} / {int(live.get('max_clients', 0) or 0)}"
    rows = [
        (t(lang, "state"), state_label(lang, snapshot.state)),
        (t(lang, "network"), snapshot.network or "—"),
        (t(lang, "version"), str(live.get("version", E_LITE_VERSION))),
        (t(lang, "core_version"), BITCOIN_VERSION),
        (t(lang, "clients"), clients if snapshot.phase == "live" else "—"),
        (t(lang, "uptime"), fmt_duration(int(live.get("uptime_seconds", 0) or 0)) if snapshot.phase == "live" else "—"),
        (t(lang, "disk_free"), fmt_bytes(int(live.get("disk_free_bytes", 0) or 0)) if snapshot.phase == "live" else "—"),
        (t(lang, "db_size"), fmt_bytes(int(live.get("db_bytes", 0) or 0)) if snapshot.phase == "live" else "—"),
        (t(lang, "memory"), f"{int(memory.get('available_mib', 0) or 0):,} MiB" if snapshot.phase == "live" else "—"),
        (t(lang, "profile"), str(tuning.get("profile", "—")) if snapshot.phase == "live" else "—"),
        (t(lang, "electrum_ports"), f"{ELECTRUM_PORT} TCP · {ELECTRUM_TLS_PORT} TLS"),
        (t(lang, "core_datadir"), "/data/bitcoin"),
    ]
    body = "".join(f'<div class="row"><span class="k">{h(k)}</span><span class="v">{h(v)}</span></div>' for k, v in rows)
    return f'<div class="card"><h2>{h(t(lang, "details"))}</h2>{body}</div>'


def _logs_card(snapshot: Snapshot, lang: str) -> str:
    e_lite = "\n".join(snapshot.logs) if snapshot.logs else t(lang, "no_logs")
    core = "\n".join(snapshot.core_logs) if snapshot.core_logs else t(lang, "no_logs")
    return f"""
<div class="card">
  <h2>{h(t(lang, "logs"))}</h2>
  <p class="help">{h(t(lang, "latest_logs"))}</p>
  <pre>{h(e_lite)}</pre>
  <details><summary>{h(t(lang, "show_core_log"))}</summary><pre style="margin-top:12px">{h(core)}</pre></details>
</div>"""


def config_page(
    app: WebApp,
    snapshot: Snapshot,
    lang: str,
    query: str,
    errors: list[str] | None = None,
    values: Settings | None = None,
) -> str:
    settings = values or app.settings()
    errors = errors or []
    error_html = ""
    if errors:
        error_html = '<div class="card error"><ul>' + "".join(f"<li>{h(_error_text(lang, e))}</li>" for e in errors) + "</ul></div>"
    notice = ""
    if "saved=1" in (query or ""):
        notice = f'<div class="card warning"><p>{h(t(lang, "saved"))}</p></div>'

    groups: dict[str, list[str]] = {}
    for field_def in FIELDS:
        group = "advanced" if field_def.advanced else field_def.group
        groups.setdefault(group, []).append(field_def.key)

    network_select = "".join(
        f'<option value="{net}" {"selected" if (settings.network or snapshot.network or "signet") == net else ""}>{h(t(lang, "network_" + net))}</option>'
        for net in NETWORKS
    )
    sections = []
    for group in ("performance", "server", "pruning", "core"):
        if group not in groups:
            continue
        rows = "".join(_field_row(lang, settings, key) for key in groups[group])
        sections.append(f'<div class="card"><h2>{h(t(lang, "group_" + group))}</h2>{rows}</div>')

    advanced_rows = "".join(
        _field_row(lang, settings, key) for key in groups.get("advanced", [])
    )
    advanced = f'<div class="card"><details><summary>{h(t(lang, "group_advanced"))}</summary>{advanced_rows}</details></div>' if advanced_rows else ""

    content = f"""
{notice}
{error_html}
<form method="post" action="/config?lang={lang}" onsubmit="return confirmSave(this)" data-network="{h(settings.network or snapshot.network or 'signet')}">
  <div class="card">
    <h2>{h(t(lang, "network"))}</h2>
    <p class="help">{h(t(lang, "network_change_notice"))}</p>
    <select name="network" id="network-select">{network_select}</select>
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
<script>
function toggleDefault(box) {{
  const target = document.getElementById('value-' + box.dataset.key);
  if (!target) return;
  target.disabled = box.checked;
}}
function resetDefaults(form) {{
  form.querySelectorAll('input[type=checkbox][data-key]').forEach(function(box) {{
    box.checked = true;
    toggleDefault(box);
  }});
}}
function confirmSave(form) {{
  const net = form.querySelector('#network-select');
  if (net && net.value !== form.dataset.network) {{
    return confirm({json.dumps(t(lang, "network_change_notice"))});
  }}
  return confirm({json.dumps(t(lang, "restart_notice"))});
}}
</script>
"""
    return page(t(lang, "settings_page"), content, lang, active="config")


def _field_row(lang: str, settings: Settings, key: str) -> str:
    field_def = FIELDS_BY_KEY[key]
    is_default = settings.is_default(key)
    value = settings.value(key)
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
  <div class="label">
    <label for="default-{key}">{h(field_label(lang, key))}</label>
    <code>{h(key)}</code>
  </div>
  <div>
    <label class="checkline"><input id="default-{key}" type="checkbox" name="default__{key}" value="1" data-key="{h(key)}" {"checked" if is_default else ""} onchange="toggleDefault(this)"> {h(t(lang, "use_default"))} {default_hint}</label>
    {control}
    <p class="help">{h(field_help(lang, key))}</p>
  </div>
</div>"""
