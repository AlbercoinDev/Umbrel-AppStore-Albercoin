import os
import time
import logging
from typing import Optional
from config import APP_ID_REGEX, DRY_RUN, POLL_INTERVAL, POLL_TIMEOUT
from detector import _read_hostname, _is_safe_path
from restarter import has_restart_target, restart_app

logger = logging.getLogger("onion_rotator.rotator")


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


def rotate_single(app_id: str, hostname_path: str, current_onion: str) -> dict:
    error = validate_app_id(app_id)
    if error:
        logger.warning(f"Validation failed for {app_id}: {error}")
        return {
            "app_id": app_id,
            "old_onion": current_onion,
            "new_onion": "",
            "status": "invalid_hostname",
            "message": error,
        }

    if not has_restart_target(app_id):
        logger.warning(f"Refusing to delete hostname for {app_id}: no restartable containers found")
        return {
            "app_id": app_id,
            "old_onion": current_onion,
            "new_onion": "",
            "status": "restart_failed",
            "message": "no_restartable_containers_found",
        }

    del_ok, del_msg = delete_hostname(hostname_path)
    if not del_ok:
        return {
            "app_id": app_id,
            "old_onion": current_onion,
            "new_onion": "",
            "status": "delete_failed",
            "message": del_msg,
        }

    restart_ok, restart_msg = restart_app(app_id)
    if not restart_ok:
        return {
            "app_id": app_id,
            "old_onion": current_onion,
            "new_onion": "",
            "status": "restart_failed",
            "message": restart_msg,
        }

    status, new_onion = wait_for_new_hostname(hostname_path, current_onion)

    return {
        "app_id": app_id,
        "old_onion": current_onion,
        "new_onion": new_onion,
        "status": status,
        "message": "",
    }
