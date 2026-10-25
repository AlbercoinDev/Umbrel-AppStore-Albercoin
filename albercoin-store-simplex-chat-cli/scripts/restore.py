#!/usr/bin/env python3
"""Offline restore. Stop the app in Umbrel before executing this script."""
import argparse
import json
import os
import shutil
import tarfile
import tempfile
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--identity", type=Path, help="Clave privada age para copias .age; requiere age instalado")
    parser.add_argument("--confirm", required=True, choices=["APP-STOPPED-RESTORE"])
    args = parser.parse_args()
    data, archive_path = args.data.resolve(), args.archive.resolve()
    if data.name != "data" or data.parent.name != "albercoin-store-simplex-chat-cli":
        raise SystemExit("La ruta debe terminar en albercoin-store-simplex-chat-cli/data.")
    if not data.is_dir():
        raise SystemExit("No existe el directorio de datos de la app.")
    os.umask(0o077)
    staging = Path(tempfile.mkdtemp(prefix=".simplex-restore-", dir=data))
    decrypted = None
    try:
        if archive_path.name.endswith(".age"):
            if args.identity is None:
                raise SystemExit("Indica --identity con tu clave privada age (guardada fuera del nodo).")
            if archive_path.stat().st_size > shutil.disk_usage(data).free - 256 * 1024 * 1024:
                raise SystemExit("Espacio insuficiente para descifrar la copia temporalmente.")
            decrypted = staging / "decrypted.tar.gz"
            subprocess.run(["age", "--decrypt", "-i", str(args.identity), "-o", str(decrypted), str(archive_path)], check=True)
            archive_path = decrypted
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > 200000:
                raise SystemExit("Archivo con demasiadas entradas.")
            size = sum(m.size for m in members)
            if size > shutil.disk_usage(data).free - 256 * 1024 * 1024:
                raise SystemExit("Espacio insuficiente para restaurar conservando los datos anteriores.")
            for member in members:
                parts = PurePosixPath(member.name)
                if parts.is_absolute() or ".." in parts.parts or "\\" in member.name or not parts.parts:
                    raise SystemExit("Archivo con rutas no permitidas.")
                if parts.parts[0] not in ("simplex", "manager", "backup-info.json"):
                    raise SystemExit("El archivo no corresponde a esta app.")
                if not member.isfile() and not member.isdir():
                    raise SystemExit("El archivo contiene enlaces o archivos especiales no permitidos.")
                target = staging.joinpath(*parts.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise SystemExit("Entrada ilegible.")
                    with source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
            info = json.loads((staging / "backup-info.json").read_text())
            if info.get("app") not in ("1.0.0", "1.0.1", "1.0.2", "1.0.3", "1.0.4") or info.get("simplex") != "v7.0.3":
                raise SystemExit("Versión de copia no compatible con esta restauración.")
        if not (staging / "manager/admin-auth.json").is_file() or not list((staging / "simplex").glob("*_chat.db")):
            raise SystemExit("Copia incompleta: faltan las bases de datos o la configuración.")
        # Preserve the current contents for recovery, then replace both directories.
        previous = data / ("before-restore-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
        previous.mkdir()
        moved = []
        installed = []
        try:
            for name in ("simplex", "manager"):
                if (data / name).exists():
                    (data / name).rename(previous / name)
                    moved.append(name)
                (staging / name).rename(data / name)
                installed.append(name)
        except BaseException:
            for name in reversed(installed):
                (data / name).rename(staging / name)
            for name in reversed(moved):
                (previous / name).rename(data / name)
            raise
        print("Restaurado. Datos anteriores conservados en " + str(previous))
        print("Inicia la app en Umbrel. Se recuperan también su contraseña y credenciales anteriores.")
    finally:
        # staging is created by mkdtemp inside the explicitly validated app volume.
        if staging.parent == data and staging.name.startswith(".simplex-restore-"):
            shutil.rmtree(staging)


if __name__ == "__main__":
    main()
