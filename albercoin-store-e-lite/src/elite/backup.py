"""Export and import per-network snapshots (e-lite index + Bitcoin Core)."""

from __future__ import annotations

import json
import os
import shutil
import signal
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
EXPORT_TIMEOUT_SECONDS = 6 * 3600
IMPORT_TIMEOUT_SECONDS = 4 * 3600
SPACE_MARGIN_BYTES = 256 * 1024 * 1024


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


def free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def same_device(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def export_tmp_candidates(output: Path) -> tuple[Path, ...]:
    """e-lite writes `<output>.tmp` via Rust `with_extension("tmp")`.

    For `foo.tar.gz` that yields `foo.tar.tmp`; we also probe the more
    intuitive `foo.tar.gz.tmp` so a future change cannot silently break the
    progress bar again.
    """
    return output.with_suffix(".tmp"), output.with_name(output.name + ".tmp")


def cleanup_tmp_files(output: Path) -> None:
    for candidate in export_tmp_candidates(output):
        try:
            if candidate.exists():
                candidate.unlink()
        except OSError:
            pass


def read_manifest(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.getmember(MANIFEST_NAME)
        handle = tar.extractfile(member)
        if handle is None:
            raise ValueError("snapshot manifest is not readable")
        return json.loads(handle.read().decode("utf-8", errors="replace"))


def restored_estimate(manifest: dict, archive_bytes: int) -> int:
    """Bytes the restore will need on the data disk (index + Core)."""
    core = manifest.get("core") or {}
    core_bytes = int(core.get("bytes") or 0)
    if core_bytes:
        index_bytes = max(0, archive_bytes - core_bytes) * 2
        return core_bytes + index_bytes
    return archive_bytes * 2


def network_leftovers(paths: AppPaths, network: str) -> list[Path]:
    """`.pre-import-*` directories left by a previous import, per network."""
    root = paths.network_root(network)
    if not root.is_dir():
        return []
    prefixes = (
        f"{paths.network_e_lite_data(network).name}.pre-import-",
        f"{paths.network_core_data(network).name}.pre-import-",
    )
    return sorted((entry for entry in root.iterdir() if entry.name.startswith(prefixes)), key=lambda p: p.name)


def _ratio_file(paths: AppPaths) -> Path:
    return paths.config / "backup-stats.json"


def load_ratios(paths: AppPaths) -> dict:
    try:
        data = json.loads(_ratio_file(paths).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_ratio(paths: AppPaths, network: str, index_ratio: float) -> None:
    try:
        data = load_ratios(paths)
        data[network] = round(min(1.0, max(0.05, index_ratio)), 3)
        _ratio_file(paths).write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


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
            "tail": [],
            "phases": [],
            "phase_started_at": None,
            "warnings": [],
            "no_core": False,
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
            result.append(
                {
                    "path": str(root),
                    "label": str(root),
                    "writable": writable,
                    "free": free_bytes(root),
                    "same_device": same_device(root, self.paths.data),
                }
            )
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

    def leftovers(self, network: str) -> list[dict]:
        return [{"name": entry.name, "path": str(entry)} for entry in network_leftovers(self.paths, network)]

    def cleanup_previous(self, network: str) -> int:
        removed = 0
        for entry in network_leftovers(self.paths, network):
            try:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def cleanup_stale(self) -> None:
        """Remove partial `.tmp` archives left behind by an interrupted export."""
        for destination in (ALLOWED_EXTRA_ROOT, self.paths.exports_dir):
            if not destination.is_dir():
                continue
            for entry in destination.glob("*.tmp"):
                try:
                    if time.time() - entry.stat().st_mtime > 300:
                        entry.unlink()
                except OSError:
                    pass

    def _update(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def _phase(self, phase: str, message: str) -> None:
        now = time.time()
        with self._lock:
            started = self._state.get("phase_started_at")
            if started:
                self._state["phases"] = list(self._state.get("phases") or []) + [
                    {"phase": self._state.get("phase"), "seconds": round(now - started, 1)}
                ]
            self._state.update(phase=phase, message=message, phase_started_at=now)
        self.log(f"backup: {phase}: {message}")

    def _active(self) -> bool:
        with self._lock:
            return bool(self._state.get("active"))

    # -------------------------------------------------------------------- start
    def start_export(self, network: str, destination: str, compress: str = "3") -> tuple[bool, str]:
        return self._start("export", network, destination, {"compress": compress})

    def start_import(self, network: str, destination: str, filename: str, verify: str = "basic", delete_previous: bool = False) -> tuple[bool, str]:
        return self._start("import", network, destination, {"filename": filename, "verify": verify, "delete_previous": delete_previous})

    def _start(self, action: str, network: str, destination: str, extra: dict) -> tuple[bool, str]:
        if network not in NETWORKS:
            return False, "red desconocida"
        if self._active():
            return False, "ya hay una copia en curso"
        try:
            target = self._allowed(destination)
            target.mkdir(parents=True, exist_ok=True)
        except (ValueError, OSError) as exc:
            return False, f"destino no válido: {exc}"
        with self._lock:
            self._state = self._idle()
            self._state.update({"active": True, "action": action, "network": network, "started_at": time.time(), "phase": "preparing", "message": "Preparando", "phase_started_at": time.time()})
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
            output = self._state.get("path")
            if output:
                cleanup_tmp_files(Path(output))
            self._update(active=False, ok=False, phase="error", error=str(exc), message="Error", finished_at=time.time())
            self.log(f"backup: error: {exc}")
            if enabled:
                self.supervisor.resume(network)
        finally:
            with self._lock:
                self._state["active"] = False
                if self._state.get("finished_at") is None:
                    self._state["finished_at"] = time.time()

    def _stop(self, network: str) -> None:
        self._phase("stopping", "Parando la red (Core puede tardar hasta 2 min en vaciar su caché)")
        self.supervisor.suspend(network)
        node = self.supervisor.nodes[network]
        for _ in range(10):
            if not node.status().get("running"):
                return
            time.sleep(1)
        raise RuntimeError("la red no se detuvo a tiempo; cancelado antes de tocar los datos")

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
        if index_bytes == 0:
            raise RuntimeError("el índice está vacío; no hay nada que exportar")
        ratios = load_ratios(self.paths)
        index_ratio = float(ratios.get(network) or 0.55)
        estimate = int(index_bytes * index_ratio + core_bytes * 0.95) + SPACE_MARGIN_BYTES
        warnings: list[str] = []
        if same_device(destination, self.paths.data):
            warnings.append("el destino está en el mismo disco que los datos: no sirve como backup")
        free = free_bytes(destination)
        if free < estimate:
            raise RuntimeError(
                f"espacio insuficiente en el destino: hay {free // (1024**3)} GiB libres y se necesitan unos {estimate // (1024**3)} GiB"
            )
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        output = destination / f"e-lite-{network}-{stamp}.tar.gz"
        self._update(total=estimate, path=str(output), warnings=warnings)
        self._stop(network)
        self._phase("exporting", "Sondeando Core…")
        compress = str(args.get("compress") or "3")
        if compress not in ("0", "3", "6"):
            compress = "3"
        command = [
            str(self.paths.runtime / f"e-lite-{_elite_version()}" / "e-lite"),
            "export-snapshot",
            "--config", str(config_file),
            "--with-core",
            "--hash-files",
            "--compress-level", compress,
            "--output", str(output),
        ]
        try:
            self._run_process(command, self._progress_export, output, EXPORT_TIMEOUT_SECONDS)
        finally:
            pass
        manifest = read_manifest(output)
        if manifest.get("network") != network:
            raise RuntimeError(f"la copia generada pertenece a {manifest.get('network')}")
        height = int(manifest.get("height") or 0)
        renamed = destination / f"e-lite-{network}-h{height}-{stamp}.tar.gz"
        if not renamed.exists():
            try:
                os.replace(output, renamed)
            except OSError:
                renamed = output
        size = renamed.stat().st_size
        index_ratio = min(1.0, max(0.05, (size - core_bytes * 0.95) / max(1, index_bytes)))
        save_ratio(self.paths, network, index_ratio)
        cleanup_tmp_files(renamed)
        self._append_job_log(f"exported {renamed.name} ({size} bytes, height {height})")
        self._update(
            ok=True,
            phase="done",
            message=f"Copia completada · altura {height}",
            done=size,
            total=size,
            eta_seconds=0,
            path=str(renamed),
            finished_at=time.time(),
        )
        self.log(f"backup: exported {renamed} ({size} bytes)")
        self._restart(network, enabled)

    def _progress_export(self, output: Path) -> None:
        with self._lock:
            total = int(self._state.get("total") or 0)
        done = 0
        for candidate in export_tmp_candidates(output):
            try:
                if candidate.exists():
                    done = candidate.stat().st_size
                    break
            except OSError:
                continue
        if done == 0 and output.exists():
            try:
                done = output.stat().st_size
            except OSError:
                done = 0
        message = "Sondeando Core…" if done == 0 else "Exportando…"
        self._update(message=message)
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
        total = restored_estimate(manifest, archive.stat().st_size)
        need = total + SPACE_MARGIN_BYTES
        free = free_bytes(self.paths.data)
        if free < need:
            raise RuntimeError(
                f"espacio insuficiente en el disco de datos: hay {free // (1024**3)} GiB libres y se necesitan unos {need // (1024**3)} GiB"
            )
        warnings: list[str] = []
        if not core_section:
            warnings.append("esta copia no incluye Core; comprueba que la punta de Core no sea anterior a la del índice")
        self._update(total=total, path=str(archive), warnings=warnings, no_core=not core_section)
        self._stop(network)
        target = self.paths.network_e_lite_data(network)
        core_target = self.paths.network_core_data(network)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
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
            self._run_process(command, self._progress_import, target, IMPORT_TIMEOUT_SECONDS)
            restored_core = target / "bitcoin"
            if core_section:
                if not restored_core.is_dir():
                    raise RuntimeError("el snapshot no restauró el datadir de Core")
                if core_target.exists():
                    shutil.rmtree(core_target, ignore_errors=True)
                os.replace(restored_core, core_target)
            if args.get("delete_previous"):
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
        self._append_job_log(f"imported {archive.name} (height {manifest.get('height')})")
        self.log(f"backup: imported {archive} (height {manifest.get('height')})")
        self._restart(network, enabled)

    def _progress_import(self, target: Path) -> None:
        with self._lock:
            total = int(self._state.get("total") or 0)
        done = _du(target) if target.exists() else 0
        self._update(message="Verificando…" if done >= total > 0 else "Restaurando…")
        self._set_progress(done, total)

    def _run_process(self, command: list[str], progress, target: Path, timeout: int) -> None:
        env = os.environ.copy()
        env["HOME"] = str(self.paths.data)
        env["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=True,
        )
        lines: list[str] = []
        reader = threading.Thread(target=lambda: self._drain(process, lines), daemon=True)
        reader.start()
        deadline = time.time() + timeout
        try:
            while process.poll() is None:
                progress(target)
                if time.time() > deadline:
                    raise RuntimeError(f"el proceso superó el límite de {timeout // 3600} h; cancelado")
                time.sleep(1)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                process.terminate()
            try:
                process.wait(30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
            raise
        reader.join(timeout=5)
        self._update(log=lines[-40:])
        if process.returncode != 0:
            raise RuntimeError(" | ".join(lines[-3:]) or f"el comando terminó con código {process.returncode}")

    def _drain(self, process: subprocess.Popen[str], lines: list[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            text = line.rstrip("\n")
            lines.append(text)
            del lines[:-200]
            with self._lock:
                self._state["tail"] = lines[-5:]

    def _append_job_log(self, message: str) -> None:
        try:
            path = self.paths.exports_dir / "backup.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}Z {message}\n")
        except OSError:
            pass

    def _set_progress(self, done: int, total: int) -> None:
        now = time.time()
        with self._lock:
            started = self._state.get("started_at") or now
            elapsed = max(0.001, now - started)
            rate = done / elapsed
            # The export estimate is a guess; never let the bar look stuck by
            # showing less than what has actually been written.
            total = max(total, done)
            eta = int(max(0, total - done) / rate) if rate > 0 and total > done else 0
            self._state.update(done=done, total=total, eta_seconds=eta)


def _elite_version() -> str:
    from .runtime import E_LITE_VERSION
    return E_LITE_VERSION
