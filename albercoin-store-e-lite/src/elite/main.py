"""Entry point for the e-lite Umbrel container."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .backup import BackupManager
from .config_writer import AppPaths
from .runtime import RuntimeManager
from .settings import NETWORKS, load_settings
from .supervisor import SupervisorManager
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
    paths = AppPaths.from_root(root)
    paths.ensure()
    configure_logging(paths)
    LOG.info("starting e-lite multi-network wrapper (data=%s)", paths.data)

    demo = os.environ.get("E_LITE_DEMO") == "1"
    domain = os.environ.get("DEVICE_DOMAIN_NAME", "umbrel.local").strip() or "umbrel.local"
    hostname = os.environ.get("HOSTNAME", "e-lite").strip() or "e-lite"

    runtime = RuntimeManager(paths.runtime, log=LOG.info)

    def public_hostnames(network: str) -> list[str]:
        try:
            return list(load_settings(paths.settings_file).network(network).public_hostnames)
        except Exception:
            return []

    tls_managers = {
        network: TLSManager(
            paths,
            network,
            domain=domain,
            hostname=hostname,
            public_hostnames_loader=lambda n=network: public_hostnames(n),
            log=LOG.info,
        )
        for network in NETWORKS
    }
    supervisor = SupervisorManager(
        paths,
        settings_loader=lambda: load_settings(paths.settings_file),
        tls_managers=tls_managers,
        e_lite_binary=runtime.e_lite_binary,
        bitcoin_binary=runtime.bitcoin_binary,
        tor_binary=runtime.tor_binary,
        log=LOG.info,
    )
    backup = BackupManager(paths, supervisor, lambda: load_settings(paths.settings_file), log=LOG.info)
    app = WebApp(
        paths,
        runtime,
        supervisor,
        tls_managers,
        domain=domain,
        hostname=hostname,
        demo=demo,
        backup=backup,
    )
    httpd = make_server(app, 3000)

    # Start the UI first so Umbrel's LAN ingress can create the public route
    # even while Tor, Core or the first-run downloads are still in progress.
    server_thread = threading.Thread(target=httpd.serve_forever, name="web", daemon=True)
    server_thread.start()
    LOG.info("e-lite Umbrel UI listening on 0.0.0.0:3000")

    shutdown = threading.Event()

    def handle_signal(signum: int, _frame: object) -> None:
        LOG.info("received signal %s", signum)
        shutdown.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    if not demo:
        runtime.start()
        for manager in tls_managers.values():
            manager.ensure()
        supervisor.start()

        def tls_loop() -> None:
            while not shutdown.wait(15):
                for manager in tls_managers.values():
                    manager.ensure()

        threading.Thread(target=tls_loop, name="tls-refresher", daemon=True).start()
    else:
        LOG.info("demo mode enabled; e-lite, Bitcoin Core and Tor will not start")

    while not shutdown.wait(1):
        pass

    LOG.info("shutting down")
    httpd.shutdown()
    httpd.server_close()
    supervisor.stop()
    LOG.info("stopped")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        LOG.exception("fatal startup error")
        raise
