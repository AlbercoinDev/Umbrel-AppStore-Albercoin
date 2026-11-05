"""Entry point for the e-lite Umbrel container."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config_writer import AppPaths
from .runtime import RuntimeManager
from .settings import load_settings
from .supervisor import Supervisor
from .tls import TLSManager
from .web import WebApp, make_server

LOG = logging.getLogger("elite")


def configure_logging(paths: AppPaths) -> None:
    paths.ensure()
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    if LOG.handlers:
        return
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    LOG.addHandler(stream)
    file_handler = RotatingFileHandler(paths.controller_log, maxBytes=4 * 1024 * 1024, backupCount=2, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOG.addHandler(file_handler)


def main() -> int:
    root = Path(os.environ.get("E_LITE_DATA_ROOT", "/data"))
    onion_file = Path(
        os.environ.get(
            "E_LITE_ONION_FILE",
            "/tor-data/app-albercoin-store-e-lite-electrum/hostname",
        )
    )
    paths = AppPaths.from_root(root, onion_file)
    paths.ensure()
    configure_logging(paths)

    demo = os.environ.get("E_LITE_DEMO") == "1"
    domain = os.environ.get("DEVICE_DOMAIN_NAME", "umbrel.local").strip() or "umbrel.local"
    hostname = os.environ.get("HOSTNAME", "e-lite").strip() or "e-lite"
    tor_socks = os.environ.get("E_LITE_TOR_SOCKS", "tor:9050").strip() or "tor:9050"

    runtime = RuntimeManager(paths.runtime, log=LOG.info)
    tls = TLSManager(paths, domain=domain, hostname=hostname, log=LOG.info)
    supervisor = Supervisor(
        paths,
        settings_loader=lambda: load_settings(paths.settings_file),
        tls=tls,
        e_lite_binary=runtime.e_lite_binary,
        bitcoin_binary=runtime.bitcoin_binary,
        tor_socks=tor_socks,
        log=LOG.info,
    )
    app = WebApp(paths, runtime, supervisor, tls, domain=domain, hostname=hostname, demo=demo)
    httpd = make_server(app, 3000)

    shutdown = threading.Event()

    def handle_signal(signum: int, _frame: object) -> None:
        LOG.info("received signal %s", signum)
        shutdown.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    if not demo:
        runtime.start()
        tls.ensure()
        supervisor.start()

        def tls_loop() -> None:
            while not shutdown.wait(60):
                tls.ensure()

        threading.Thread(target=tls_loop, name="tls-refresher", daemon=True).start()
    else:
        LOG.info("demo mode enabled; e-lite and Bitcoin Core will not start")

    server_thread = threading.Thread(target=httpd.serve_forever, name="web", daemon=True)
    server_thread.start()
    LOG.info("e-lite Umbrel UI listening on 0.0.0.0:3000")

    while not shutdown.wait(1):
        pass

    LOG.info("shutting down")
    httpd.shutdown()
    httpd.server_close()
    supervisor.stop()
    LOG.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
