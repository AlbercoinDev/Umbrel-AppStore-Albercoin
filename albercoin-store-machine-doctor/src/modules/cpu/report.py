from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORT_DIR = Path("/data/reports")


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def build_report(state: dict[str, Any]) -> dict[str, Any]:
    samples = state.get("samples", [])
    temperatures = [s.get("temperature_cpu") for s in samples if isinstance(s.get("temperature_cpu"), (int, float))]
    usages = [s.get("cpu_total_usage") for s in samples if isinstance(s.get("cpu_total_usage"), (int, float))]
    cpu_info = state.get("cpu_info", {})
    started_at = state.get("started_at") or ""
    finished_at = state.get("finished_at") or datetime.now(UTC).isoformat()

    return {
        "app": "Machine Doctor",
        "test_type": "cpu",
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": state.get("duration_seconds"),
        "status": state.get("result", "WARNING"),
        "summary": {
            "cpu_model": cpu_info.get("model", "Unknown CPU"),
            "cores": cpu_info.get("cores", 0),
            "max_temperature": max(temperatures) if temperatures else None,
            "average_temperature": _average(temperatures),
            "max_cpu_usage": max(usages) if usages else None,
            "average_cpu_usage": _average(usages),
        },
        "warnings": state.get("warnings", []),
        "errors": state.get("errors", []),
        "samples": samples,
        "stress_ng": state.get("stress_ng", {}),
    }


def save_report(report: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = REPORT_DIR / f"cpu-{timestamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def latest_report() -> dict[str, Any] | None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    reports = sorted(REPORT_DIR.glob("cpu-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        return None
    try:
        return json.loads(reports[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
