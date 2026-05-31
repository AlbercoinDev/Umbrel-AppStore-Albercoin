from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from typing import Any

from .report import build_report, save_report
from .sensors import cpu_usage_percent, read_cpu_info, read_cpu_topology, read_frequencies, read_load_average, read_proc_stat, read_temperatures, read_thermal_management
from .stress import StressProcess


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


TEST_DURATION_SECONDS = max(5, min(_env_int("TEST_DURATION_SECONDS", 300), 3600))
CPU_WARNING_TEMP = _env_int("CPU_WARNING_TEMP", 80)
CPU_CRITICAL_TEMP = _env_int("CPU_CRITICAL_TEMP", 90)
CPU_FAIL_TEMP = _env_int("CPU_FAIL_TEMP", 100)
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
                "fail_temp": CPU_FAIL_TEMP,
                "min_expected_load": CPU_MIN_EXPECTED_LOAD,
            },
            "throttling": {"status": "unknown", "confirmed": False, "available": False, "details": []},
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
            self._thread = threading.Thread(target=self._run_test_safe, daemon=True)
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

    def _run_test_safe(self) -> None:
        try:
            self._run_test()
        except Exception as exc:
            self._finish("FAIL", [], [f"Unexpected CPU test error: {exc}"], {"returncode": None, "stdout": "", "stderr": str(exc)})

    def _sample(self, previous_stat: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
        current_stat = read_proc_stat()
        cpu_info = self._state.get("cpu_info", read_cpu_info())
        temperatures = read_temperatures()
        thermal_management = read_thermal_management()
        throttle_baseline = self._state.get("throttle_baseline", {})
        throttle_deltas = self._throttle_deltas(throttle_baseline, thermal_management.get("cpu_throttle_counts", {}))
        processor_cooling_active = [device for device in thermal_management.get("active_cooling_devices", []) if "processor" in str(device.get("type", "")).lower()]
        throttling_confirmed = bool(throttle_deltas or processor_cooling_active)
        throttling_status = "confirmed" if throttling_confirmed else "not_detected" if thermal_management.get("throttle_counters_available") or thermal_management.get("cooling_devices") else "unavailable"
        topology = read_cpu_topology()
        frequencies = read_frequencies(cpu_info.get("cpuinfo_mhz", {}))
        total_usage = None
        core_usage: dict[int, float | None] = {}

        if previous_stat:
            total_usage = cpu_usage_percent(previous_stat.get("total", []), current_stat.get("total", []))
            for core_id, current_values in current_stat.get("cores", {}).items():
                previous_values = previous_stat.get("cores", {}).get(core_id, [])
                core_usage[core_id] = cpu_usage_percent(previous_values, current_values)

        per_core = []
        core_temperatures = temperatures.get("core_temperatures", {})
        cpu_temperature = temperatures.get("cpu")
        for core_id in sorted(current_stat.get("cores", {}).keys()):
            temperature = None
            temperature_source = None
            if core_id in core_temperatures:
                temperature = core_temperatures[core_id]
                temperature_source = "core"
            else:
                physical_core_id = topology.get(core_id, {}).get("core_id")
                if physical_core_id in core_temperatures:
                    temperature = core_temperatures[physical_core_id]
                    temperature_source = "physical_core"
                elif isinstance(cpu_temperature, (int, float)):
                    temperature = cpu_temperature
                    temperature_source = "cpu_global"
            per_core.append({
                "core": core_id,
                "usage": core_usage.get(core_id),
                "frequency_mhz": frequencies.get(core_id),
                "temperature": temperature,
                "temperature_source": temperature_source,
            })

        sample = {
            "timestamp": datetime.now(UTC).isoformat(),
            "cpu_total_usage": total_usage,
            "cpu_core_usage": core_usage,
            "temperature_cpu": temperatures.get("cpu"),
            "temperature_per_core": core_temperatures,
            "temperatures": temperatures.get("sensors", []),
            "frequency_per_core_mhz": frequencies,
            "load_average": read_load_average(),
            "stress_ng_running": self._stress.is_running() if self._stress else False,
            "thermal_management": thermal_management,
            "throttling": {
                "status": throttling_status,
                "confirmed": throttling_confirmed,
                "available": thermal_management.get("throttle_counters_available") or bool(thermal_management.get("cooling_devices")),
                "counter_deltas": throttle_deltas,
                "active_processor_cooling": processor_cooling_active,
            },
            "per_core": per_core,
        }
        return sample, current_stat

    def _throttle_deltas(self, baseline: dict[str, Any], current: dict[int, dict[str, int]]) -> dict[int, dict[str, int]]:
        deltas: dict[int, dict[str, int]] = {}
        for cpu_id, counters in current.items():
            base_counters = baseline.get(cpu_id) or baseline.get(str(cpu_id)) or {}
            for counter_name, value in counters.items():
                base_value = base_counters.get(counter_name, value) if isinstance(base_counters, dict) else value
                delta = value - base_value
                if delta > 0:
                    deltas.setdefault(cpu_id, {})[counter_name] = delta
        return deltas

    def _run_test(self) -> None:
        stress = StressProcess(TEST_DURATION_SECONDS)
        with self._lock:
            self._stress = stress
            self._state["throttle_baseline"] = read_thermal_management().get("cpu_throttle_counts", {})

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

        throttling_confirmed = any((sample.get("throttling") or {}).get("confirmed") for sample in samples)
        throttling_available = any((sample.get("throttling") or {}).get("available") for sample in samples)

        if not temperatures:
            warnings.append("CPU temperature is not exposed by this system")
        elif max(temperatures) >= CPU_FAIL_TEMP:
            errors.append(f"CPU temperature reached fail threshold: {max(temperatures)} C")
        elif max(temperatures) >= CPU_CRITICAL_TEMP and throttling_confirmed:
            errors.append(f"CPU temperature reached critical threshold and throttling was detected: {max(temperatures)} C")
        elif max(temperatures) >= CPU_CRITICAL_TEMP:
            suffix = "throttling was not detected" if throttling_available else "throttling could not be confirmed by this system"
            warnings.append(f"CPU temperature reached critical threshold: {max(temperatures)} C; {suffix}")
        elif max(temperatures) >= CPU_WARNING_TEMP:
            warnings.append(f"CPU temperature reached warning threshold: {max(temperatures)} C")

        if throttling_confirmed:
            warnings.append("CPU throttling was detected during the test")

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
            max_usage_by_core: dict[int, float] = {}
            for sample_cores in core_samples:
                for core in sample_cores:
                    core_id = core.get("core")
                    usage = core.get("usage")
                    if isinstance(core_id, int) and isinstance(usage, (int, float)):
                        max_usage_by_core[core_id] = max(max_usage_by_core.get(core_id, 0.0), usage)
            high_load_cores = [usage for usage in max_usage_by_core.values() if usage >= CPU_MIN_EXPECTED_LOAD]
            if len(high_load_cores) < max(1, int(expected_cores * 0.75)):
                warnings.append("Not all CPU cores were observed at high load during the test")

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
            latest_sample = self._state.get("current_sample") or {}
            self._state["throttling"] = latest_sample.get("throttling", self._state.get("throttling"))
            report = build_report(self._state)
        path = save_report(report)
        with self._lock:
            self._state["report_path"] = str(path)


cpu_check_service = CpuCheckService()
