from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

HOST_PROC = Path(os.environ.get("HOST_PROC", "/host/proc"))
HOST_SYS = Path(os.environ.get("HOST_SYS", "/host/sys"))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


def _read_number(path: Path) -> float | None:
    text = _read_text(path)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_cpu_info() -> dict[str, Any]:
    text = _read_text(HOST_PROC / "cpuinfo")
    model = "Unknown CPU"
    processors: list[int] = []
    cpu_mhz: dict[int, float] = {}

    current_processor: int | None = None
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        key_lower = key.lower()
        if key_lower == "processor":
            try:
                current_processor = int(value)
                processors.append(current_processor)
            except ValueError:
                current_processor = None
        elif key_lower in ("model name", "hardware") and value and model == "Unknown CPU":
            model = value
        elif key_lower == "cpu mhz" and current_processor is not None:
            try:
                cpu_mhz[current_processor] = float(value)
            except ValueError:
                pass

    if not processors:
        stat = read_proc_stat()
        processors = sorted(stat["cores"].keys())

    return {
        "model": model,
        "cores": len(processors),
        "processors": processors,
        "cpuinfo_mhz": cpu_mhz,
    }


def read_proc_stat() -> dict[str, Any]:
    text = _read_text(HOST_PROC / "stat")
    total: list[int] | None = None
    cores: dict[int, list[int]] = {}
    for line in text.splitlines():
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        values = [int(v) for v in parts[1:] if v.isdigit()]
        if parts[0] == "cpu":
            total = values
            continue
        match = re.fullmatch(r"cpu(\d+)", parts[0])
        if match:
            cores[int(match.group(1))] = values
    return {"total": total or [], "cores": cores}


def cpu_usage_percent(previous: list[int], current: list[int]) -> float | None:
    if not previous or not current or len(previous) < 5 or len(current) < 5:
        return None
    prev_idle = previous[3] + (previous[4] if len(previous) > 4 else 0)
    curr_idle = current[3] + (current[4] if len(current) > 4 else 0)
    prev_total = sum(previous)
    curr_total = sum(current)
    total_delta = curr_total - prev_total
    idle_delta = curr_idle - prev_idle
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 2)


def read_load_average() -> dict[str, float | None]:
    parts = _read_text(HOST_PROC / "loadavg").split()
    values: list[float | None] = []
    for index in range(3):
        try:
            values.append(float(parts[index]))
        except (IndexError, ValueError):
            values.append(None)
    return {"1m": values[0], "5m": values[1], "15m": values[2]}


def read_frequencies(cpuinfo_mhz: dict[int, float] | None = None) -> dict[int, float | None]:
    cpuinfo_mhz = cpuinfo_mhz or {}
    result: dict[int, float | None] = {}
    cpu_root = HOST_SYS / "devices/system/cpu"
    for cpu_path in sorted(cpu_root.glob("cpu[0-9]*")):
        match = re.fullmatch(r"cpu(\d+)", cpu_path.name)
        if not match:
            continue
        core_id = int(match.group(1))
        khz = _read_number(cpu_path / "cpufreq/scaling_cur_freq")
        if khz is None:
            khz = _read_number(cpu_path / "cpufreq/cpuinfo_cur_freq")
        result[core_id] = round(khz / 1000.0, 1) if khz else cpuinfo_mhz.get(core_id)
    for core_id, mhz in cpuinfo_mhz.items():
        result.setdefault(core_id, mhz)
    return result


def read_cpu_topology() -> dict[int, dict[str, int | None]]:
    topology: dict[int, dict[str, int | None]] = {}
    cpu_root = HOST_SYS / "devices/system/cpu"
    for cpu_path in sorted(cpu_root.glob("cpu[0-9]*")):
        match = re.fullmatch(r"cpu(\d+)", cpu_path.name)
        if not match:
            continue
        logical_id = int(match.group(1))
        core_id = _read_number(cpu_path / "topology/core_id")
        package_id = _read_number(cpu_path / "topology/physical_package_id")
        topology[logical_id] = {
            "core_id": int(core_id) if core_id is not None else None,
            "package_id": int(package_id) if package_id is not None else None,
        }
    return topology


def _thermal_zone_name(zone: Path) -> str:
    zone_type = _read_text(zone / "type")
    return zone_type or zone.name


def _hwmon_name(hwmon: Path) -> str:
    name = _read_text(hwmon / "name")
    return name or hwmon.name


