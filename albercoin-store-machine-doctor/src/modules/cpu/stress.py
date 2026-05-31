from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field


@dataclass
class StressProcess:
    duration_seconds: int
    process: subprocess.Popen | None = None
    output: str = ""
    error: str = ""
    started_at_monotonic: float = field(default_factory=time.monotonic)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> None:
        command = ["stress-ng", "--cpu", "0", "--timeout", f"{self.duration_seconds}s", "--metrics-brief"]
        with self._lock:
            self.started_at_monotonic = time.monotonic()
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd="/tmp")

    def poll(self) -> int | None:
        with self._lock:
            return self.process.poll() if self.process else None

    def is_running(self) -> bool:
        return self.poll() is None

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at_monotonic

    def finish_output(self, timeout: float = 2.0) -> tuple[int | None, str, str]:
        with self._lock:
            process = self.process
        if not process:
            return None, self.output, self.error
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
        self.output += stdout or ""
        self.error += stderr or ""
        return process.returncode, self.output, self.error

    def stop(self) -> None:
        with self._lock:
            process = self.process
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
        self.output += stdout or ""
        self.error += stderr or ""
