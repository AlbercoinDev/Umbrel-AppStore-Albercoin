import os
import time
import logging
from typing import Optional
from config import APP_ID_REGEX, DRY_RUN, POLL_INTERVAL, POLL_TIMEOUT
from detector import _read_hostname, _is_safe_path
from restarter import has_restart_target, restart_app, restart_tor_containers

logger = logging.getLogger("onion_rotator.rotator")

ROTATION_FILES = ("hostname", "hs_ed25519_secret_key", "hs_ed25519_public_key")


def validate_app_id(app_id: str) -> Optional[str]:
    if not APP_ID_REGEX.match(app_id):
        return f"invalid_app_id: {app_id}"
    return None


def delete_hostname(hostname_path: str) -> tuple[bool, str]:
    if not _is_safe_path(hostname_path):
        return False, "path_traversal_blocked"

    if DRY_RUN:
        logger.info(f"DRY RUN: Would delete {hostname_path}")
        return True, "dry_run"

    try:
        if not os.path.isfile(hostname_path):
            return False, "file_not_found"
        os.remove(hostname_path)
        logger.info(f"Deleted hostname: {hostname_path}")
        return True, "deleted"
    except PermissionError:
        logger.error(f"Permission denied deleting {hostname_path}")
        return False, "permission_denied"
    except OSError as e:
        logger.error(f"Error deleting {hostname_path}: {e}")
        return False, f"delete_error: {e}"


def delete_hidden_service_keys(hostname_path: str) -> tuple[bool, str]:
    service_dir = os.path.dirname(hostname_path)
    if not _is_safe_path(hostname_path) or not _is_safe_path(service_dir):
        return False, "path_traversal_blocked"

    if DRY_RUN:
        logger.info(f"DRY RUN: Would delete Tor rotation files in {service_dir}")
        return True, "dry_run"

    deleted: list[str] = []
    try:
        for filename in ROTATION_FILES:
            path = os.path.join(service_dir, filename)
            if not _is_safe_path(path):
                return False, "path_traversal_blocked"
            if os.path.islink(path):
                return False, "symlink_blocked"
            if os.path.exists(path):
                os.remove(path)
                deleted.append(filename)
        if "hostname" not in deleted:
            return False, "hostname_not_found"
        logger.info(f"Deleted Tor rotation files in {service_dir}: {', '.join(deleted)}")
        return True, "deleted_" + "_".join(deleted)
    except PermissionError:
        logger.error(f"Permission denied deleting Tor rotation files in {service_dir}")
        return False, "permission_denied"
    except OSError as e:
        logger.error(f"Error deleting Tor rotation files in {service_dir}: {e}")
        return False, f"delete_error: {e}"


def wait_for_new_hostname(hostname_path: str, old_onion: str) -> tuple[str, str]:
    if DRY_RUN:
        return "success", "dry_run_new_onion.onion"

    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        new_onion = _read_hostname(hostname_path)
        if new_onion and new_onion != old_onion:
            return "success", new_onion
        if new_onion and new_onion == old_onion:
            time.sleep(POLL_INTERVAL)
            continue
        time.sleep(POLL_INTERVAL)

    new_onion = _read_hostname(hostname_path)
    if new_onion:
        if new_onion == old_onion:
            return "unchanged", new_onion
        return "success", new_onion
    return "timeout", ""


def rotate_single(app_id: str, hostname_path: str, current_onion: str, restart_app_id: str | None = None) -> dict:
    restart_app_id = restart_app_id or app_id
    error = validate_app_id(app_id) or validate_app_id(restart_app_id)
    if error:
        logger.warning(f"Validation failed for {app_id}: {error}")
        return {
            "app_id": app_id,
            "old_onion": current_onion,
            "new_onion": "",
            "status": "invalid_hostname",
            "message": error,
        }

    if not has_restart_target(restart_app_id):
        logger.warning(f"Refusing to delete hostname for {app_id}: no restartable containers found for {restart_app_id}")
        return {
            "app_id": app_id,
            "old_onion": current_onion,
            "new_onion": "",
            "status": "restart_failed",
            "message": "no_restartable_containers_found",
        }

    del_ok, del_msg = delete_hidden_service_keys(hostname_path)
    if not del_ok:
        return {
            "app_id": app_id,
            "old_onion": current_onion,
            "new_onion": "",
            "status": "delete_failed",
            "message": del_msg,
        }

    restart_ok, restart_msg = restart_app(restart_app_id)
    if not restart_ok:
        return {
            "app_id": app_id,
            "old_onion": current_onion,
            "new_onion": "",
            "status": "restart_failed",
            "message": restart_msg,
        }

    tor_ok, tor_msg = restart_tor_containers()
    if not tor_ok:
        logger.warning(f"Tor restart failed after rotating {app_id}: {tor_msg}")

    status, new_onion = wait_for_new_hostname(hostname_path, current_onion)

    return {
        "app_id": app_id,
        "old_onion": current_onion,
        "new_onion": new_onion,
        "status": status,
        "message": "",
    }
