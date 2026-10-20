import asyncio
import contextlib
import json
import tarfile
import time
from datetime import datetime, timezone

from .config import DATA


class Runtime:
    def __init__(self, config, broker):
        self.config, self.broker = config, broker
        self.process = None
        self.lock = asyncio.Lock()
        self.stopping = False
        self.paused = False
        self.last_exit_code = None

    async def start(self):
        directory = DATA / "simplex"
        directory.mkdir(parents=True, exist_ok=True)
        files = directory / "files"
        files.mkdir(exist_ok=True)
        args = ["/usr/local/bin/simplex-chat", "-d", str(directory / "simplex"),
                "-p", "5225", "--files-folder", str(files), "--auto-accept-files", "0", "-y"]
        if not list(directory.glob("*_chat.db")):
            args += ["--user-display-name", "SimpleX"]
        for key, flag in (("smp_servers", "--server"), ("xftp_servers", "--xftp-server"),
                          ("socks_proxy", "--socks-proxy")):
            if self.config.value.get(key):
                args += [flag, self.config.value[key]]
        # Do not write CLI stdout to container logs: it can contain messages and links.
        self.process = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)

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
        self.process = None

    async def supervise(self):
        while not self.stopping:
            async with self.lock:
                if not self.paused and (self.process is None or self.process.returncode is not None):
                    if self.process:
                        self.last_exit_code = self.process.returncode
                    await self.start()
            await asyncio.sleep(5)

    def archive(self):
        directory = DATA / "backups"
        directory.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        path = directory / (stamp + "-" + str(time.time_ns() % 1000000) + ".tar.gz")
        partial = path.with_suffix(".partial")
        try:
            with tarfile.open(partial, "w:gz", dereference=False) as archive:
                for name in ("simplex", "manager"):
                    archive.add(DATA / name, arcname=name, recursive=True)
                metadata = DATA / "backup-info.json"
                metadata.write_text(json.dumps({"app": "1.0.0", "simplex": "v7.0.3", "created_utc": stamp}))
                archive.add(metadata, arcname="backup-info.json")
                metadata.unlink()
            partial.replace(path)
            path.chmod(0o600)
        finally:
            with contextlib.suppress(FileNotFoundError):
                partial.unlink()
        return path.name

    async def backup(self):
        async with self.lock:
            self.paused = True
            try:
                await self.stop()
                return await asyncio.to_thread(self.archive)
            finally:
                self.paused = False
                await self.start()

    async def restart(self):
        async with self.lock:
            await self.stop()
            await self.start()
