import logging
import os
import shutil
import time
from typing import Callable, Optional

from config import APP_ID_REGEX, DRY_RUN, POLL_INTERVAL, POLL_TIMEOUT
from detector import _is_safe_path, _read_hostname
from restarter import has_restart_target, restart_app, restart_app_tor_server

logger = logging.getLogger("onion_rotator.rotator")

ProgressCallback = Callable[[str, str, str, dict | None], None]


def validate_app_id(app_id: str) -> Optional[str]:
    if not APP_ID_REGEX.match(app_id):
        return f"invalid_app_id: {app_id}"
    return None


def _service_dir_from_hostname(hostname_path: str) -> str:
    return os.path.dirname(hostname_path)


def _validate_service_dir(service_id: str, service_dir: str) -> tuple[bool, str]:
    error = validate_app_id(service_id)
    if error:
        return False, error
    if not _is_safe_path(service_dir):
        return False, "path_traversal_blocked"
    if os.path.islink(service_dir):
        return False, "symlink_blocked"
    if os.path.basename(service_dir) != f"app-{service_id}":
        return False, "service_dir_name_mismatch"
    if not os.path.isdir(service_dir):
        return False, "service_dir_not_found"
    return True, "ok"


def delete_hostname(hostname_path: str) -> tuple[bool, str]:
    if not _is_safe_path(hostname_path):
        return False, "path_traversal_blocked"
    if DRY_RUN:
        logger.info(f"DRY RUN: Would delete {hostname_path}")
        return True, "dry_run"
    try:
        if not os.path.isfile(hostname_path):
            return False, "file_not_found"
        if os.path.islink(hostname_path):
            return False, "symlink_blocked"
        os.remove(hostname_path)
        logger.info(f"Deleted hostname: {hostname_path}")
        return True, "deleted"
    except PermissionError:
        logger.error(f"Permission denied deleting {hostname_path}")
        return False, "permission_denied"
    except OSError as e:
        logger.error(f"Error deleting {hostname_path}: {e}")
        return False, f"delete_error: {e}"


def delete_hidden_service_dir(service_id: str, service_dir: str) -> tuple[bool, str]:
    ok, message = _validate_service_dir(service_id, service_dir)
    if not ok:
        return False, message

    if DRY_RUN:
        logger.info(f"DRY RUN: Would delete hidden service directory {service_dir}")
        return True, "dry_run"

    try:
        shutil.rmtree(service_dir)
        logger.info(f"Deleted hidden service directory: {service_dir}")
        return True, "deleted_service_dir"
    except PermissionError:
        logger.error(f"Permission denied deleting hidden service directory {service_dir}")
        return False, "permission_denied"
    except OSError as e:
        logger.error(f"Error deleting hidden service directory {service_dir}: {e}")
        return False, f"delete_error: {e}"


def delete_hidden_service_keys(hostname_path: str) -> tuple[bool, str]:
    service_dir = _service_dir_from_hostname(hostname_path)
    service_id = os.path.basename(service_dir).removeprefix("app-")
    return delete_hidden_service_dir(service_id, service_dir)


def wait_for_new_hostname(hostname_path: str, old_onion: str, timeout: int = POLL_TIMEOUT) -> tuple[str, str]:
    if DRY_RUN:
        return "success", "a" * 56 + ".onion"

    deadline = time.time() + timeout
    while time.time() < deadline:
        new_onion = _read_hostname(hostname_path)
        if new_onion and new_onion != old_onion:
            return "success", new_onion
        time.sleep(POLL_INTERVAL)

    new_onion = _read_hostname(hostname_path)
    if new_onion:
        return ("unchanged" if new_onion == old_onion else "success"), new_onion
    return "timeout", ""


def rotate_single(app_id: str, hostname_path: str, current_onion: str, restart_app_id: str | None = None) -> dict:
    service_id = app_id
    restart_app_id = restart_app_id or app_id
    error = validate_app_id(service_id) or validate_app_id(restart_app_id)
    if error:
        logger.warning(f"Validation failed for {service_id}: {error}")
        return {"app_id": service_id, "old_onion": current_onion, "new_onion": "", "status": "invalid_hostname", "message": error}

    if not has_restart_target(restart_app_id):
        logger.warning(f"Refusing to delete {service_id}: no restartable containers found for {restart_app_id}")
        return {"app_id": service_id, "old_onion": current_onion, "new_onion": "", "status": "restart_failed", "message": "no_restartable_containers_found"}

    service_dir = _service_dir_from_hostname(hostname_path)
    del_ok, del_msg = delete_hidden_service_dir(service_id, service_dir)
    if not del_ok:
        return {"app_id": service_id, "old_onion": current_onion, "new_onion": "", "status": "delete_failed", "message": del_msg}

    restart_ok, restart_msg = restart_app_tor_server(restart_app_id)
    if not restart_ok:
        restart_ok, restart_msg = restart_app(restart_app_id)
    if not restart_ok:
        return {"app_id": service_id, "old_onion": current_onion, "new_onion": "", "status": "restart_failed", "message": restart_msg}

    status, new_onion = wait_for_new_hostname(hostname_path, current_onion)
    if status == "timeout":
        restart_app(restart_app_id)
        status, new_onion = wait_for_new_hostname(hostname_path, current_onion, timeout=45)
    return {"app_id": service_id, "old_onion": current_onion, "new_onion": new_onion, "status": status, "message": ""}


