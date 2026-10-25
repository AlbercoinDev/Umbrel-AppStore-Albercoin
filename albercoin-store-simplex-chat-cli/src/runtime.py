import asyncio
import contextlib
import json
import io
import tarfile
import time
import re
import subprocess
from datetime import datetime, timezone

from .config import DATA
from .network import network_args
from .backups import inventory, storage


class Runtime:
    def __init__(self, config, broker):
        self.config, self.broker = config, broker
        self.process = None
        self.lock = asyncio.Lock()
        self.stopping = False
        self.paused = False
        self.last_exit_code = None
        self.stderr_task = None

    def log(self, level, phase, summary, **context):
        self.broker.log(level, phase, summary, **context)

    async def read_errors(self, stream):
        # stderr can include private data: retain only recognised failure categories.
        patterns = {"permission denied": "Permiso denegado", "no space left": "Disco lleno",
                    "unrecognized option": "Argumento de arranque incompatible", "invalid option": "Argumento de arranque incompatible",
                    "database is locked": "Base de datos bloqueada", "address already in use": "Puerto ocupado",
                    "database disk image is malformed": "Base de datos dañada", "migration": "Aviso de migración"}
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            lower = chunk.decode(errors="replace").lower()
            matched = [label for pattern, label in patterns.items() if pattern in lower]
            self.log("error", "process_stderr", "Salida de error del proceso (texto privado omitido).", categories=matched or ["Error no clasificado"])

    async def start(self):
        directory = DATA / "simplex"
        directory.mkdir(parents=True, exist_ok=True)
        files = directory / "files"
        files.mkdir(exist_ok=True)
        args = ["/usr/local/bin/simplex-chat", "-d", str(directory / "simplex"),
                "-p", "5225", "--files-folder", str(files), "--auto-accept-files", "0", "-y"]
        if not list(directory.glob("*_chat.db")):
            args += ["--user-display-name", "SimpleX"]
        args += network_args(self.config.value)
        # Do not write CLI stdout to container logs: it can contain messages and links.
        self.process = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE)
        self.stderr_task = asyncio.create_task(self.read_errors(self.process.stderr))
        self.log("info", "process_start", "Proceso SimpleX iniciado.", pid=self.process.pid)

    async def stop(self):
        await self.broker.disconnect()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 25)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.process:
            self.last_exit_code = self.process.returncode
            self.log("info", "process_stop", "Proceso SimpleX detenido.", exit_code=self.last_exit_code)
        if self.stderr_task:
            await self.stderr_task
            self.stderr_task = None
        self.process = None

    async def supervise(self):
        while not self.stopping:
            async with self.lock:
                if not self.paused and (self.process is None or self.process.returncode is not None):
                    if self.process:
                        self.last_exit_code = self.process.returncode
                        self.log("error", "process_exit", "SimpleX terminó; se intentará reiniciar. Revisa los errores de arranque.", exit_code=self.last_exit_code)
                        if self.stderr_task:
                            await self.stderr_task
                    try:
                        await self.start()
                    except Exception as exc:
                        self.last_exit_code = -1
                        self.log("error", "process_start_failed", "No se pudo iniciar SimpleX.", error_type=type(exc).__name__, errno=getattr(exc, "errno", None))
            await asyncio.sleep(5)

    def archive(self):
        directory = DATA / "backups"
        directory.mkdir(exist_ok=True)
        usage = storage(DATA)
        if usage["free"] < usage["data"] * (2 if self.config.value.get("backup_recipient") else 1) + 256 * 1024 * 1024:
            raise ValueError("Espacio insuficiente para una copia (estimación conservadora).")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        path = directory / (stamp + "-" + str(time.time_ns() % 1000000) + ".tar.gz")
        partial = path.with_suffix(".partial")
        try:
            with tarfile.open(partial, "w:gz", dereference=False) as archive:
                for name in ("simplex", "manager"):
                    archive.add(DATA / name, arcname=name, recursive=True)
                metadata = json.dumps({"app": "1.0.4", "simplex": "v7.0.3", "created_utc": stamp}).encode()
                info = tarfile.TarInfo("backup-info.json")
                info.size, info.mode, info.mtime = len(metadata), 0o600, int(time.time())
                archive.addfile(info, io.BytesIO(metadata))
            partial.chmod(0o600)
            recipient = self.config.value.get("backup_recipient", "")
            if recipient:
                if not re.fullmatch(r"age1[0-9a-z]{58}", recipient):
                    raise ValueError("Destinatario age no válido.")
                encrypted = path.with_name(path.name + ".age")
                encrypted_partial = encrypted.with_suffix(".partial")
                try:
                    subprocess.run(["age", "-r", recipient, "-o", str(encrypted_partial), str(partial)],
                                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    encrypted_partial.chmod(0o600)
                    encrypted_partial.replace(encrypted)
                    path = encrypted
                finally:
                    encrypted_partial.unlink(missing_ok=True)
            else:
                partial.replace(path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                partial.unlink()
        return path.name

    async def maintain_backups(self):
        while True:
            try:
                await asyncio.to_thread(inventory, DATA / "backups")
                hours = self.config.value.get("backup_interval", 0)
                last = self.config.value.get("backup_last", time.time())
                if hours and time.time() - last >= hours * 3600:
                    await self.backup()
            except Exception as exc:
                self.log("error", "backup_maintenance", "Falló el mantenimiento de copias.", error_type=type(exc).__name__)
            await asyncio.sleep(60)

    async def backup(self):
        async with self.lock:
            self.paused = True
            try:
                await self.stop()
                name = await asyncio.to_thread(self.archive)
                self.config.value["backup_last"] = time.time()
                self.config.save()
                return name
            finally:
                self.paused = False
                await self.start()

    async def restart(self):
        async with self.lock:
            await self.stop()
            await self.start()
