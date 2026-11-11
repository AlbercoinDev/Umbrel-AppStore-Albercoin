"""Generate e-lite TOML configuration and per-network runtime paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .settings import FIELDS_BY_KEY, NETWORKS, NetworkConfig

NETWORK_PORTS: dict[str, tuple[int, int]] = {
    "mainnet": (55011, 55022),
    "testnet4": (55111, 55122),
    "signet": (55211, 55222),
}
NETWORK_METRICS: dict[str, int] = {
    "mainnet": 9184,
    "testnet4": 9185,
    "signet": 9186,
}
NETWORK_TOR_SOCKS: dict[str, int] = {
    "mainnet": 9050,
    "testnet4": 19050,
    "signet": 19051,
}


@dataclass(frozen=True)
class AppPaths:
    data: Path
    config: Path
    settings_file: Path
    runtime: Path
    logs: Path
    controller_log: Path
    ca_certificate: Path
    ca_key: Path

    @classmethod
    def from_root(cls, root: Path) -> "AppPaths":
        config = root / "config"
        tls = config / "tls"
        return cls(
            data=root,
            config=config,
            settings_file=config / "settings.json",
            runtime=root / "runtime",
            logs=root / "logs",
            controller_log=root / "logs" / "controller.log",
            ca_certificate=tls / "ca.crt",
            ca_key=tls / "ca.key",
        )

    def ensure(self) -> None:
        for directory in (self.data, self.config, self.ca_certificate.parent, self.runtime, self.logs):
            directory.mkdir(parents=True, exist_ok=True)
        for network in NETWORKS:
            self.network_e_lite_data(network).mkdir(parents=True, exist_ok=True)
            self.network_core_data(network).mkdir(parents=True, exist_ok=True)
            self.network_tls_dir(network).mkdir(parents=True, exist_ok=True)
            self.network_tor_dir(network).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- per network
    def network_root(self, network: str) -> Path:
        return self.data / "networks" / network

    def network_e_lite_data(self, network: str) -> Path:
        return self.network_root(network) / "e-lite"

    def network_core_data(self, network: str) -> Path:
        return self.network_root(network) / "core"

    def network_config_dir(self, network: str) -> Path:
        return self.config / "networks" / network

    def network_config_file(self, network: str) -> Path:
        return self.network_config_dir(network) / "e-lite.toml"

    def network_tls_dir(self, network: str) -> Path:
        return self.network_config_dir(network) / "tls"

    def network_certificates(self, network: str) -> Path:
        return self.network_tls_dir(network) / "server.crt"

    def network_certificate_key(self, network: str) -> Path:
        return self.network_tls_dir(network) / "server.key"

    def network_certificate_chain(self, network: str) -> Path:
        return self.network_tls_dir(network) / "fullchain.pem"

    def network_sans_marker(self, network: str) -> Path:
        return self.network_tls_dir(network) / "sans.txt"

    def network_tor_dir(self, network: str) -> Path:
        return self.network_config_dir(network) / "tor"

    def network_public_servers(self, network: str) -> Path:
        return self.network_config_dir(network) / "public-servers.toml"

    def network_onion_file(self, network: str) -> Path:
        return self.network_tor_dir(network) / "onion" / "hostname"

    def network_e_lite_log(self, network: str) -> Path:
        return self.network_e_lite_data(network) / "e-lite.log"

    def network_core_log(self, network: str) -> Path:
        return self.network_e_lite_data(network) / "bitcoind.log"

    def network_core_pid(self, network: str) -> Path:
        return self.network_e_lite_data(network) / "core-child.pid"

    def network_console_log(self, network: str) -> Path:
        return self.logs / f"e-lite-{network}-console.log"


def network_ports(network: str) -> tuple[int, int]:
    return NETWORK_PORTS[network]


def network_metrics_port(network: str) -> int:
    return NETWORK_METRICS[network]


def toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_string(str(item)) for item in value) + "]"
    return toml_string(str(value))


def _section(lines: list[str], name: str, values: list[tuple[str, Any]]) -> None:
    if not values:
        return
    lines.append(f"[{name}]")
    for key, value in values:
        lines.append(f"{key} = {toml_value(value)}")
    lines.append("")


def total_memory_mib() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def network_dbcache_mib(total_mib: int, networks: int) -> int:
    if total_mib <= 0 or networks < 1:
        return 0
    return max(450, min(2048, total_mib // (4 * networks)))


def build_config(
    network: str,
    config: NetworkConfig,
    paths: AppPaths,
    *,
    bitcoin_binary: Path,
    tor_binary: Path,
    active_networks: int = 1,
    total_mib: int | None = None,
) -> str:
    if network not in NETWORKS:
        raise ValueError("unknown network")
    plain_port, tls_port = network_ports(network)
    metrics_port = network_metrics_port(network)

    def value(key: str) -> Any:
        return config.overrides.get(key, FIELDS_BY_KEY[key].default)

    def present(key: str) -> bool:
        return key in config.overrides

    lines: list[str] = [
        "# Generated by the e-lite Umbrel app. Manual edits are overwritten.",
        "# Values not listed here use e-lite's own defaults.",
        "",
    ]

    _section(
        lines,
        "network",
        [
            ("name", network),
            ("data_dir", str(paths.network_e_lite_data(network))),
        ],
    )

    core_values: list[tuple[str, Any]] = [
        ("manage", "managed"),
        ("bitcoind", str(bitcoin_binary)),
        ("core_datadir", str(paths.network_core_data(network))),
        ("require_loopback_rpc", True),
    ]
    for key in ("core.timeout_seconds", "core.prune_mib", "core.restart", "core.io_priority", "core.cgroup_io_weight"):
        if present(key):
            core_values.append((key.split(".", 1)[1], value(key)))
    if present("core.extra_args") and value("core.extra_args"):
        core_values.append(("extra_args", list(value("core.extra_args"))))
    elif active_networks > 1:
        # e-lite sizes each managed Core's -dbcache from total RAM. With
        # several networks that budget is duplicated per instance, so cap it
        # here unless the operator set their own extra_args.
        dbcache = network_dbcache_mib(total_memory_mib() if total_mib is None else total_mib, active_networks)
        if dbcache:
            core_values.append(("extra_args", [f"-dbcache={dbcache}"]))
    _section(lines, "core", core_values)

    index_keys = [
        "index.profile",
        "index.adaptive",
        "index.cache_mib",
        "index.write_buffer_mib",
        "index.memory_reserve_mib",
        "index.memory_fraction",
        "index.background_jobs",
        "index.readers",
        "index.commit_interval_ms",
        "index.prefetch_deadline_ms",
        "index.adaptive_memtables",
        "index.max_open_files",
        "index.zfs_arc_dynamic",
        "index.zfs_arc_max_mib",
        "index.zfs_arc_min_mib",
        "index.commit_sync",
        "index.allow_low_memory",
        "index.batch_blocks",
        "index.reorg_window",
    ]
    index_values = [(key.split(".", 1)[1], value(key)) for key in index_keys if present(key)]
    # Every instance budgets memory independently. When several networks share
    # the same host, split e-lite's memory ceiling instead of letting each one
    # plan for the full default (0.75 of total RAM).
    if active_networks > 1 and not present("index.memory_fraction"):
        index_values.append(("memory_fraction", round(0.60 / active_networks, 2)))
    _section(lines, "index", index_values)

    server_values: list[tuple[str, Any]] = [
        ("tls", True),
        ("tls_auto_cert", False),
        ("listen", f"0.0.0.0:{plain_port}"),
        ("tls_listen", f"0.0.0.0:{tls_port}"),
        ("tls_cert", str(paths.network_certificate_chain(network))),
        ("tls_key", str(paths.network_certificate_key(network))),
    ]
    if present("server.max_clients"):
        server_values.append(("max_clients", value("server.max_clients")))
    for key in ("server.rate_limit", "server.rate_burst"):
        if present(key):
            server_values.append((key.split(".", 1)[1], value(key)))
    _section(lines, "server", server_values)

    _section(
        lines,
        "metrics",
        [
            ("enabled", True),
            ("listen", f"127.0.0.1:{metrics_port}"),
        ],
    )

    rawtx_values: list[tuple[str, Any]] = []
    for key in (
        "rawtx.enabled",
        "rawtx.cache_mib",
        "rawtx.decoys",
        "rawtx.use_p2p",
        "rawtx.use_esplora",
        "rawtx.allow_clearnet",
    ):
        if present(key):
            rawtx_values.append((key.split(".", 1)[1], value(key)))
    if present("rawtx.p2p_peers") and value("rawtx.p2p_peers"):
        rawtx_values.append(("p2p_peers", list(value("rawtx.p2p_peers"))))
    _section(lines, "rawtx", rawtx_values)

    pruning_keys = [
        "pruning.allow_auto_prune",
        "pruning.min_free_gib",
        "pruning.critical_free_gib",
        "pruning.max_bulk_lag_gib",
        "pruning.max_bulk_lag_blocks",
        "pruning.scheduler",
        "pruning.pause_core_during_bulk",
        "pruning.core_resume_ahead_gib",
        "pruning.core_resume_ahead_blocks",
        "pruning.prune_defer_free_gib",
        "pruning.min_gain_blocks",
        "pruning.poll_seconds",
        "pruning.repair_max_gap_blocks",
    ]
    pruning_values = [(key.split(".", 1)[1], value(key)) for key in pruning_keys if present(key)]
    # The byte watermarks take precedence in 29.8.0. If the operator kept using
    # the legacy block knobs, disable the GiB values so their choice still wins.
    for legacy, gib in (
        ("pruning.max_bulk_lag_blocks", "pruning.max_bulk_lag_gib"),
        ("pruning.core_resume_ahead_blocks", "pruning.core_resume_ahead_gib"),
    ):
        if present(legacy) and not present(gib):
            pruning_values.append((gib.split(".", 1)[1], 0))
    _section(lines, "pruning", pruning_values)

    _section(
        lines,
        "public_servers",
        [
            ("file", str(paths.network_public_servers(network))),
            ("generate", True),
        ],
    )

    logging_values: list[tuple[str, Any]] = [("file", str(paths.network_e_lite_log(network)))]
    if present("logging.max_mib"):
        logging_values.append(("max_mib", value("logging.max_mib")))
    if present("logging.level"):
        logging_values.append(("level", value("logging.level")))
    _section(lines, "logging", logging_values)

    # e-lite manages its own Tor per network. The wildcard listener is mapped
    # to loopback by e-lite and the onion ports mirror the Electrum listeners.
    _section(
        lines,
        "tor",
        [
            ("mode", "managed"),
            ("binary", str(tor_binary)),
            ("data_dir", str(paths.network_tor_dir(network))),
            ("plain", True),
            ("tls", True),
            ("onion_port", 0),
            ("onion_tls_port", 0),
            ("socks_port", NETWORK_TOR_SOCKS[network]),
        ],
    )

    _section(lines, "safety", [("allow_mainnet", network == "mainnet")])

    return "\n".join(lines).rstrip() + "\n"


def write_config(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(contents, encoding="utf-8")
    tmp.replace(path)
