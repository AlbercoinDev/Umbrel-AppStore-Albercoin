"""Backup inventory: only application-generated regular files, ten day retention."""
import re
import shutil
import time
import subprocess
from datetime import datetime, timezone

RETENTION = 10 * 86400
NAME = re.compile(r"([0-9]{8}T[0-9]{6})-[0-9]+\.tar\.gz(?:\.age)?$")


def validate_recipient(recipient):
    """Let age verify the checksum too, not just the key's character set."""
    try:
        subprocess.run(["age", "-r", recipient], input=b"", stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("No se pudo validar la clave pública age. Comprueba la clave y que age esté instalado.") from exc


def inventory(directory, now=None, prune=True):
    now = time.time() if now is None else now
    result = []
    for path in directory.iterdir():
        match = NAME.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_file():
            continue
        try:
            created = datetime.strptime(match[1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        expires = created + RETENTION
        if expires < now:
            if prune:
                path.unlink()
            continue
        result.append({"name": path.name, "size": path.stat().st_size, "expires": expires})
    return sorted(result, key=lambda item: item["name"], reverse=True)


def storage(data):
    def size(directory):
        return sum(p.stat().st_size for p in directory.rglob("*") if not p.is_symlink() and p.is_file())
    return {"free": shutil.disk_usage(data).free,
            "data": size(data / "simplex") + size(data / "manager"),
            "backups": size(data / "backups")}
