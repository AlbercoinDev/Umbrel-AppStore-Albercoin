import json
import logging
import os
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from config import DEBUG, DRY_RUN, LOG_MAX_LINES, TOR_DATA_DIR, UMBREL_APP_DATA_DIR
from detector import get_app_data_app_ids, scan_apps
from i18n import TRANSLATIONS
from restarter import get_docker_info, get_installed_app_ids, is_docker_accessible
from rotator import rotate_single

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("onion_rotator")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
PORT = 8900
_in_memory_logs: list[dict] = []


class LogHandler(logging.Handler):
    def emit(self, record):
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "message": self.format(record),
        }
        _in_memory_logs.append(entry)
        if len(_in_memory_logs) > LOG_MAX_LINES:
            _in_memory_logs.pop(0)


logging.getLogger("onion_rotator").addHandler(LogHandler())


def _health() -> dict:
    return {
        "status": "ok",
        "tor_data_dir": TOR_DATA_DIR,
        "tor_data_accessible": os.path.isdir(TOR_DATA_DIR),
        "app_data_accessible": os.path.isdir(UMBREL_APP_DATA_DIR),
        "docker_accessible": is_docker_accessible(),
        "dry_run": DRY_RUN,
    }


def _api_path(path: str) -> str:
    if path == "/health":
        return "/health"
    for api_path in ("/api/apps", "/api/logs", "/api/i18n", "/api/rotate"):
        if path == api_path or path.endswith(api_path):
            return api_path
    return path


def _scan_installed_apps() -> list[dict]:
    app_data_ids = get_app_data_app_ids()
    docker_ids = get_installed_app_ids()

    if app_data_ids:
        # app-data is the authoritative source for installed Umbrel apps. This
        # intentionally hides stale Tor dirs like app-electrs-rpc when no app
        # with that exact ID is installed.
        logger.debug(f"Filtering Tor hostnames by app-data IDs: {sorted(app_data_ids)}")
        return scan_apps(app_data_ids)

    if docker_ids:
        logger.warning("app-data unavailable; filtering Tor hostnames by Docker projects")
        return scan_apps(docker_ids)

    logger.warning("No installed app source available; returning no apps to avoid stale hostnames")
    return []


def _rotate(app_ids: list[str]) -> dict:
    if not app_ids:
        return {"error": "no_apps_selected", "results": []}

    all_apps = _scan_installed_apps()
    app_map = {a["app_id"]: a for a in all_apps}
    results = []

    for app_id in app_ids:
        if app_id not in app_map:
            results.append({
                "app_id": app_id,
                "old_onion": "",
                "new_onion": "",
                "status": "invalid_hostname",
                "message": "app_not_found",
            })
            continue

        app = app_map[app_id]
        results.append(rotate_single(
            app_id=app_id,
            hostname_path=app["hostname_path"],
            current_onion=app["onion_address"],
            restart_app_id=app.get("restart_app_id") or app_id,
        ))

    return {"results": results}


class Handler(BaseHTTPRequestHandler):
    server_version = "OnionRotator/1.0.3"

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = _api_path(urlparse(self.path).path)
        if path == "/health":
            self._send_json(_health())
            return
        if path == "/api/apps":
            self._send_json({"apps": _scan_installed_apps(), "dry_run": DRY_RUN})
            return
        if path == "/api/logs":
            self._send_json({"logs": _in_memory_logs[-100:]})
            return
        if path == "/api/i18n":
            self._send_json(TRANSLATIONS)
            return
        if path in ("/", "/index.html") or not path.startswith("/api/"):
            index_path = os.path.join(STATIC_DIR, "index.html")
            if os.path.exists(index_path):
                with open(index_path, encoding="utf-8") as f:
                    self._send_html(f.read())
                return
            self._send_html("<h1>Onion Rotator</h1><p>Frontend not found.</p>", 500)
            return
        self._send_json({"error": "not_found"}, 404)

    def do_POST(self):
        path = _api_path(urlparse(self.path).path)
        if path != "/api/rotate":
            self._send_json({"error": "not_found"}, 404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            app_ids = payload.get("app_ids") or payload.get("appIds") or []
            if not isinstance(app_ids, list) or not all(isinstance(x, str) for x in app_ids):
                self._send_json({"error": "invalid_app_ids", "results": []}, 400)
                return
            result = _rotate(app_ids)
            self._send_json(result, 400 if "error" in result else 200)
        except Exception as e:
            logger.exception("Rotate request failed")
            self._send_json({"error": str(e), "results": []}, 500)


def main():
    logger.info("=== Onion Rotator v1.0.3 starting ===")
    logger.info(f"DRY_RUN={DRY_RUN}, DEBUG={DEBUG}")
    logger.info(f"TOR_DATA_DIR={TOR_DATA_DIR}")
    logger.info(f"tor_data_exists={os.path.isdir(TOR_DATA_DIR)}")
    docker_ok = is_docker_accessible()
    logger.info(f"docker_accessible={docker_ok}")
    if docker_ok:
        info = get_docker_info()
        logger.info(f"docker_version={info.get('ServerVersion', 'unknown')}")
    else:
        logger.warning("Docker socket not accessible; app restart will fail")

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    logger.info(f"Listening on 0.0.0.0:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
