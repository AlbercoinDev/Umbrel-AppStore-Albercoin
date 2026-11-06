from __future__ import annotations

import tempfile
from pathlib import Path

from elite.config_writer import AppPaths
from elite.runtime import RuntimeManager
from elite.settings import NETWORKS, load_settings
from elite.supervisor import SupervisorManager
from elite.tls import TLSManager
from elite.web import WebApp


def temp_paths() -> tuple[tempfile.TemporaryDirectory[str], AppPaths]:
    directory = tempfile.TemporaryDirectory()
    paths = AppPaths.from_root(Path(directory.name))
    paths.ensure()
    return directory, paths


def demo_app(paths: AppPaths) -> WebApp:
    runtime = RuntimeManager(paths.runtime)
    tls_managers = {
        network: TLSManager(paths, network, "umbrel.local", "e-lite")
        for network in NETWORKS
    }
    supervisor = SupervisorManager(
        paths,
        settings_loader=lambda: load_settings(paths.settings_file),
        tls_managers=tls_managers,
        e_lite_binary=runtime.e_lite_binary,
        bitcoin_binary=runtime.bitcoin_binary,
        tor_binary=runtime.tor_binary,
    )
    return WebApp(paths, runtime, supervisor, tls_managers, "umbrel.local", "e-lite", demo=True)
