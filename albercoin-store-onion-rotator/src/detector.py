import os
import re
import logging
import pathlib
from typing import Optional
from config import TOR_DATA_DIR, ONION_V3_REGEX, APP_ID_REGEX, DEBUG

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
    common = os.path.commonpath([real, base])
    return common == base


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


def _extract_app_id(dirname: str) -> Optional[str]:
    PREFIX = "app-"
    if dirname.startswith(PREFIX):
        app_id = dirname[len(PREFIX):]
        if APP_ID_REGEX.match(app_id):
            return app_id
    return None


def scan_apps(installed_app_ids: set[str] | None = None) -> list[dict]:
    tor_dir = _resolve_tor_data_dir()
    if not os.path.isdir(tor_dir):
        logger.error(f"Tor data directory not found: {tor_dir}")
        return []

    detected: list[dict] = []
    try:
        for entry in os.listdir(tor_dir):
            app_dir = os.path.join(tor_dir, entry)
            if not os.path.isdir(app_dir) or os.path.islink(app_dir):
                continue

            app_id = _extract_app_id(entry)
            if app_id is None:
                continue
            if installed_app_ids is not None and app_id not in installed_app_ids:
                continue

            hostname_path = os.path.join(app_dir, "hostname")
            if not _is_safe_path(hostname_path):
                logger.warning(f"Path traversal blocked: {hostname_path}")
                continue

            onion = _read_hostname(hostname_path)
            status = "available" if onion else "no_hostname"

            detected.append({
                "app_id": app_id,
                "hostname_path": hostname_path,
                "onion_address": onion or "",
                "status": status,
            })

            if DEBUG:
                logger.debug(f"Detected {app_id}: {onion or 'no onion'}")
    except PermissionError as e:
        logger.error(f"Permission denied scanning {tor_dir}: {e}")

    detected.sort(key=lambda x: x["app_id"])
    return detected
