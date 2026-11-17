"""Generate e-lite TOML configuration and per-network runtime paths."""

from __future__ import annotations

import re
import tomllib
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
# e-lite's template ships a verified Electrum list only for mainnet. Every
# network gets one so per-txid mode does not fall back to one HTTP request and
# one Tor circuit per txid (plus one per decoy wallet). Checked on 2026-11-15:
# server.version + server.features (genesis), headers and a real tx.get.
ELECTRUM_DEFAULTS: dict[str, tuple[str, ...]] = {
    "testnet4": (
        "mempool.space:40002:s",
        "blackie.c3-soft.com:57010:s",
        "testnet4-electrum.mineracks.com:50002:s",
        "fulcrum.theuplink.net:60002:s",
        "testnet.aranguren.org:52002:s",
    ),
    "signet": (
        "mempool.space:60602:s",
        "node202.fra.mempool.space:60602:s",
        "mempool.tk7.mempool.space:60602:s",
        "signet-electrum.mineracks.com:50002:s",
    ),
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

    @property
    def exports_dir(self) -> Path:
        return self.data / "exports"

    def ensure(self) -> None:
        for directory in (self.data, self.config, self.ca_certificate.parent, self.runtime, self.logs, self.exports_dir):
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

    def network_privacy_file(self, network: str) -> Path:
        return self.network_config_dir(network) / "privacy.toml"

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
    # core.network is left to e-lite unless the operator overrides it: the
    # app must be able to follow upstream performance/privacy defaults.
    for key in ("core.network", "core.timeout_seconds", "core.prune_mib", "core.restart", "core.io_priority"):
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
        "index.max_open_files",
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

    # Keep the plain listener on the LAN by default (Umbrel publishes the port
    # and existing wallets use it). The advanced switch moves it to loopback.
    plain_host = "0.0.0.0" if value("server.plain_lan") else "127.0.0.1"
    server_values: list[tuple[str, Any]] = [
        ("tls", True),
        ("tls_auto_cert", False),
        ("listen", f"{plain_host}:{plain_port}"),
        ("tls_listen", f"0.0.0.0:{tls_port}"),
        ("tls_cert", str(paths.network_certificate_chain(network))),
        ("tls_key", str(paths.network_certificate_key(network))),
        # Sparrow only batches JSON-RPC for banners it recognises; the banner
        # still names e-lite. On by default for a fast first wallet load.
        ("advertise_electrumx_compat", value("server.advertise_electrumx_compat")),
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

    # Performance knobs intentionally follow e-lite's own defaults. The TOML
    # only carries a value when the operator overrides it, so a new upstream
    # release (new timeouts, parallelism, prefetch) is measured as shipped.
    rawtx_values: list[tuple[str, Any]] = []
    for key in (
        "rawtx.enabled",
        "rawtx.cache_mib",
        "rawtx.decoys",
        "rawtx.use_p2p",
        "rawtx.use_esplora",
        "rawtx.use_tor",
        "rawtx.allow_clearnet",
        "rawtx.tor_fallback_clearnet",
        "rawtx.prefetch",
        "rawtx.tor_request_timeout_seconds",
        "rawtx.tor_connect_timeout_seconds",
        "rawtx.tor_stall_timeout_seconds",
        "rawtx.p2p_tor_timeout_seconds",
        "rawtx.max_parallel_fetches",
        "rawtx.path",
        "rawtx.wallet_deadline_seconds",
        "rawtx.txid_attempt_seconds",
        "rawtx.txid_race",
        "rawtx.decoy_sessions",
        "rawtx.sessions_total",
        "rawtx.jitter_ms",
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
        "pruning.pause_core_during_bulk",
        "pruning.core_resume_ahead_gib",
        "pruning.prune_defer_free_gib",
        "pruning.min_gain_blocks",
        "pruning.min_gain_live_blocks",
        "pruning.poll_seconds",
        "pruning.repair_max_gap_blocks",
    ]
    pruning_values = [(key.split(".", 1)[1], value(key)) for key in pruning_keys if present(key)]
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


PRIVACY_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "decoys",
        (
            "privacy.decoys.wallets",
            "privacy.decoys.size_min_ratio",
            "privacy.decoys.size_max_ratio",
            "privacy.decoys.era_margin_blocks",
            "privacy.decoys.seek_attempts",
            "privacy.decoys.growth",
        ),
    ),
    (
        "sessions",
        (
            "privacy.sessions.size",
            "privacy.sessions.min_servers",
            "privacy.sessions.max_share",
            "privacy.sessions.parallel",
            "privacy.sessions.batch_window_ms",
        ),
    ),
    ("blocks", ("privacy.blocks.decoys",)),
)


