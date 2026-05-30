import logging
import os
import shutil

import config
import detector

logger = logging.getLogger("onion_rotator.system_onions")

SYSTEM_ONIONS = {
    "web": {
        "name": "Umbrel Web Dashboard",
        "description": "Umbrel dashboard Onion address",
    },
    "auth": {
        "name": "Umbrel Auth Service",
        "description": "Umbrel app authentication Onion address",
    },
}


def _system_dir(service_id: str) -> str:
    return os.path.join(config.TOR_DATA_DIR, service_id)


def _validate_system_service(service_id: str) -> tuple[bool, str, str]:
    if service_id not in SYSTEM_ONIONS:
        return False, "invalid_system_service", ""
    service_dir = _system_dir(service_id)
    if os.path.basename(service_dir) != service_id:
        return False, "service_dir_name_mismatch", ""
    if not detector._is_safe_path(service_dir):
        return False, "path_traversal_blocked", ""
    if os.path.islink(service_dir):
        return False, "symlink_blocked", ""
    return True, "ok", service_dir


def scan_system_onions() -> list[dict]:
    services = []
    for service_id, meta in SYSTEM_ONIONS.items():
        ok, message, service_dir = _validate_system_service(service_id)
        hostname_path = os.path.join(service_dir, "hostname") if service_dir else ""
        onion = detector._read_hostname(hostname_path) if ok else None
        services.append({
            "id": service_id,
            "name": meta["name"],
            "description": meta["description"],
            "tor_dir": service_id,
            "onion_address": onion or "",
            "status": "available" if onion else ("missing" if ok else message),
        })
    return services


def delete_system_onion_contents(service_id: str) -> dict:
    ok, message, service_dir = _validate_system_service(service_id)
    if not ok:
        return {"id": service_id, "status": "error", "message": message}
    if not os.path.isdir(service_dir):
        return {"id": service_id, "status": "missing", "message": "directory_not_found"}

    if config.DRY_RUN:
        logger.info(f"DRY RUN: Would delete contents of system Onion directory {service_dir}")
        return {"id": service_id, "status": "deleted", "message": "dry_run"}

    deleted = []
    try:
        for entry in os.listdir(service_dir):
            path = os.path.join(service_dir, entry)
            if not detector._is_safe_path(path):
                return {"id": service_id, "status": "error", "message": "path_traversal_blocked"}
            if os.path.islink(path):
                return {"id": service_id, "status": "error", "message": "symlink_blocked"}
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            deleted.append(entry)
        logger.info(f"Deleted system Onion contents for {service_id}: {', '.join(deleted) or 'empty'}")
        return {
            "id": service_id,
            "status": "deleted",
            "message": "restart_umbrel_server_to_regenerate",
            "deleted": deleted,
        }
    except PermissionError:
        logger.error(f"Permission denied deleting system Onion contents for {service_id}")
        return {"id": service_id, "status": "error", "message": "permission_denied"}
    except OSError as e:
        logger.error(f"Error deleting system Onion contents for {service_id}: {e}")
        return {"id": service_id, "status": "error", "message": f"delete_error: {e}"}


def delete_system_onions(service_ids: list[str]) -> list[dict]:
    return [delete_system_onion_contents(service_id) for service_id in service_ids]
