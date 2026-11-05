"""Supervise the e-lite process and its managed Bitcoin Core child."""

from __future__ import annotations

import os
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


class Supervisor:
    def __init__(
        self,
        paths: AppPaths,
        settings_loader: Callable[[], Settings],
        tls: TLSManager,
        e_lite_binary: Path,
        bitcoin_binary: Path,
        tor_socks: str,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.paths = paths
        self.settings_loader = settings_loader
        self.tls = tls
        self.e_lite_binary = e_lite_binary
        self.bitcoin_binary = bitcoin_binary
        self.tor_socks = tor_socks
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
            self._thread = threading.Thread(target=self._run_loop, name="e-lite-supervisor", daemon=True)
            self._thread.start()

    def request_restart(self) -> None:
        self._restart.set()

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
                "state": self._state,
                "running": running,
                "pid": process.pid if running and process else None,
                "exit_code": self._exit_code,
                "last_error": self._last_error,
                "started_at": self._started_at,
                "restart_count": self._restart_count,
                "log_tail": list(self._ring)[-60:],
            }

    def tail_log(self, network: str | None, limit: int = 80) -> list[str]:
        if network:
            path = self.paths.e_lite_log(network)
            lines = read_tail(path, limit=limit)
            if lines:
                return lines
        return list(self._ring)[-limit:]

    def core_log_tail(self, network: str | None, limit: int = 80) -> list[str]:
        if not network:
            return []
        return read_tail(self.paths.core_log(network), limit=limit)

    # ------------------------------------------------------------------- thread
    def _run_loop(self) -> None:
        backoff = 5
        while not self._stop.is_set():
            try:
                settings = self.settings_loader()
            except Exception as exc:  # noqa: BLE001
                self._set_state("error", f"settings: {exc}")
                self._stop.wait(3)
                continue

            if not settings.setup_complete or not settings.network:
                self._set_state("setup")
                self._stop.wait(1)
                continue

            if not self.e_lite_binary.is_file() or not self.bitcoin_binary.is_file():
                self._set_state("preparing")
                self._stop.wait(2)
                continue

            try:
                self.tls.ensure()
                contents = build_config(
                    settings,
                    self.paths,
                    e_lite_binary=self.e_lite_binary,
                    bitcoin_binary=self.bitcoin_binary,
                    tor_socks=self.tor_socks,
                )
                write_config(self.paths.config_file, contents)
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
            next_tls = time.monotonic() + 30
            while not self._stop.is_set() and not self._restart.is_set():
                if process.poll() is not None:
                    break
                if time.monotonic() >= next_tls:
                    self.tls.ensure()
                    next_tls = time.monotonic() + 30
                self._stop.wait(1)

            self._terminate(settings.network)
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
        command = [str(self.e_lite_binary), "run", "--config", str(self.paths.config_file)]
        self.log("starting: " + " ".join(command))
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
        threading.Thread(target=self._read_output, args=(process,), name="e-lite-output", daemon=True).start()
        return process

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        handle = self.paths.e_lite_console_log.open("a", encoding="utf-8", errors="replace")
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
                        backup = self.paths.e_lite_console_log.with_suffix(".log.1")
                        os.replace(self.paths.e_lite_console_log, backup)
                        handle = self.paths.e_lite_console_log.open("a", encoding="utf-8", errors="replace")
                except OSError:
                    pass
        finally:
            try:
                handle.close()
            except OSError:
                pass

    def _terminate(self, network: str | None) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self.log("e-lite did not stop in time; killing it")
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
        self._cleanup_core(network)

    def _cleanup_core(self, network: str | None) -> None:
        if not network:
            return
        pid_file = self.paths.core_pid(network)
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        if pid <= 1 or not _looks_like_our_core(pid, self.paths.core_data):
            return
        self.log(f"stopping leftover Bitcoin Core pid {pid}")
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