def build_privacy(network: str, config: NetworkConfig) -> str:
    """Generate the per-network `privacy.toml` (AUD-163).

    Only the preset and the values the operator changed are written; the rest
    of each preset is applied by e-lite. `privacy.mode = "preset"` is the
    panel's way of saying "follow the preset" and is never written.
    """
    if network not in NETWORKS:
        raise ValueError("unknown network")
    lines = [
        "# Generated by the e-lite Umbrel app from Configuración -> Privacidad.",
        "# Manual edits are overwritten on the next start.",
        "# Values not listed here use the selected preset.",
        "",
        f'preset = {toml_string(str(config.value("privacy.preset")))}',
    ]
    mode = config.overrides.get("privacy.mode")
    if mode in ("tx", "block"):
        lines.append(f"mode = {toml_string(str(mode))}")
    lines.append("")
    for section, keys in PRIVACY_SECTIONS:
        values = [
            (key.rsplit(".", 1)[1], config.overrides[key])
            for key in keys
            if key in config.overrides
        ]
        _section(lines, section, values)
    return "\n".join(lines).rstrip() + "\n"



def _section_bounds(lines: list[str], network: str) -> tuple[int, int] | None:
    header = f"[{network}]"
    for index, line in enumerate(lines):
        if line.strip() == header:
            end = len(lines)
            for other in range(index + 1, len(lines)):
                if lines[other].lstrip().startswith("["):
                    end = other
                    break
            return index, end
    return None


def _key_line(lines: list[str], start: int, end: int, key: str) -> int | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index in range(start + 1, end):
        if pattern.match(lines[index]):
            return index
    return None


def _write_array(lines: list[str], start: int, end: int, key: str, values: tuple[str, ...] | list[str]) -> bool:
    replacement = [f"{key} = ["] + [f'    "{value}",' for value in values] + ["]"]
    index = _key_line(lines, start, end, key)
    if index is not None:
        last = index
        if "[" in lines[index] and "]" not in lines[index]:
            while last + 1 < end and "]" not in lines[last]:
                last += 1
        lines[index:last + 1] = replacement
        return True
    insert_at = end
    while insert_at > start + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    lines[insert_at:insert_at] = replacement + [""]
    return True


def public_server_lists(path: Path, network: str) -> tuple[list[str], list[str], str]:
    """Read the provider lists of one network (read-only, for the panel)."""
    if not path.is_file():
        return [], [], "missing"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return [], [], str(exc)
    except tomllib.TOMLDecodeError as exc:
        return [], [], str(exc)
    if not isinstance(data, dict):
        return [], [], "not an object"
    net = data.get(network)
    if not isinstance(net, dict):
        return [], [], "missing network"
    esplora = net.get("esplora")
    electrum = net.get("electrum")
    return (
        [str(item) for item in esplora] if isinstance(esplora, list) else [],
        [str(item) for item in electrum] if isinstance(electrum, list) else [],
        "",
    )


def ensure_public_servers(path: Path, network: str, template: Path | None = None) -> None:
    """Fill empty or missing provider lists without touching manual edits.

    e-lite ships `electrum = []` for testnet4 and signet; the app fills it with
    verified servers. An existing non-empty list always wins. An empty list is
    only replaced while the network's `esplora` still matches the shipped
    template (an untouched copy); a customised file is left alone.
    """
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
        data = tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError):
        return
    if not isinstance(data, dict):
        return
    current = data.get(network)
    if not isinstance(current, dict):
        return
    template_net: dict[str, Any] = {}
    if template is not None and template.is_file():
        try:
            template_data = tomllib.loads(template.read_text(encoding="utf-8"))
            candidate = template_data.get(network)
            if isinstance(candidate, dict):
                template_net = candidate
        except (OSError, tomllib.TOMLDecodeError):
            pass

    lines = text.splitlines()
    bounds = _section_bounds(lines, network)
    if bounds is None:
        return
    start, end = bounds
    changed = False

    esplora = current.get("esplora")
    if not esplora and template_net.get("esplora"):
        changed |= _write_array(lines, start, end, "esplora", list(template_net["esplora"]))
        bounds = _section_bounds(lines, network)
        if bounds is None:
            return
        start, end = bounds

    desired = list(ELECTRUM_DEFAULTS.get(network, ())) or list(template_net.get("electrum") or [])
    electrum = current.get("electrum")
    untouched = current.get("esplora") == template_net.get("esplora")
    if "electrum" not in current and desired:
        changed |= _write_array(lines, start, end, "electrum", desired)
    elif electrum == [] and untouched and desired:
        changed |= _write_array(lines, start, end, "electrum", desired)

    if changed:
        write_config(path, "\n".join(lines) + "\n")



def write_config(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(contents, encoding="utf-8")
    tmp.replace(path)
