import logging
import os
import pathlib
from typing import Optional

from config import APP_ID_REGEX, DEBUG, ONION_V3_REGEX, TOR_DATA_DIR, UMBREL_APP_DATA_DIR

logger = logging.getLogger("onion_rotator.detector")

REAL_TOR_DATA_DIR: Optional[str] = None


def _resolve_tor_data_dir() -> str:
    global REAL_TOR_DATA_DIR
    if REAL_TOR_DATA_DIR is not None:
        return REAL_TOR_DATA_DIR
    REAL_TOR_DATA_DIR = os.path.realpath(TOR_DATA_DIR)
    return REAL_TOR_DATA_DIR


def _is_safe_path(path: str) -> bool:
    real = os.path.realpath(path)
    base = _resolve_tor_data_dir()
    try:
        return os.path.commonpath([real, base]) == base
    except ValueError:
        return False


def _read_hostname(hostname_path: str) -> Optional[str]:
    try:
        if not os.path.isfile(hostname_path):
            return None
        if os.path.islink(hostname_path):
            logger.warning(f"Skipping symlink: {hostname_path}")
            return None
        content = pathlib.Path(hostname_path).read_text().strip()
        if ONION_V3_REGEX.match(content):
            return content
        logger.debug(f"Invalid onion format in {hostname_path}: {content}")
        return None
    except (OSError, PermissionError) as e:
        logger.warning(f"Error reading {hostname_path}: {e}")
        return None


def _extract_service_id(dirname: str) -> Optional[str]:
    prefix = "app-"
    if dirname.startswith(prefix):
        service_id = dirname[len(prefix):]
        if APP_ID_REGEX.match(service_id):
            return service_id
    return None


_extract_app_id = _extract_service_id


def get_app_data_app_ids() -> set[str]:
    app_data_dir = os.path.realpath(UMBREL_APP_DATA_DIR)
    app_ids: set[str] = set()
    if not os.path.isdir(app_data_dir):
        logger.warning(f"Umbrel app-data directory not found: {app_data_dir}")
        return app_ids
    try:
        for entry in os.listdir(app_data_dir):
            full_path = os.path.join(app_data_dir, entry)
            if os.path.isdir(full_path) and not os.path.islink(full_path) and APP_ID_REGEX.match(entry):
                app_ids.add(entry)
    except PermissionError as e:
        logger.error(f"Permission denied scanning app-data {app_data_dir}: {e}")
    return app_ids


def owner_app_id(service_id: str, installed_app_ids: set[str]) -> Optional[str]:
    if service_id in installed_app_ids:
        return service_id
    matches = [app_id for app_id in installed_app_ids if service_id.startswith(f"{app_id}-")]
    if not matches:
        return None
    return max(matches, key=len)


def scan_services(installed_app_ids: set[str] | None = None, include_unavailable: bool = False) -> list[dict]:
    installed_app_ids = installed_app_ids if installed_app_ids is not None else get_app_data_app_ids()
    if not installed_app_ids:
        logger.warning("No installed app IDs available; hiding Tor services to avoid stale entries")
        return []

    tor_dir = _resolve_tor_data_dir()
    if not os.path.isdir(tor_dir):
        logger.error(f"Tor data directory not found: {tor_dir}")
        return []

    services: list[dict] = []
    try:
        for entry in os.listdir(tor_dir):
            service_dir = os.path.join(tor_dir, entry)
            if not os.path.isdir(service_dir) or os.path.islink(service_dir):
                continue
            service_id = _extract_service_id(entry)
            if service_id is None:
                continue
            app_id = owner_app_id(service_id, installed_app_ids)
            if app_id is None:
                continue

            hostname_path = os.path.join(service_dir, "hostname")
            if not _is_safe_path(service_dir) or not _is_safe_path(hostname_path):
                logger.warning(f"Unsafe Tor service path blocked: {service_dir}")
                continue

            onion = _read_hostname(hostname_path)
            if not onion and not include_unavailable:
                continue

            services.append({
                "app_id": app_id,
                "service_id": service_id,
                "tor_dir": entry,
                "service_dir": service_dir,
                "hostname_path": hostname_path,
                "onion_address": onion or "",
                "status": "available" if onion else "no_hostname",
            })
            if DEBUG:
                logger.debug(f"Detected onion service {service_id} -> {app_id}: {onion or 'no onion'}")
    except PermissionError as e:
        logger.error(f"Permission denied scanning {tor_dir}: {e}")

    services.sort(key=lambda x: (x["app_id"], x["service_id"]))
    return services


def group_services_by_app(services: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for service in services:
        app_id = service["app_id"]
        grouped.setdefault(app_id, {
            "app_id": app_id,
            "status": "idle",
            "services": [],
        })["services"].append(service)
    return [grouped[app_id] for app_id in sorted(grouped)]


def scan_apps() -> list[dict]:
    return group_services_by_app(scan_services())
