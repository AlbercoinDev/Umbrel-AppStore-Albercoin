import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from config import DEBUG, DRY_RUN, LOG_MAX_LINES, TOR_DATA_DIR, UMBREL_APP_DATA_DIR
from detector import scan_apps
from i18n import TRANSLATIONS
from restarter import get_docker_info, is_docker_accessible
from rotator import rotate_app_services
from system_onions import delete_system_onions, scan_system_onions

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("onion_rotator")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
PORT = 8900
_in_memory_logs: list[dict] = []
_operations: dict[str, dict] = {}
_operations_lock = threading.Lock()


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


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _health() -> dict:
    return {
        "status": "ok",
        "tor_data_dir": TOR_DATA_DIR,
        "tor_data_accessible": os.path.isdir(TOR_DATA_DIR),
        "app_data_dir": UMBREL_APP_DATA_DIR,
        "app_data_accessible": os.path.isdir(UMBREL_APP_DATA_DIR),
        "docker_accessible": is_docker_accessible(),
        "dry_run": DRY_RUN,
    }


def _api_path(path: str) -> str:
    if path == "/health":
        return "/health"
    for api_path in ("/api/apps", "/api/logs", "/api/i18n", "/api/rotate", "/api/system-onions", "/api/system-onions/delete"):
        if path == api_path or path.endswith(api_path):
            return api_path
    marker = "/api/operations/"
    if marker in path:
        return path[path.index(marker):]
    return path


def _operation_template(operation_id: str, selected_service_ids: list[str], apps: list[dict]) -> dict:
    selected = set(selected_service_ids)
    op_apps = []
    for app in apps:
        services = []
        for service in app.get("services", []):
            if service["service_id"] in selected:
                services.append({
                    "service_id": service["service_id"],
                    "tor_dir": service["tor_dir"],
                    "old_onion": service.get("onion_address", ""),
                    "new_onion": "",
                    "status": "queued",
                    "message": "",
                })
        if services:
            op_apps.append({"app_id": app["app_id"], "status": "queued", "message": "", "services": services})
    return {
        "operation_id": operation_id,
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "current_app_id": "",
        "apps": op_apps,
        "results": [],
    }


def _update_operation(operation_id: str, updater):
    with _operations_lock:
        operation = _operations.get(operation_id)
        if not operation:
            return
        updater(operation)
        operation["updated_at"] = _now()


def _run_operation(operation_id: str, selected_service_ids: list[str], apps: list[dict]):
    logger.info(f"Starting operation {operation_id} for services: {', '.join(selected_service_ids)}")
    selected = set(selected_service_ids)
    results = []

    def set_operation_status(status: str, current_app_id: str = ""):
        _update_operation(operation_id, lambda op: op.update({"status": status, "current_app_id": current_app_id}))

    set_operation_status("running")
    try:
        for app in apps:
            app_services = [s for s in app.get("services", []) if s["service_id"] in selected]
            if not app_services:
                continue
            app_id = app["app_id"]
            selected_for_app = [s["service_id"] for s in app_services]

            def progress(progress_app_id: str, status: str, message: str, extra: dict | None):
                def apply(op):
                    op["status"] = "running"
                    op["current_app_id"] = progress_app_id
                    for op_app in op["apps"]:
                        if op_app["app_id"] == progress_app_id:
                            op_app["status"] = status
                            op_app["message"] = message
                            if extra and "services" in extra:
                                for service_update in extra["services"]:
                                    for op_service in op_app["services"]:
                                        if op_service["service_id"] == service_update["service_id"]:
                                            op_service.update({
                                                "new_onion": service_update.get("new_onion", op_service.get("new_onion", "")),
                                                "status": service_update.get("status", op_service.get("status", "")),
                                                "message": service_update.get("message", op_service.get("message", "")),
                                            })
                _update_operation(operation_id, apply)

            result = rotate_app_services(app, selected_for_app, progress)
            results.append(result)
            _update_operation(operation_id, lambda op, r=result: op["results"].append(r))

        final_status = "completed"
        if any(app.get("status") not in ("ready", "skipped") for app in results):
            final_status = "completed_with_errors"
        set_operation_status(final_status)
        logger.info(f"Operation {operation_id} finished with status {final_status}")
    except Exception as e:
        logger.exception(f"Operation {operation_id} failed")
        _update_operation(operation_id, lambda op: op.update({"status": "error", "message": str(e)}))


def _create_operation(service_ids: list[str]) -> dict:
    apps = scan_apps()
    available_service_ids = {s["service_id"] for app in apps for s in app.get("services", [])}
    selected = [service_id for service_id in service_ids if service_id in available_service_ids]
    invalid = [service_id for service_id in service_ids if service_id not in available_service_ids]
    if not selected:
        return {"error": "no_valid_services_selected", "invalid_service_ids": invalid}

    operation_id = uuid.uuid4().hex
    operation = _operation_template(operation_id, selected, apps)
    if invalid:
        operation["invalid_service_ids"] = invalid
    with _operations_lock:
        _operations[operation_id] = operation
    thread = threading.Thread(target=_run_operation, args=(operation_id, selected, apps), daemon=True)
    thread.start()
    return {"operation_id": operation_id, "operation": operation}


class Handler(BaseHTTPRequestHandler):
    server_version = "OnionRotator/1.0.8"

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
            self._send_json({"apps": scan_apps(), "dry_run": DRY_RUN})
            return
        if path == "/api/system-onions":
            self._send_json({"services": scan_system_onions(), "dry_run": DRY_RUN})
            return
        if path.startswith("/api/operations/"):
            operation_id = path.rsplit("/", 1)[-1]
            with _operations_lock:
                operation = _operations.get(operation_id)
            if not operation:
                self._send_json({"error": "operation_not_found"}, 404)
                return
            self._send_json({"operation": operation})
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
        if path == "/api/system-onions/delete":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw.decode("utf-8"))
                service_ids = payload.get("service_ids") or payload.get("serviceIds") or []
                if not isinstance(service_ids, list) or not all(isinstance(x, str) for x in service_ids):
                    self._send_json({"error": "invalid_service_ids"}, 400)
                    return
                results = delete_system_onions(service_ids)
                self._send_json({
                    "results": results,
                    "message": "restart_umbrel_server_to_regenerate",
                })
            except Exception as e:
                logger.exception("System Onion delete request failed")
                self._send_json({"error": str(e)}, 500)
            return
        if path != "/api/rotate":
            self._send_json({"error": "not_found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            service_ids = payload.get("service_ids") or payload.get("serviceIds") or payload.get("app_ids") or []
            if not isinstance(service_ids, list) or not all(isinstance(x, str) for x in service_ids):
                self._send_json({"error": "invalid_service_ids"}, 400)
                return
            response = _create_operation(service_ids)
            self._send_json(response, 400 if "error" in response else 202)
        except Exception as e:
            logger.exception("Rotate request failed")
            self._send_json({"error": str(e)}, 500)


def main():
    logger.info("=== Onion Rotator v1.0.8 starting ===")
    logger.info(f"DRY_RUN={DRY_RUN}, DEBUG={DEBUG}")
    logger.info(f"TOR_DATA_DIR={TOR_DATA_DIR}")
    logger.info(f"tor_data_exists={os.path.isdir(TOR_DATA_DIR)}")
    logger.info(f"UMBREL_APP_DATA_DIR={UMBREL_APP_DATA_DIR}")
    logger.info(f"app_data_exists={os.path.isdir(UMBREL_APP_DATA_DIR)}")
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
