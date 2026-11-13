"""Inspect and clear e-lite's rawtx cache (the only memoised on-disk cache)."""

from __future__ import annotations

import json
import os
import subprocess

from .config_writer import AppPaths
from .runtime import E_LITE_VERSION


class CacheError(RuntimeError):
    """Raised when e-lite cannot report or clear the cache."""


def _binary(paths: AppPaths):
    return paths.runtime / f"e-lite-{E_LITE_VERSION}" / "e-lite"


def _run(paths: AppPaths, network: str, action: str, *, yes: bool = False, timeout: int = 60) -> dict:
    binary = _binary(paths)
    if not binary.is_file():
        raise CacheError("e-lite todavía no está instalado; espera a que termine la descarga")
    command = [
        str(binary),
        "cache",
        action,
        "--config",
        str(paths.network_config_file(network)),
        "--json",
    ]
    if yes:
        command.append("--yes")
    env = os.environ.copy()
    env["HOME"] = str(paths.data)
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CacheError(f"no se pudo ejecutar e-lite cache {action}: {exc}") from exc
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "").strip()
        raise CacheError(detail or f"e-lite cache {action} terminó con código {process.returncode}")
    # e-lite prints its log-level notice on stdout before the JSON document.
    text = process.stdout or ""
    start = text.find("{")
    if start < 0:
        raise CacheError("respuesta no válida de e-lite cache")
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError as exc:
        raise CacheError("respuesta no válida de e-lite cache") from exc
    if not isinstance(data, dict):
        raise CacheError("respuesta no válida de e-lite cache")
    return data


def cache_status(paths: AppPaths, network: str) -> dict:
    """Size, limits and free space of the network's rawtx cache."""
    return _run(paths, network, "status", timeout=30)


def cache_clear(paths: AppPaths, network: str) -> dict:
    """Delete only cache files (e-lite refuses any unsafe path)."""
    return _run(paths, network, "clear", yes=True, timeout=300)
