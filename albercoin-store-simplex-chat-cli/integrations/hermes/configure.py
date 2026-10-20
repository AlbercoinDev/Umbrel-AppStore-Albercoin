#!/usr/bin/env python3
"""Run inside the official Hermes Umbrel container as its UID 1000 user.

Reads the exported JSON on stdin. Never prints credentials. No network probes.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def main():
    os.umask(0o077)
    home = Path(os.environ.get("HERMES_HOME", "/opt/data")).resolve()
    if not home.is_dir() or str(home) != "/opt/data":
        raise SystemExit("Este asistente está destinado al paquete oficial de Hermes en Umbrel (/opt/data).")
    if importlib.util.find_spec("websockets") is None:
        raise SystemExit("Falta websockets en el Python del gateway. Sigue el README antes de aplicar la configuración.")
    hermes = Path(sys.executable).parent / "hermes"
    if not hermes.is_file():
        raise SystemExit("Ejecuta el asistente con /opt/hermes/.venv/bin/python.")
    body = sys.stdin.read(65537)
    if len(body) > 65536:
        raise SystemExit("Archivo demasiado grande.")
    values = json.loads(body)
    keys = {"SIMPLEX_WS_URL", "SIMPLEX_ALLOWED_USERS", "SIMPLEX_ALLOW_ALL_USERS", "SIMPLEX_AUTO_ACCEPT",
            "SIMPLEX_GROUP_ALLOWED", "SIMPLEX_HOME_CHANNEL"}
    if not isinstance(values, dict) or set(values) != keys or not all(isinstance(v, str) for v in values.values()):
        raise SystemExit("El archivo no tiene el formato exportado por SimpleX Chat CLI.")
    url = urlsplit(values["SIMPLEX_WS_URL"])
    if url.scheme not in ("ws", "wss") or not url.hostname or not url.username or not url.password:
        raise SystemExit("URL de conector inválida.")
    if any("\n" in value or "\r" in value for value in values.values()):
        raise SystemExit("Valores no válidos.")
    if not re.fullmatch(r"[1-9][0-9]*(,[1-9][0-9]*)*", values["SIMPLEX_ALLOWED_USERS"]):
        raise SystemExit("Se requiere una lista de contactos numéricos autorizados.")
    if values["SIMPLEX_ALLOW_ALL_USERS"] != "false" or values["SIMPLEX_AUTO_ACCEPT"] != "false" or values["SIMPLEX_GROUP_ALLOWED"]:
        raise SystemExit("Este asistente requiere aceptación manual y grupos desactivados.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    backup = home / "simplex-config-backups" / stamp
    backup.mkdir(parents=True, mode=0o700)
    manifest = {"files": {}}
    for name in (".env", "config.yaml"):
        path = home / name
        if path.is_symlink():
            raise SystemExit("No se modifican archivos de configuración que sean enlaces simbólicos.")
        manifest["files"][name] = path.exists()
        if path.exists():
            shutil.copy2(path, backup / name)
            (backup / name).chmod(0o600)
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # Use Hermes' own plugin management instead of rewriting its YAML schema.
    subprocess.run([str(hermes), "plugins", "enable", "simplex-platform"], check=True,
                   cwd="/opt/hermes", stdout=subprocess.DEVNULL)
    path = home / ".env"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = []
    for line in old.splitlines():
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=", line)
        if not match or match.group(1) not in keys:
            lines.append(line)
    lines += ["", "# SimpleX Chat CLI for Umbrel — managed connector settings"]
    lines += [key + "=" + json.dumps(value) for key, value in values.items()]
    temp = path.with_name(".env.simplex-tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)
    print("Configuración aplicada. Copia previa en " + str(backup))
    print("Reinicia Hermes desde Umbrel para que el gateway lea los cambios.")


if __name__ == "__main__":
    main()
