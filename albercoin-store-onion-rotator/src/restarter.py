import http.client
import json
import logging
import socket
from urllib.parse import quote

from config import DEBUG, DRY_RUN

logger = logging.getLogger("onion_rotator.restarter")

DOCKER_SOCKET = "/var/run/docker.sock"


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


def _docker_request(method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
    conn = UnixHTTPConnection(DOCKER_SOCKET)
    try:
        conn.request(method, path, body=body)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def is_docker_accessible() -> bool:
    try:
        status, body = _docker_request("GET", "/_ping")
        return status == 200 and body.strip() == b"OK"
    except Exception as e:
        logger.error(f"Failed to connect to Docker socket: {e}")
        return False


def get_docker_info() -> dict:
    try:
        status, body = _docker_request("GET", "/info")
        if status != 200:
            return {}
        return json.loads(body.decode("utf-8"))
    except Exception as e:
        logger.warning(f"Failed to read Docker info: {e}")
        return {}


def _list_containers() -> list[dict]:
    status, body = _docker_request("GET", "/containers/json?all=1")
    if status != 200:
        raise RuntimeError(f"docker_list_failed_status_{status}")
    return json.loads(body.decode("utf-8"))


def get_installed_app_ids() -> set[str]:
    app_ids: set[str] = set()
    try:
        for container in _list_containers():
            labels = container.get("Labels") or {}
            names = container.get("Names") or []
            candidates = [
                labels.get("com.docker.compose.project", ""),
                labels.get("app", ""),
                labels.get("umbrel.appId", ""),
            ]
            for name in names:
                clean = name.strip("/")
                if "_" in clean:
                    candidates.append(clean.split("_", 1)[0])
            for candidate in candidates:
                if candidate:
                    app_ids.add(candidate)
    except Exception as e:
        logger.warning(f"Failed to list installed app IDs from Docker: {e}")
    return app_ids


def _container_matches_app(container: dict, app_id: str) -> bool:
    labels = container.get("Labels") or {}
    names = container.get("Names") or []
    compose_project = labels.get("com.docker.compose.project", "")
    umbrel_app = labels.get("app", "") or labels.get("umbrel.appId", "")

    if compose_project == app_id or umbrel_app == app_id:
        return True

    # Conservative fallback for older Umbrel/Compose naming.
    return any(name.strip("/").startswith(f"{app_id}_") for name in names)


def _restart_container(container_id: str) -> tuple[bool, str]:
    status, body = _docker_request("POST", f"/containers/{quote(container_id)}/restart")
    if status in (204, 304):
        return True, "restarted"
    return False, body.decode("utf-8", errors="replace")


def restart_app(app_id: str) -> tuple[bool, str]:
    if DRY_RUN:
        logger.info(f"DRY RUN: Would restart app {app_id}")
        return True, "dry_run"

    try:
        containers = [c for c in _list_containers() if _container_matches_app(c, app_id)]
        if not containers:
            logger.warning(f"No containers found for app {app_id}")
            return False, "no_containers_found"

        restarted = 0
        errors: list[str] = []
        for container in containers:
            container_id = container.get("Id", "")
            name = ",".join(container.get("Names", [])) or container_id[:12]
            if DEBUG:
                logger.debug(f"Restarting container {name} for app {app_id}")
            ok, message = _restart_container(container_id)
            if ok:
                restarted += 1
            else:
                errors.append(f"{name}: {message}")

        if errors:
            logger.error(f"Failed restarting some containers for {app_id}: {'; '.join(errors)}")
            return False, "; ".join(errors)

        logger.info(f"Restarted {restarted} container(s) for app {app_id}")
        return True, f"restarted_{restarted}_containers"
    except Exception as e:
        logger.error(f"Unexpected error restarting {app_id}: {e}")
        return False, f"error: {e}"
