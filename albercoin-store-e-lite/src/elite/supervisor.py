"""Supervise one e-lite process per enabled Bitcoin network."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from .config_writer import AppPaths, build_config, write_config
from .settings import Settings
from .tls import TLSManager

CONSOLE_LOG_LIMIT = 8 * 1024 * 1024
STOP_GRACE_SECONDS = 240


class NodeSupervisor:
    def __init__(
        self,
        paths: AppPaths,
        network: str,
        settings_loader: Callable[[], Settings],
        tls: TLSManager,
        e_lite_binary: Path,
        bitcoin_binary: Path,
        tor_binary: Path,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.paths = paths
        self.network = network
        self.settings_loader = settings_loader
        self.tls = tls
        self.e_lite_binary = e_lite_binary
        self.bitcoin_binary = bitcoin_binary
        self.tor_binary = tor_binary
        self.log = log or (lambda _message: None)

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._restart = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._state = "idle"
        self._last_error = ""
        self._started_at: float | None = None
        self._exit_code: int | None = None
        self._restart_count = 0
        self._ring: deque[str] = deque(maxlen=200)

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name=f"e-lite-{self.network}",
                daemon=True,
            )
            self._thread.start()

    def request_restart(self) -> None:
        self._restart.set()

    def is_started(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        self._stop.set()
        self._restart.set()
        thread = self._thread
        if thread is not None:
            thread.join(STOP_GRACE_SECONDS + 30)

    def status(self) -> dict[str, object]:
        process = self._process
        running = process is not None and process.poll() is None
        with self._lock:
            return {
                "network": self.network,
                "state": self._state,
                "running": running,
                "pid": process.pid if running and process else None,
                "exit_code": self._exit_code,
                "last_error": self._last_error,
                "started_at": self._started_at,
                "restart_count": self._restart_count,
                "log_tail": list(self._ring)[-60:],
            }

    def tail_log(self, limit: int = 80) -> list[str]:
        lines = read_tail(self.paths.network_e_lite_log(self.network), limit=limit)
        return lines or list(self._ring)[-limit:]

    def core_log_tail(self, limit: int = 80) -> list[str]:
        return read_tail(self.paths.network_core_log(self.network), limit=limit)

    # ------------------------------------------------------------------- thread
    def _run_loop(self) -> None:
        backoff = 5
        while not self._stop.is_set():
            try:
                settings = self.settings_loader()
                config = settings.network(self.network)
            except Exception as exc:  # noqa: BLE001
                self._set_state("error", f"settings: {exc}")
                self._stop.wait(3)
                continue

            if not settings.setup_complete or not config.enabled:
                self._set_state("disabled")
                self._stop.wait(1)
                continue

            if not self.e_lite_binary.is_file() or not self.bitcoin_binary.is_file() or not self.tor_binary.is_file():
                self._set_state("preparing")
                self._stop.wait(2)
                continue

            try:
                template = self.e_lite_binary.parent / "public-servers.toml"
                destination = self.paths.network_public_servers(self.network)
                if template.is_file() and not destination.is_file():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(template, destination)
                self.tls.ensure()
                contents = build_config(
                    self.network,
                    config,
                    self.paths,
                    bitcoin_binary=self.bitcoin_binary,
                    tor_binary=self.tor_binary,
                )
                write_config(self.paths.network_config_file(self.network), contents)
            except Exception as exc:  # noqa: BLE001
                self._set_state("error", f"configuration: {exc}")
                self._stop.wait(5)
                continue

            self._restart.clear()
            started = time.monotonic()
            try:
                process = self._launch()
            except Exception as exc:  # noqa: BLE001
                self._set_state("error", f"launch: {exc}")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)
                continue

            self._set_state("running")
            next_tls = time.monotonic() + 15
            while not self._stop.is_set() and not self._restart.is_set():
                if process.poll() is not None:
                    break
                if time.monotonic() >= next_tls:
                    self.tls.ensure()
                    next_tls = time.monotonic() + 15
                self._stop.wait(1)

            self._terminate()
            self._set_state("restarting")
            if self._stop.is_set():
                break
            if self._restart.is_set():
                self._restart.clear()
                backoff = 5
                continue

            code = process.returncode
            elapsed = time.monotonic() - started
            with self._lock:
                self._exit_code = code
                self._restart_count += 1
            self._set_state("exited", f"e-lite exited with code {code}")
            if elapsed < 60:
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)
            else:
                backoff = 5

        self._set_state("stopped")

    def _launch(self) -> subprocess.Popen[str]:
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(self.paths.data)
        env.setdefault("PYTHONUNBUFFERED", "1")
        command = [
            str(self.e_lite_binary),
            "run",
            "--config",
            str(self.paths.network_config_file(self.network)),
        ]
        self.log(f"starting {self.network}: " + " ".join(command))
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=True,
        )
        with self._lock:
            self._process = process
            self._started_at = time.time()
            self._exit_code = None
        threading.Thread(
            target=self._read_output,
            args=(process,),
            name=f"e-lite-{self.network}-output",
            daemon=True,
        ).start()
        return process

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        path = self.paths.network_console_log(self.network)
        handle = path.open("a", encoding="utf-8", errors="replace")
        try:
            for line in process.stdout:
                text = line.rstrip("\n")
                with self._lock:
                    self._ring.append(text)
                try:
                    handle.write(text + "\n")
                    handle.flush()
                    if handle.tell() > CONSOLE_LOG_LIMIT:
                        handle.close()
                        backup = path.with_name(path.name + ".1")
                        os.replace(path, backup)
                        handle = path.open("a", encoding="utf-8", errors="replace")
                except OSError:
                    pass
        finally:
            try:
                handle.close()
            except OSError:
                pass

    def _terminate(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self.log(f"{self.network}: e-lite did not stop in time; killing it")
                try:
                    process.kill()
                    process.wait(30)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        if process is not None:
            try:
                process.stdout and process.stdout.close()
            except OSError:
                pass
        with self._lock:
            self._process = None
            self._started_at = None
        self._cleanup_core()

    def _cleanup_core(self) -> None:
        pid_file = self.paths.network_core_pid(self.network)
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        datadir = self.paths.network_core_data(self.network)
        if pid <= 1 or not _looks_like_our_core(pid, datadir):
            return
        self.log(f"{self.network}: stopping leftover Bitcoin Core pid {pid}")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if not _pid_alive(pid):
                return
            time.sleep(1)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    def _set_state(self, state: str, error: str = "") -> None:
        with self._lock:
            self._state = state
            self._last_error = error


class SupervisorManager:
    """Own one NodeSupervisor per network and apply settings changes."""

    def __init__(
        self,
        paths: AppPaths,
        settings_loader: Callable[[], Settings],
        tls_managers: dict[str, TLSManager],
        e_lite_binary: Path,
        bitcoin_binary: Path,
        tor_binary: Path,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.paths = paths
        self.settings_loader = settings_loader
        self.log = log or (lambda _message: None)
        self.nodes = {
            network: NodeSupervisor(
                paths,
                network,
                settings_loader,
                tls_managers[network],
                e_lite_binary,
                bitcoin_binary,
                tor_binary,
                log=log,
            )
            for network in tls_managers
        }

    def start(self) -> None:
        settings = self.settings_loader()
        for network, node in self.nodes.items():
            if settings.network(network).enabled:
                node.start()

    def stop(self) -> None:
        for node in self.nodes.values():
            node.stop()

    def request_restart(self, network: str | None = None) -> None:
        if network:
            self.nodes[network].request_restart()
        else:
            for node in self.nodes.values():
                node.request_restart()

    def apply_settings(self, network: str | None = None) -> None:
        settings = self.settings_loader()
        names = [network] if network else list(self.nodes)
        for name in names:
            node = self.nodes[name]
            if settings.network(name).enabled:
                if node.is_started():
                    node.request_restart()
                else:
                    node.start()
            else:
                node.stop()

    def status(self) -> dict[str, dict[str, object]]:
        return {network: node.status() for network, node in self.nodes.items()}

    def tail_log(self, network: str, limit: int = 80) -> list[str]:
        return self.nodes[network].tail_log(limit=limit)

    def core_log_tail(self, network: str, limit: int = 80) -> list[str]:
        return self.nodes[network].core_log_tail(limit=limit)


def read_tail(path: Path, limit: int = 80, max_bytes: int = 128 * 1024) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            if size > max_bytes:
                handle.readline()
            data = handle.read().decode(errors="replace")
    except OSError:
        return []
    lines = data.splitlines()
    return lines[-limit:]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _looks_like_our_core(pid: int, datadir: Path) -> bool:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    args = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
    if not args or "bitcoind" not in Path(args[0]).name:
        return False
    needle = f"-datadir={datadir}"
    return any(arg == needle for arg in args)
