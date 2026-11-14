"""Per-network settings for the e-lite Umbrel app.

A settings document stores one independent configuration for each supported
Bitcoin network.  Only values explicitly overridden by the user are stored; a
field that is absent from ``overrides`` is omitted from the generated e-lite
TOML file, so e-lite owns its default value.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NETWORKS = ("mainnet", "testnet4", "signet")
SCHEMA_VERSION = 2

GROUPS = ("performance", "server", "pruning", "core", "advanced")


@dataclass(frozen=True)
class Field:
    key: str
    kind: str
    default: Any
    group: str = "performance"
    minimum: float | None = None
    maximum: float | None = None
    options: tuple[str, ...] = ()
    advanced: bool = False

    @property
    def name(self) -> str:
        return self.key.replace(".", "_")


FIELDS: tuple[Field, ...] = (
    Field("index.profile", "enum", "auto", "performance", options=("auto", "hdd", "ssd", "nvme", "manual")),
    Field("index.adaptive", "bool", True, "performance"),
    Field("index.cache_mib", "int", 0, "performance", minimum=0, maximum=262144),
    Field("index.write_buffer_mib", "int", 0, "performance", minimum=0, maximum=131072),
    Field("index.memory_reserve_mib", "int", 0, "performance", minimum=0, maximum=131072),
    Field("index.memory_fraction", "float", 0.0, "performance", minimum=0.0, maximum=1.0),
    Field("index.background_jobs", "int", 0, "performance", minimum=0, maximum=32),
    Field("index.readers", "int", 0, "performance", minimum=0, maximum=64),
    Field("index.commit_interval_ms", "int", 0, "performance", minimum=0, maximum=30000),
    Field("index.prefetch_deadline_ms", "int", 0, "performance", minimum=0, maximum=60000, advanced=True),
    Field("index.max_open_files", "int", 0, "performance", minimum=0, maximum=1048576, advanced=True),
    Field("index.zfs_arc_dynamic", "bool", True, "performance", advanced=True),
    Field("index.zfs_arc_max_mib", "int", 0, "performance", minimum=0, maximum=1048576, advanced=True),
    Field("index.zfs_arc_min_mib", "int", 1024, "performance", minimum=512, maximum=1048576, advanced=True),
    Field("index.adaptive_memtables", "bool", False, "performance", advanced=True),
    Field("index.commit_sync", "enum", "auto", "performance", options=("auto", "always", "txids"), advanced=True),
    Field("index.allow_low_memory", "bool", False, "performance", advanced=True),
    Field("index.batch_blocks", "int", 256, "performance", minimum=0, maximum=4096),
    Field("index.reorg_window", "int", 288, "performance", minimum=1, maximum=100000),
    Field("server.max_clients", "int", 32, "server", minimum=1, maximum=1024),
    Field("server.rate_limit", "optional_int", None, "server", minimum=0, maximum=10000),
    Field("server.rate_burst", "optional_int", None, "server", minimum=1, maximum=1000000),
    Field("server.plain_lan", "bool", True, "server", advanced=True),
    Field("server.advertise_electrumx_compat", "bool", True, "server", advanced=True),
    Field("rawtx.enabled", "bool", True, "server"),
    Field("rawtx.cache_mib", "int", 256, "server", minimum=0, maximum=262144),
    Field("rawtx.decoys", "int", 1, "server", minimum=0, maximum=32),
    Field("rawtx.use_p2p", "bool", True, "server"),
    Field("rawtx.use_esplora", "bool", True, "server"),
    Field("rawtx.allow_clearnet", "bool", False, "server", advanced=True),
    Field("rawtx.tor_fallback_clearnet", "bool", False, "server", advanced=True),
    Field("rawtx.prefetch", "bool", True, "server", advanced=True),
    Field("rawtx.tor_request_timeout_seconds", "int", 60, "server", minimum=1, maximum=300, advanced=True),
    Field("rawtx.tor_connect_timeout_seconds", "int", 10, "server", minimum=1, maximum=300, advanced=True),
    Field("rawtx.tor_stall_timeout_seconds", "int", 12, "server", minimum=1, maximum=300, advanced=True),
    Field("rawtx.p2p_tor_timeout_seconds", "int", 30, "server", minimum=1, maximum=300, advanced=True),
    Field("rawtx.max_parallel_fetches", "int", 8, "server", minimum=1, maximum=16, advanced=True),
    Field("rawtx.p2p_peers", "list", (), "server", advanced=True),
    Field("core.prune_mib", "int", 1, "pruning", minimum=0, maximum=1000000),
    Field("pruning.allow_auto_prune", "bool", False, "pruning", advanced=True),
    Field("pruning.min_free_gib", "int", 10, "pruning", minimum=0, maximum=100000),
    Field("pruning.critical_free_gib", "int", 1, "pruning", minimum=0, maximum=100000),
    Field("pruning.max_bulk_lag_gib", "int", 30, "pruning", minimum=0, maximum=1000000),
    Field("pruning.max_bulk_lag_blocks", "int", 20000, "pruning", minimum=0, maximum=1000000),
    Field("pruning.scheduler", "enum", "overlap", "pruning", options=("overlap", "alternate"), advanced=True),
    Field("pruning.pause_core_during_bulk", "bool", True, "pruning"),
    Field("pruning.core_resume_ahead_gib", "int", 7, "pruning", minimum=0, maximum=1000000),
    Field("pruning.core_resume_ahead_blocks", "int", 5000, "pruning", minimum=0, maximum=1000000),
    Field("pruning.prune_defer_free_gib", "int", 20, "pruning", minimum=0, maximum=100000),
    Field("pruning.min_gain_blocks", "int", 5000, "pruning", minimum=0, maximum=1000000),
    Field("pruning.min_gain_live_blocks", "int", 144, "pruning", minimum=0, maximum=1000000),
    Field("pruning.poll_seconds", "int", 10, "pruning", minimum=1, maximum=3600),
    Field("pruning.repair_max_gap_blocks", "int", 2048, "pruning", minimum=0, maximum=100000),
    Field("core.network", "enum", "auto", "core", options=("auto", "private", "clearnet"), advanced=True),
    Field("core.timeout_seconds", "int", 30, "core", minimum=1, maximum=600),
    Field("core.io_priority", "int", 0, "core", minimum=0, maximum=7, advanced=True),
    Field("core.cgroup_io_weight", "int", 0, "core", minimum=0, maximum=10000, advanced=True),
    Field("core.extra_args", "list", (), "core", advanced=True),
    Field("core.restart", "bool", True, "core", advanced=True),
    Field("logging.max_mib", "int", 16, "core", minimum=0, maximum=1024, advanced=True),
    Field("logging.level", "enum", "debug", "core", options=("info", "debug", "trace"), advanced=True),
)

FIELDS_BY_KEY = {f.key: f for f in FIELDS}

_IPV4_PORT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{1,252}:[0-9]{1,5}$")
_IPV6_PORT = re.compile(r"^\[[0-9A-Fa-f:]+\]:[0-9]{1,5}$")
_HOSTNAME = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")


class SettingsError(ValueError):
    """Raised when a settings document or submitted form is invalid."""


def normalize_public_hostnames(values: Any) -> list[str]:
    if isinstance(values, str):
        items = re.split(r"[,\s]+", values)
    elif isinstance(values, (list, tuple)):
        items = [str(item) for item in values]
    else:
        raise SettingsError("public_hostnames must be a list")
    result: list[str] = []
    for item in items:
        host = item.strip().lower().rstrip(".")
        if not host:
            continue
        if not _HOSTNAME.match(host):
            raise SettingsError(f"public_hostnames: invalid hostname {item!r}")
        if host not in result:
            result.append(host)
    return result


@dataclass
class NetworkConfig:
    enabled: bool = False
    overrides: dict[str, Any] = field(default_factory=dict)
    public_hostnames: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "overrides": dict(sorted(self.overrides.items())),
            "public_hostnames": list(self.public_hostnames),
        }

    def value(self, key: str) -> Any:
        return self.overrides.get(key, FIELDS_BY_KEY[key].default)

    def is_default(self, key: str) -> bool:
        return key not in self.overrides


@dataclass
class Settings:
    networks: dict[str, NetworkConfig] = field(
        default_factory=lambda: {name: NetworkConfig() for name in NETWORKS}
    )
    setup_complete: bool = False

    @property
    def enabled_networks(self) -> list[str]:
        return [name for name in NETWORKS if self.networks[name].enabled]

    @property
    def any_enabled(self) -> bool:
        return bool(self.enabled_networks)

    def network(self, name: str) -> NetworkConfig:
        return self.networks[name]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "setup_complete": self.setup_complete,
            "networks": {name: self.networks[name].as_dict() for name in NETWORKS},
        }


def default_settings() -> Settings:
    return Settings()


def load_settings(path: Path) -> Settings:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default_settings()
    except OSError as exc:
        raise SettingsError(f"cannot read settings: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SettingsError(f"settings file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SettingsError("settings file must contain an object")

    # Old single-network documents are intentionally not migrated: the new
    # multi-network layout starts from a clean setup.
    if data.get("schema") != SCHEMA_VERSION or not isinstance(data.get("networks"), dict):
        return default_settings()

    networks: dict[str, NetworkConfig] = {}
    for name in NETWORKS:
        raw_network = data["networks"].get(name, {})
        if not isinstance(raw_network, dict):
            raw_network = {}
        overrides: dict[str, Any] = {}
        raw_overrides = raw_network.get("overrides", {})
        if isinstance(raw_overrides, dict):
            for key, value in raw_overrides.items():
                field_def = FIELDS_BY_KEY.get(key)
                if field_def is None:
                    continue
                try:
                    overrides[key] = _coerce(field_def, value)
                except SettingsError:
                    continue
        try:
            public_hostnames = normalize_public_hostnames(raw_network.get("public_hostnames", []))
        except SettingsError:
            public_hostnames = []
        networks[name] = NetworkConfig(
            enabled=bool(raw_network.get("enabled", False)),
            overrides=overrides,
            public_hostnames=public_hostnames,
        )
    return Settings(networks=networks, setup_complete=bool(data.get("setup_complete", False)))


def save_settings(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings.as_dict(), indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _coerce(field_def: Field, value: Any) -> Any:
    kind = field_def.kind
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise SettingsError(f"{field_def.key} must be a boolean")
    if kind == "optional_int":
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if isinstance(value, bool):
            raise SettingsError(f"{field_def.key} must be an integer")
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{field_def.key} must be an integer") from exc
        return _check_range(field_def, number)
    if kind == "int":
        if isinstance(value, bool):
            raise SettingsError(f"{field_def.key} must be an integer")
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{field_def.key} must be an integer") from exc
        return _check_range(field_def, number)
    if kind == "float":
        if isinstance(value, bool):
            raise SettingsError(f"{field_def.key} must be a number")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{field_def.key} must be a number") from exc
        return _check_range(field_def, number)
    if kind == "enum":
        text = str(value).strip()
        if text not in field_def.options:
            raise SettingsError(f"{field_def.key} must be one of {', '.join(field_def.options)}")
        return text
    if kind == "list":
        if isinstance(value, (list, tuple)):
            items = [str(item).strip() for item in value]
        elif isinstance(value, str):
            items = [line.strip() for line in value.splitlines()]
        else:
            raise SettingsError(f"{field_def.key} must be a list")
        return [item for item in items if item]
    return str(value).strip()


def _check_range(field_def: Field, number: float) -> Any:
    if field_def.minimum is not None and number < field_def.minimum:
        raise SettingsError(f"{field_def.key} must be at least {field_def.minimum:g}")
    if field_def.maximum is not None and number > field_def.maximum:
        raise SettingsError(f"{field_def.key} must be at most {field_def.maximum:g}")
    return int(number) if field_def.kind in ("int", "optional_int") else float(number)


def validate_network(network: str, config: NetworkConfig) -> list[str]:
    errors: list[str] = []
    for key, value in config.overrides.items():
        field_def = FIELDS_BY_KEY.get(key)
        if field_def is None:
            errors.append(f"{key}: unknown setting")
            continue
        try:
            _coerce(field_def, value)
        except SettingsError as exc:
            errors.append(str(exc))
        if key == "rawtx.p2p_peers":
            for peer in value if isinstance(value, (list, tuple)) else []:
                if not (_IPV4_PORT.match(str(peer)) or _IPV6_PORT.match(str(peer))):
                    errors.append(f"{key}: invalid peer {peer!r}; use host:port")
        if key == "core.extra_args":
            for arg in value if isinstance(value, (list, tuple)) else []:
                text = str(arg)
                if not text.startswith("-"):
                    errors.append(f"{key}: each argument must start with '-'")
                if any(ch in text for ch in "\r\n\0"):
                    errors.append(f"{key}: arguments cannot contain newlines or NUL")

    prune = config.overrides.get("core.prune_mib")
    if prune is not None and 1 < int(prune) < 550:
        errors.append("core.prune_mib: use 0 (full), 1 (manual) or >= 550 (automatic)")
    if prune is not None and int(prune) >= 550 and not bool(config.overrides.get("pruning.allow_auto_prune", False)):
        errors.append("pruning.allow_auto_prune: must be enabled when core.prune_mib >= 550")

    min_free = config.overrides.get("pruning.min_free_gib")
    critical = config.overrides.get("pruning.critical_free_gib")
    if min_free is not None and critical is not None and int(critical) > int(min_free):
        errors.append("pruning.critical_free_gib: cannot be greater than pruning.min_free_gib")

    return errors


def validate_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    for name in NETWORKS:
        if name not in settings.networks:
            errors.append(f"{name}: missing network settings")
            continue
        for error in validate_network(name, settings.networks[name]):
            errors.append(f"{name}: {error}")
    return errors


def parse_form(form: dict[str, list[str]], network: str, previous: Settings | None = None) -> tuple[Settings, list[str]]:
    """Parse the settings form for one network."""

    previous = previous or default_settings()
    if network not in NETWORKS:
        return previous, ["network: select mainnet, testnet4 or signet"]

    errors: list[str] = []
    overrides: dict[str, Any] = {}
    for field_def in FIELDS:
        checkbox = f"default__{field_def.key}"
        if _first(form, checkbox) in ("1", "on", "true", "yes"):
            continue
        raw = _first(form, f"value__{field_def.key}")
        if raw is None:
            continue
        try:
            coerced = _coerce(field_def, raw)
        except SettingsError as exc:
            errors.append(str(exc))
            continue
        if coerced is not None:
            overrides[field_def.key] = coerced

    try:
        public_hostnames = normalize_public_hostnames(_first(form, "public_hostnames") or "")
    except SettingsError as exc:
        public_hostnames = list(previous.network(network).public_hostnames)
        errors.append(str(exc))

    networks = {name: NetworkConfig(
        enabled=previous.network(name).enabled,
        overrides=dict(previous.network(name).overrides),
        public_hostnames=list(previous.network(name).public_hostnames),
    ) for name in NETWORKS}
    networks[network] = NetworkConfig(
        enabled=previous.network(network).enabled,
        overrides=overrides,
        public_hostnames=public_hostnames,
    )
    settings = Settings(networks=networks, setup_complete=True)
    errors.extend(validate_network(network, networks[network]))
    return settings, list(dict.fromkeys(errors))


def parse_networks_form(
    form: dict[str, list[str]],
    previous: Settings | None = None,
    *,
    require_one: bool = False,
) -> tuple[Settings, list[str]]:
    """Parse the enable/disable checkboxes for every network."""

    previous = previous or default_settings()
    errors: list[str] = []
    networks: dict[str, NetworkConfig] = {}
    for name in NETWORKS:
        old = previous.network(name)
        enabled = _first(form, f"enabled_{name}") in ("1", "on", "true", "yes")
        if (
            name == "mainnet"
            and enabled
            and not old.enabled
            and _first(form, "mainnet_ack") not in ("1", "on", "true", "yes")
        ):
            errors.append("mainnet_ack: confirm that you understand the mainnet sync")
        networks[name] = NetworkConfig(
            enabled=enabled,
            overrides=dict(old.overrides),
            public_hostnames=list(old.public_hostnames),
        )
    if require_one and not any(config.enabled for config in networks.values()):
        errors.append("networks: enable at least one network")
    return Settings(networks=networks, setup_complete=not errors if require_one else True), errors


def parse_setup_form(form: dict[str, list[str]], previous: Settings | None = None) -> tuple[Settings, list[str]]:
    return parse_networks_form(form, previous, require_one=True)


def _first(form: dict[str, list[str]], key: str) -> str | None:
    values = form.get(key)
    if not values:
        return None
    return str(values[0]).strip()


def format_value(field_def: Field, value: Any) -> str:
    if field_def.kind == "bool":
        return "true" if bool(value) else "false"
    if field_def.kind == "list":
        return "\n".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def default_label(field_def: Field) -> str:
    if field_def.kind == "bool":
        return "true" if field_def.default else "false"
    if field_def.kind == "list":
        return ""
    if field_def.kind == "optional_int":
        return "auto"
    return str(field_def.default)
