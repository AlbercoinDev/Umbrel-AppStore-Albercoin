"""Export and import per-network snapshots (e-lite index + Bitcoin Core)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import threading
import time
from pathlib import Path

from .config_writer import AppPaths
from .settings import NETWORKS, Settings
from .supervisor import SupervisorManager

ALLOWED_EXTRA_ROOT = Path("/exports")
MANIFEST_NAME = "e-lite-snapshot.json"
EXCLUDE_INDEX = {"rawtx-cache", "tor", "tls", "LOG", "LOCK", "e-lite.log", "e-lite.log.1", "banner.txt", "core-paused-by-e-lite"}
EXCLUDE_CORE = {"debug.log", ".cookie", "bitcoind.pid", "mempool.dat", "peers.dat", "banlist.dat", "anchors.dat"}


def _du(path: Path, exclude: set[str] | None = None) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not exclude or d not in exclude]
        for name in files:
            if exclude and name in exclude:
                continue
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def read_manifest(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.getmember(MANIFEST_NAME)
        handle = tar.extractfile(member)
        if handle is None:
            raise ValueError("snapshot manifest is not readable")
        return json.loads(handle.read().decode("utf-8", errors="replace"))


def _snapshot_files(destination: Path) -> list[dict]:
    items: list[dict] = []
    if not destination.is_dir():
        return items
    for entry in sorted(destination.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            manifest = read_manifest(entry)
        except Exception:
            continue
        core = manifest.get("core") or {}
        items.append(
            {
                "name": entry.name,
                "bytes": entry.stat().st_size,
                "network": manifest.get("network", ""),
                "height": manifest.get("height", 0),
                "hash": manifest.get("hash", ""),
                "format": manifest.get("format", 1),
                "has_core": bool(core),
                "core_tip": core.get("tip_height", 0),
                "created": entry.stat().st_mtime,
            }
        )
    return items


class BackupManager:
    """Run one export/import job at a time and publish its progress."""

    def __init__(self, paths: AppPaths, supervisor: SupervisorManager, settings_loader, log=None) -> None:
        self.paths = paths
        self.supervisor = supervisor
        self.settings_loader = settings_loader
        self.log = log or (lambda _message: None)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict = self._idle()

    @staticmethod
    def _idle() -> dict:
        return {
            "active": False,
            "ok": None,
            "action": "",
            "network": "",
            "phase": "idle",
            "message": "",
            "done": 0,
            "total": 0,
            "eta_seconds": None,
            "path": "",
            "error": "",
            "started_at": None,
            "finished_at": None,
            "log": [],
        }

    # ------------------------------------------------------------------ helpers
    def destinations(self) -> list[dict]:
        result = []
        for root in (ALLOWED_EXTRA_ROOT, self.paths.exports_dir):
            if not root.is_dir():
                continue
            try:
                probe = root / ".e-lite-write-test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                writable = True
            except OSError:
                writable = False
            result.append({"path": str(root), "label": str(root), "writable": writable})
        return result

    def default_destination(self) -> Path:
        for item in self.destinations():
            if item["writable"] and item["path"] == str(ALLOWED_EXTRA_ROOT):
                return ALLOWED_EXTRA_ROOT
        return self.paths.exports_dir

    def _allowed(self, destination: str) -> Path:
        target = Path(destination).resolve()
        for root in (ALLOWED_EXTRA_ROOT, self.paths.exports_dir):
            try:
                root_resolved = root.resolve()
            except OSError:
                continue
            if target == root_resolved or root_resolved in target.parents:
                return target
        raise ValueError("destino no permitido")

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def files(self, destination: str) -> list[dict]:
        try:
            return _snapshot_files(self._allowed(destination))
        except (ValueError, OSError):
            return []

    def _update(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def _phase(self, phase: str, message: str) -> None:
        self._update(phase=phase, message=message)
        self.log(f"backup: {phase}: {message}")

    def _active(self) -> bool:
        with self._lock:
            return bool(self._state.get("active"))

    # -------------------------------------------------------------------- start
    def start_export(self, network: str, destination: str) -> tuple[bool, str]:
        return self._start("export", network, destination, {})

    def start_import(self, network: str, destination: str, filename: str, verify: str = "basic", keep_previous: bool = False) -> tuple[bool, str]:
        return self._start("import", network, destination, {"filename": filename, "verify": verify, "keep_previous": keep_previous})

    def _start(self, action: str, network: str, destination: str, extra: dict) -> tuple[bool, str]:
        if network not in NETWORKS:
            return False, "red desconocida"
        if self._active():
            return False, "ya hay una copia en curso"
        try:
            target = self._allowed(destination)
        except ValueError as exc:
            return False, str(exc)
        target.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._state = self._idle()
            self._state.update({"active": True, "action": action, "network": network, "started_at": time.time(), "phase": "preparing", "message": "Preparando"})
        args = {"network": network, "destination": target, **extra}
        self._thread = threading.Thread(target=self._run, args=(action, args), name=f"backup-{action}", daemon=True)
        self._thread.start()
        return True, ""

    # --------------------------------------------------------------------- run
    def _run(self, action: str, args: dict) -> None:
        network = args["network"]
        enabled = False
        try:
            settings = self.settings_loader()
            enabled = settings.network(network).enabled
        except Exception:
            enabled = False
        try:
            if action == "export":
                self._run_export(args, enabled)
            else:
                self._run_import(args, enabled)
        except Exception as exc:  # noqa: BLE001
            self._update(active=False, ok=False, phase="error", error=str(exc), message="Error", finished_at=time.time())
            self.log(f"backup: error: {exc}")
            if enabled:
                self.supervisor.resume(network)
        finally:
            with self._lock:
                self._state["active"] = False
                if self._state.get("finished_at") is None:
                    self._state["finished_at"] = time.time()

    def _stop(self, network: str, enabled: bool) -> None:
        if enabled:
            self._phase("stopping", "Parando la red")
            self.supervisor.suspend(network)

    def _restart(self, network: str, enabled: bool) -> None:
        if enabled:
            self._phase("starting", "Reactivando la red")
            self.supervisor.resume(network)

    def _run_export(self, args: dict, enabled: bool) -> None:
        network = args["network"]
        destination: Path = args["destination"]
        config_file = self.paths.network_config_file(network)
        if not config_file.is_file():
            raise RuntimeError("la red aún no tiene configuración; iníciala primero")
        index_dir = self.paths.network_e_lite_data(network)
        core_dir = self.paths.network_core_data(network)
        index_bytes = _du(index_dir, EXCLUDE_INDEX)
        core_bytes = _du(core_dir, EXCLUDE_CORE)
        estimate = int(index_bytes * 0.55 + core_bytes * 0.95) + 1024 * 1024
        output = destination / f"e-lite-{network}-{time.strftime('%Y%m%d-%H%M%S')}Z.tar.gz"
        self._update(total=estimate, path=str(output))
        self._stop(network, enabled)
        self._phase("exporting", "Exportando índice y Core")
        command = [
            str(self.paths.runtime / f"e-lite-{_elite_version()}" / "e-lite"),
            "export-snapshot",
            "--config", str(config_file),
            "--with-core",
            "--hash-files",
            "--compress-level", "3",
            "--output", str(output),
        ]
        self._run_process(command, self._progress_export, output)
        size = output.stat().st_size if output.is_file() else 0
        self._update(ok=True, phase="done", message="Copia completada", done=size, total=size, eta_seconds=0, finished_at=time.time())
        self.log(f"backup: exported {output} ({size} bytes)")
        self._restart(network, enabled)

    def _progress_export(self, output: Path) -> None:
        with self._lock:
            total = int(self._state.get("total") or 0)
        tmp = output.with_name(output.name + ".tmp")
        done = tmp.stat().st_size if tmp.exists() else (output.stat().st_size if output.exists() else 0)
        self._set_progress(done, total)

    def _run_import(self, args: dict, enabled: bool) -> None:
        network = args["network"]
        destination: Path = args["destination"]
        archive = destination / os.path.basename(args["filename"])
        if not archive.is_file():
            raise RuntimeError("el archivo seleccionado no existe")
        manifest = read_manifest(archive)
        if manifest.get("network") != network:
            raise RuntimeError(f"la copia pertenece a {manifest.get('network')}, no a {network}")
        core_section = manifest.get("core") or {}
        total = int(core_section.get("bytes") or 0) + int(archive.stat().st_size * 2)
        self._update(total=max(total, archive.stat().st_size), path=str(archive))
        self._stop(network, enabled)
        target = self.paths.network_e_lite_data(network)
        core_target = self.paths.network_core_data(network)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        previous_index = target.with_name(f"{target.name}.pre-import-{stamp}")
        previous_core = core_target.with_name(f"{core_target.name}.pre-import-{stamp}")
        if target.exists():
            os.replace(target, previous_index)
        if core_section and core_target.exists():
            os.replace(core_target, previous_core)
        self._phase("extracting", "Restaurando el snapshot")
        command = [
            str(self.paths.runtime / f"e-lite-{_elite_version()}" / "e-lite"),
            "import-snapshot",
            "--config", str(self.paths.network_config_file(network)),
            "--input", str(archive),
        ]
        if args.get("verify") == "basic":
            command += ["--verify", "basic"]
        elif args.get("verify") == "full":
            command += ["--verify", "full"]
        try:
            self._run_process(command, self._progress_import, target)
            restored_core = target / "bitcoin"
            if core_section:
                if not restored_core.is_dir():
                    raise RuntimeError("el snapshot no restauró el datadir de Core")
                if core_target.exists():
                    shutil.rmtree(core_target, ignore_errors=True)
                os.replace(restored_core, core_target)
            if not args.get("keep_previous"):
                shutil.rmtree(previous_index, ignore_errors=True)
                shutil.rmtree(previous_core, ignore_errors=True)
        except Exception:
            # Roll back to the previous data if the import failed.
            if previous_index.exists():
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                os.replace(previous_index, target)
            if core_section and previous_core.exists() and not core_target.exists():
                os.replace(previous_core, core_target)
            raise
        self._update(ok=True, phase="done", message=f"Restaurado a altura {manifest.get('height', 0)}", done=int(self._state.get("total") or 0), eta_seconds=0, finished_at=time.time())
        self.log(f"backup: imported {archive} (height {manifest.get('height')})")
        self._restart(network, enabled)

    def _progress_import(self, target: Path) -> None:
        with self._lock:
            total = int(self._state.get("total") or 0)
        done = _du(target) if target.exists() else 0
        self._set_progress(done, total)

    def _run_process(self, command: list[str], progress, target: Path) -> None:
        env = os.environ.copy()
        env["HOME"] = str(self.paths.data)
        env["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
        lines: list[str] = []
        reader = threading.Thread(target=lambda: self._drain(process, lines), daemon=True)
        reader.start()
        while process.poll() is None:
            progress(target)
            time.sleep(1)
        reader.join(timeout=5)
        self._update(log=lines[-40:])
        if process.returncode != 0:
            raise RuntimeError(" ".join(lines[-3:]) or f"el comando terminó con código {process.returncode}")

    @staticmethod
    def _drain(process: subprocess.Popen[str], lines: list[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            lines.append(line.rstrip("\n"))
            del lines[:-80]

    def _set_progress(self, done: int, total: int) -> None:
        now = time.time()
        with self._lock:
            started = self._state.get("started_at") or now
            elapsed = max(0.001, now - started)
            rate = done / elapsed
            eta = int(max(0, total - done) / rate) if rate > 0 and total > done else 0
            self._state.update(done=done, total=total, eta_seconds=eta)


def _elite_version() -> str:
    from .runtime import E_LITE_VERSION
    return E_LITE_VERSION
