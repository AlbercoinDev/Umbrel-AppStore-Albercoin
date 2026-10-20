from __future__ import annotations

import subprocess
import threading
import time
import os
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class StressProcess:
    duration_seconds: int
    workers: int = 1
    process: subprocess.Popen | None = None
    output: str = ""
    error: str = ""
    started_at_monotonic: float = field(default_factory=time.monotonic)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> None:
        command = ["stress-ng", "--cpu", str(max(1, self.workers)), "--timeout", f"{self.duration_seconds}s", "--metrics-brief"]
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


def available_cpu_workers() -> int:
    """Estimate CPUs available to this container, including cgroup CPU quotas."""
    try:
        count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        count = os.cpu_count() or 1

    quota_limits: list[int] = []
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            quota_limits.append(max(1, int(int(quota) / int(period))))
    except (OSError, ValueError, ZeroDivisionError):
        pass
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if quota > 0 and period > 0:
            quota_limits.append(max(1, quota // period))
    except (OSError, ValueError, ZeroDivisionError):
        pass
    if quota_limits:
        count = min(count, *quota_limits)
    return max(1, count)
