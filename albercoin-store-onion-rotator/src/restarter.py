import logging
import docker
from typing import Optional
from config import DRY_RUN, DEBUG

logger = logging.getLogger("onion_rotator.restarter")

_docker_client: Optional[docker.DockerClient] = None


def get_docker_client() -> Optional[docker.DockerClient]:
    global _docker_client
    if _docker_client is not None:
        return _docker_client
    try:
        _docker_client = docker.from_env()
        _docker_client.ping()
        return _docker_client
    except Exception as e:
        logger.error(f"Failed to connect to Docker: {e}")
        return None


def is_docker_accessible() -> bool:
    client = get_docker_client()
    return client is not None


def restart_app(app_id: str) -> tuple[bool, str]:
    if DRY_RUN:
        logger.info(f"DRY RUN: Would restart app {app_id}")
        return True, "dry_run"

    client = get_docker_client()
    if client is None:
        return False, "docker_not_available"

    try:
        containers_found = False
        for container in client.containers.list(all=True):
            labels = container.labels or {}
            project = labels.get("com.docker.compose.project", "")
            umbrel_app = labels.get("app", "")

            if project == app_id or umbrel_app == app_id:
                if DEBUG:
                    logger.debug(f"Restarting container {container.name} for app {app_id}")
                container.restart()
                containers_found = True

        if not containers_found:
            logger.warning(f"No containers found for app {app_id} using standard labels")
            return False, "no_containers_found"

        logger.info(f"Restarted {containers_found} container(s) for app {app_id}")
        return True, f"restarted_{containers_found}_containers"

    except docker.errors.APIError as e:
        logger.error(f"Docker API error restarting {app_id}: {e}")
        return False, f"docker_error: {e}"
    except Exception as e:
        logger.error(f"Unexpected error restarting {app_id}: {e}")
        return False, f"error: {e}"
