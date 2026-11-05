from __future__ import annotations

import tempfile
from pathlib import Path

from elite.config_writer import AppPaths
from elite.runtime import RuntimeManager
from elite.supervisor import Supervisor
from elite.tls import TLSManager
from elite.web import WebApp


def temp_paths() -> tuple[tempfile.TemporaryDirectory[str], AppPaths]:
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    paths = AppPaths.from_root(root, root / "tor-data" / "app-albercoin-store-e-lite-electrum" / "hostname")
    paths.ensure()
    return directory, paths


def demo_app(paths: AppPaths) -> WebApp:
    runtime = RuntimeManager(paths.runtime)
    tls = TLSManager(paths, "umbrel.local", "e-lite")
    supervisor = Supervisor(
        paths,
        settings_loader=lambda: None,  # type: ignore[arg-type]
        tls=tls,
        e_lite_binary=runtime.e_lite_binary,
        bitcoin_binary=runtime.bitcoin_binary,
        tor_socks="tor:9050",
    )
    return WebApp(paths, runtime, supervisor, tls, "umbrel.local", "e-lite", demo=True)