def rotate_app_services(app: dict, selected_service_ids: list[str], progress: ProgressCallback | None = None) -> dict:
    app_id = app["app_id"]
    selected = [s for s in app.get("services", []) if s["service_id"] in selected_service_ids]
    result = {"app_id": app_id, "status": "queued", "services": []}

    def emit(status: str, message: str = "", extra: dict | None = None):
        if progress:
            progress(app_id, status, message, extra)

    if not selected:
        result["status"] = "skipped"
        return result

    if not has_restart_target(app_id):
        result["status"] = "restart_failed"
        for service in selected:
            result["services"].append({
                "service_id": service["service_id"],
                "old_onion": service.get("onion_address", ""),
                "new_onion": "",
                "status": "restart_failed",
                "message": "no_restartable_containers_found",
            })
        emit("error", "no_restartable_containers_found", {"services": result["services"]})
        return result

    emit("deleting", "Deleting selected hidden service directories")
    for service in selected:
        ok, message = delete_hidden_service_dir(service["service_id"], service["service_dir"])
        result["services"].append({
            "service_id": service["service_id"],
            "old_onion": service.get("onion_address", ""),
            "new_onion": "",
            "status": "deleted" if ok else "delete_failed",
            "message": message,
        })
        if not ok:
            result["status"] = "delete_failed"
            emit("error", message, {"services": result["services"]})
            return result

    emit("restarting", "Restarting app Tor service")
    restart_ok, restart_msg = restart_app_tor_server(app_id)
    if not restart_ok:
        emit("restarting", "tor_server restart failed; restarting all app containers")
        restart_ok, restart_msg = restart_app(app_id)
    if not restart_ok:
        result["status"] = "restart_failed"
        for service_result in result["services"]:
            service_result["status"] = "restart_failed"
            service_result["message"] = restart_msg
        emit("error", restart_msg, {"services": result["services"]})
        return result

    if DRY_RUN:
        for service_result in result["services"]:
            service_result["new_onion"] = "a" * 56 + ".onion"
            service_result["status"] = "success"
            service_result["message"] = "dry_run"
        result["status"] = "ready"
        emit("ready", "Dry run finished", {"services": result["services"]})
        return result

    emit("waiting_onion", "Waiting for new onion addresses")
    remaining = {s["service_id"]: s for s in selected}
    deadline = time.time() + POLL_TIMEOUT
    while remaining and time.time() < deadline:
        for service_id, service in list(remaining.items()):
            new_onion = _read_hostname(service["hostname_path"])
            if new_onion and new_onion != service.get("onion_address", ""):
                for service_result in result["services"]:
                    if service_result["service_id"] == service_id:
                        service_result["new_onion"] = new_onion
                        service_result["status"] = "success"
                remaining.pop(service_id)
        emit("waiting_onion", "Waiting for new onion addresses", {"services": result["services"]})
        if remaining:
            time.sleep(POLL_INTERVAL)

    if remaining:
        emit("restarting", "New onion not detected; restarting all app containers")
        restart_app(app_id)
        deadline = time.time() + 45
        while remaining and time.time() < deadline:
            for service_id, service in list(remaining.items()):
                new_onion = _read_hostname(service["hostname_path"])
                if new_onion and new_onion != service.get("onion_address", ""):
                    for service_result in result["services"]:
                        if service_result["service_id"] == service_id:
                            service_result["new_onion"] = new_onion
                            service_result["status"] = "success"
                    remaining.pop(service_id)
            emit("waiting_onion", "Waiting for new onion addresses", {"services": result["services"]})
            if remaining:
                time.sleep(POLL_INTERVAL)

    for service_id in remaining:
        for service_result in result["services"]:
            if service_result["service_id"] == service_id:
                current = _read_hostname(next(s for s in selected if s["service_id"] == service_id)["hostname_path"])
                service_result["new_onion"] = current or ""
                service_result["status"] = "unchanged" if current == service_result["old_onion"] else "timeout"
                service_result["message"] = "timeout_waiting_for_new_onion"

    result["status"] = "ready" if all(s["status"] == "success" for s in result["services"]) else "error"
    emit(result["status"], "App rotation finished", {"services": result["services"]})
    return result