def _normalize_temp(raw: float | None) -> float | None:
    if raw is None:
        return None
    if raw > 1000:
        raw = raw / 1000.0
    return round(raw, 1)


def _core_id_from_label(label: str) -> int | None:
    match = re.search(r"\bcore\s*(\d+)\b", label, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"\bcpu\s*(\d+)\b", label, re.I)
    if match:
        return int(match.group(1))
    return None


def read_temperatures() -> dict[str, Any]:
    sensors: list[dict[str, Any]] = []

    for zone in sorted((HOST_SYS / "class/thermal").glob("thermal_zone*")):
        value = _normalize_temp(_read_number(zone / "temp"))
        if value is not None:
            name = _thermal_zone_name(zone)
            sensors.append({"id": zone.name, "name": name, "temperature": value, "source": "thermal", "core_id": _core_id_from_label(name)})

    for hwmon in sorted((HOST_SYS / "class/hwmon").glob("hwmon*")):
        hwmon_name = _hwmon_name(hwmon)
        for temp_input in sorted(hwmon.glob("temp*_input")):
            number = temp_input.name.removeprefix("temp").removesuffix("_input")
            label = _read_text(hwmon / f"temp{number}_label")
            value = _normalize_temp(_read_number(temp_input))
            if value is not None:
                name = label or hwmon_name
                sensors.append({
                    "id": f"{hwmon.name}:{temp_input.name}",
                    "name": name,
                    "temperature": value,
                    "source": "hwmon",
                    "core_id": _core_id_from_label(name),
                })

    cpu_candidates = [s for s in sensors if re.search(r"cpu|core|package|soc|tctl|tdie|thermal", s["name"], re.I)]
    current = max((s["temperature"] for s in cpu_candidates), default=None)
    if current is None and sensors:
        current = max(s["temperature"] for s in sensors)

    core_temperatures: dict[int, float] = {}
    for sensor in sensors:
        core_id = sensor.get("core_id")
        temperature = sensor.get("temperature")
        if isinstance(core_id, int) and isinstance(temperature, (int, float)):
            core_temperatures[core_id] = temperature

    return {"cpu": current, "core_temperatures": core_temperatures, "sensors": sensors}


def read_thermal_management() -> dict[str, Any]:
    cooling_devices: list[dict[str, Any]] = []
    for device in sorted((HOST_SYS / "class/thermal").glob("cooling_device*")):
        cur_state = _read_number(device / "cur_state")
        max_state = _read_number(device / "max_state")
        cooling_type = _read_text(device / "type") or device.name
        cooling_devices.append({
            "id": device.name,
            "type": cooling_type,
            "cur_state": int(cur_state) if cur_state is not None else None,
            "max_state": int(max_state) if max_state is not None else None,
            "active": bool(cur_state and cur_state > 0),
        })

    thermal_zones: list[dict[str, Any]] = []
    for zone in sorted((HOST_SYS / "class/thermal").glob("thermal_zone*")):
        trips = []
        for trip_temp in sorted(zone.glob("trip_point_*_temp")):
            match = re.fullmatch(r"trip_point_(\d+)_temp", trip_temp.name)
            if not match:
                continue
            index = match.group(1)
            temperature = _normalize_temp(_read_number(trip_temp))
            trips.append({
                "index": int(index),
                "type": _read_text(zone / f"trip_point_{index}_type") or "unknown",
                "temperature": temperature,
            })
        thermal_zones.append({
            "id": zone.name,
            "type": _thermal_zone_name(zone),
            "temperature": _normalize_temp(_read_number(zone / "temp")),
            "trip_points": trips,
        })

    throttle_counts: dict[int, dict[str, int]] = {}
    cpu_root = HOST_SYS / "devices/system/cpu"
    for throttle_path in sorted(cpu_root.glob("cpu[0-9]*/thermal_throttle/*_throttle_count")):
        match = re.search(r"cpu(\d+)/thermal_throttle/(.+)_throttle_count$", str(throttle_path))
        if not match:
            continue
        cpu_id = int(match.group(1))
        counter_name = match.group(2)
        value = _read_number(throttle_path)
        if value is not None:
            throttle_counts.setdefault(cpu_id, {})[counter_name] = int(value)

    return {
        "cooling_devices": cooling_devices,
        "active_cooling_devices": [device for device in cooling_devices if device["active"]],
        "thermal_zones": thermal_zones,
        "cpu_throttle_counts": throttle_counts,
        "throttle_counters_available": bool(throttle_counts),
    }
