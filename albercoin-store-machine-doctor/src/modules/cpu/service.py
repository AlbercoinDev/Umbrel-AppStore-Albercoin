from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from typing import Any

from .report import build_report, save_report
from .sensors import cpu_usage_percent, read_cpu_info, read_frequencies, read_load_average, read_proc_stat, read_temperatures
from .stress import StressProcess


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


TEST_DURATION_SECONDS = max(5, min(_env_int("TEST_DURATION_SECONDS", 300), 3600))
CPU_WARNING_TEMP = _env_int("CPU_WARNING_TEMP", 80)
CPU_CRITICAL_TEMP = _env_int("CPU_CRITICAL_TEMP", 90)
CPU_MIN_EXPECTED_LOAD = _env_int("CPU_MIN_EXPECTED_LOAD", 90)


class CpuCheckService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stress: StressProcess | None = None
        self._cancel_requested = False
        self._state = self._initial_state()

    def _initial_state(self) -> dict[str, Any]:
        return {
            "state": "stopped",
            "duration_seconds": TEST_DURATION_SECONDS,
            "remaining_seconds": TEST_DURATION_SECONDS,
            "started_at": None,
            "finished_at": None,
            "progress_percent": 0,
            "result": None,
            "warnings": [],
            "errors": [],
            "samples": [],
            "current_sample": None,
            "cpu_info": read_cpu_info(),
            "thresholds": {
                "warning_temp": CPU_WARNING_TEMP,
                "critical_temp": CPU_CRITICAL_TEMP,
                "min_expected_load": CPU_MIN_EXPECTED_LOAD,
            },
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            state["samples"] = list(self._state.get("samples", []))[-20:]
            return state

    def cpu_info(self) -> dict[str, Any]:
        return read_cpu_info()

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._state.get("state") == "running":
                return {"error": "cpu_test_already_running"}
            self._cancel_requested = False
            self._state = self._initial_state()
            self._state.update({
                "state": "running",
                "started_at": datetime.now(UTC).isoformat(),
                "remaining_seconds": TEST_DURATION_SECONDS,
            })
            self._thread = threading.Thread(target=self._run_test, daemon=True)
            self._thread.start()
            return {"status": "started", "duration_seconds": TEST_DURATION_SECONDS}

    def cancel(self) -> dict[str, str]:
        with self._lock:
            if self._state.get("state") != "running":
                return {"status": "not_running"}
            self._cancel_requested = True
            stress = self._stress
        if stress:
            stress.stop()
        return {"status": "cancel_requested"}

    def shutdown(self) -> None:
        self.cancel()

    def _sample(self, previous_stat: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
        current_stat = read_proc_stat()
        cpu_info = self._state.get("cpu_info", read_cpu_info())
        temperatures = read_temperatures()
        frequencies = read_frequencies(cpu_info.get("cpuinfo_mhz", {}))
        total_usage = None
        core_usage: dict[int, float | None] = {}

        if previous_stat:
            total_usage = cpu_usage_percent(previous_stat.get("total", []), current_stat.get("total", []))
            for core_id, current_values in current_stat.get("cores", {}).items():
                previous_values = previous_stat.get("cores", {}).get(core_id, [])
                core_usage[core_id] = cpu_usage_percent(previous_values, current_values)

        per_core = []
        for core_id in sorted(current_stat.get("cores", {}).keys()):
            per_core.append({
                "core": core_id,
                "usage": core_usage.get(core_id),
                "frequency_mhz": frequencies.get(core_id),
                "temperature": None,
            })

        sample = {
            "timestamp": datetime.now(UTC).isoformat(),
            "cpu_total_usage": total_usage,
            "cpu_core_usage": core_usage,
            "temperature_cpu": temperatures.get("cpu"),
            "temperatures": temperatures.get("sensors", []),
            "frequency_per_core_mhz": frequencies,
            "load_average": read_load_average(),
            "stress_ng_running": self._stress.is_running() if self._stress else False,
            "per_core": per_core,
        }
        return sample, current_stat

    def _run_test(self) -> None:
        stress = StressProcess(TEST_DURATION_SECONDS)
        with self._lock:
            self._stress = stress

        previous_stat = read_proc_stat()
        errors: list[str] = []
        warnings: list[str] = []

        try:
            stress.start()
        except FileNotFoundError:
            errors.append("stress-ng is not installed in the container")
            self._finish("FAIL", warnings, errors, {"returncode": None, "stdout": "", "stderr": "stress-ng not found"})
            return
        except OSError as exc:
            errors.append(f"Unable to start stress-ng: {exc}")
            self._finish("FAIL", warnings, errors, {"returncode": None, "stdout": "", "stderr": str(exc)})
            return

        started = time.monotonic()
        while True:
            time.sleep(1)
            elapsed = min(TEST_DURATION_SECONDS, int(time.monotonic() - started))
            sample, previous_stat = self._sample(previous_stat)

            with self._lock:
                self._state["samples"].append(sample)
                self._state["current_sample"] = sample
                self._state["remaining_seconds"] = max(0, TEST_DURATION_SECONDS - elapsed)
                self._state["progress_percent"] = round((elapsed / TEST_DURATION_SECONDS) * 100, 1)
                cancel_requested = self._cancel_requested

            if cancel_requested:
                stress.stop()
                errors.append("CPU test was cancelled by the user")
                self._finish("FAIL", warnings, errors, {"returncode": -15, "stdout": stress.output, "stderr": stress.error})
                return

            returncode = stress.poll()
            if returncode is not None:
                break
            if elapsed >= TEST_DURATION_SECONDS:
                break

        returncode, stdout, stderr = stress.finish_output(timeout=5)
        stress_data = {"returncode": returncode, "stdout": stdout, "stderr": stderr}
        result, warnings, errors = self._diagnose(returncode, warnings, errors)
        self._finish(result, warnings, errors, stress_data)

    def _diagnose(self, returncode: int | None, warnings: list[str], errors: list[str]) -> tuple[str, list[str], list[str]]:
        with self._lock:
            samples = list(self._state.get("samples", []))
            cpu_info = dict(self._state.get("cpu_info", {}))

        if returncode not in (0, None):
            errors.append(f"stress-ng exited with code {returncode}")
        if returncode is None:
            warnings.append("stress-ng return code was not available")

        if not samples:
            errors.append("No CPU samples were collected")
            return "FAIL", warnings, errors

        temperatures = [s.get("temperature_cpu") for s in samples if isinstance(s.get("temperature_cpu"), (int, float))]
        usages = [s.get("cpu_total_usage") for s in samples if isinstance(s.get("cpu_total_usage"), (int, float))]
        sensor_counts = [len(s.get("temperatures", [])) for s in samples]

        if not temperatures:
            warnings.append("CPU temperature is not exposed by this system")
        elif max(temperatures) >= CPU_CRITICAL_TEMP:
            errors.append(f"CPU temperature reached critical threshold: {max(temperatures)} C")
        elif max(temperatures) >= CPU_WARNING_TEMP:
            warnings.append(f"CPU temperature reached warning threshold: {max(temperatures)} C")

        if not usages:
            warnings.append("CPU usage could not be calculated from /proc/stat")
        else:
            avg_usage = sum(usages) / len(usages)
            max_usage = max(usages)
            if avg_usage < CPU_MIN_EXPECTED_LOAD * 0.75:
                errors.append(f"Average CPU usage was too low during stress test: {avg_usage:.1f}%")
            elif max_usage < CPU_MIN_EXPECTED_LOAD:
                warnings.append(f"CPU load did not clearly reach expected threshold: {max_usage:.1f}%")

        expected_cores = cpu_info.get("cores") or 0
        core_samples = [s.get("per_core", []) for s in samples if s.get("per_core")]
        if expected_cores and core_samples:
            last_cores = core_samples[-1]
            high_load_cores = [c for c in last_cores if isinstance(c.get("usage"), (int, float)) and c["usage"] >= CPU_MIN_EXPECTED_LOAD]
            if len(high_load_cores) < max(1, int(expected_cores * 0.75)):
                warnings.append("Not all CPU cores were observed at high load in the latest sample")

        if sensor_counts and max(sensor_counts) == 0:
            warnings.append("No hardware sensors were available")

        if errors:
            return "FAIL", warnings, errors
        if warnings:
            return "WARNING", warnings, errors
        return "PASS", warnings, errors

    def _finish(self, result: str, warnings: list[str], errors: list[str], stress_data: dict[str, Any]) -> None:
        with self._lock:
            self._state.update({
                "state": "finished" if result != "FAIL" else "error",
                "finished_at": datetime.now(UTC).isoformat(),
                "remaining_seconds": 0,
                "progress_percent": 100,
                "result": result,
                "warnings": warnings,
                "errors": errors,
                "stress_ng": stress_data,
            })
            report = build_report(self._state)
        path = save_report(report)
        with self._lock:
            self._state["report_path"] = str(path)


cpu_check_service = CpuCheckService()
