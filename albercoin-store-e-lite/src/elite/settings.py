"""User settings for the e-lite Umbrel app.

Only values explicitly overridden by the user are stored.  A field that is not
present in ``overrides`` is omitted from the generated e-lite TOML file, so
e-lite (and not this wrapper) owns the default value.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

NETWORKS = ("mainnet", "testnet4", "signet")

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
    Field("index.commit_sync", "enum", "auto", "performance", options=("auto", "always", "txids"), advanced=True),
    Field("index.batch_blocks", "int", 256, "performance", minimum=0, maximum=4096),
    Field("index.reorg_window", "int", 288, "performance", minimum=1, maximum=100000),
    Field("server.max_clients", "int", 32, "server", minimum=1, maximum=1024),
    Field("rawtx.enabled", "bool", True, "server"),
    Field("rawtx.cache_mib", "int", 2048, "server", minimum=0, maximum=262144),
    Field("rawtx.decoys", "int", 3, "server", minimum=0, maximum=32),
    Field("rawtx.use_p2p", "bool", True, "server"),
    Field("rawtx.use_esplora", "bool", True, "server"),
    Field("rawtx.allow_clearnet", "bool", False, "server", advanced=True),
    Field("rawtx.p2p_peers", "list", (), "server", advanced=True),
    Field("core.prune_mib", "int", 1, "pruning", minimum=0, maximum=1000000),
    Field("pruning.allow_auto_prune", "bool", False, "pruning", advanced=True),
    Field("pruning.min_free_gib", "int", 10, "pruning", minimum=0, maximum=100000),
    Field("pruning.critical_free_gib", "int", 1, "pruning", minimum=0, maximum=100000),
    Field("pruning.max_bulk_lag_blocks", "int", 20000, "pruning", minimum=0, maximum=1000000),
    Field("pruning.pause_core_during_bulk", "bool", True, "pruning"),
    Field("pruning.core_resume_ahead_blocks", "int", 5000, "pruning", minimum=0, maximum=1000000),
    Field("pruning.prune_defer_free_gib", "int", 50, "pruning", minimum=0, maximum=100000),
    Field("pruning.min_gain_blocks", "int", 10000, "pruning", minimum=0, maximum=1000000),
    Field("pruning.poll_seconds", "int", 10, "pruning", minimum=1, maximum=3600),
    Field("pruning.repair_max_gap_blocks", "int", 2048, "pruning", minimum=0, maximum=100000),
    Field("core.timeout_seconds", "int", 30, "core", minimum=1, maximum=600),
    Field("core.extra_args", "list", (), "core", advanced=True),
    Field("core.restart", "bool", True, "core", advanced=True),
    Field("logging.max_mib", "int", 64, "core", minimum=0, maximum=1024, advanced=True),
)

FIELDS_BY_KEY = {f.key: f for f in FIELDS}

_IPV4_PORT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{1,252}:[0-9]{1,5}$")
_IPV6_PORT = re.compile(r"^\[[0-9A-Fa-f:]+\]:[0-9]{1,5}$")


class SettingsError(ValueError):
    """Raised when a settings document or submitted form is invalid."""


@dataclass
class Settings:
    network: str | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    setup_complete: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "network": self.network,
            "setup_complete": self.setup_complete,
            "overrides": dict(sorted(self.overrides.items())),
        }

    def value(self, key: str) -> Any:
        field_def = FIELDS_BY_KEY[key]
        return self.overrides.get(key, field_def.default)

    def is_default(self, key: str) -> bool:
        return key not in self.overrides


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

    network = data.get("network")
    if network is not None and network not in NETWORKS:
        network = None
    setup_complete = bool(data.get("setup_complete")) and network in NETWORKS
    overrides_raw = data.get("overrides", {})
    if not isinstance(overrides_raw, dict):
        raise SettingsError("overrides must be an object")

    overrides: dict[str, Any] = {}
    for key, value in overrides_raw.items():
        field_def = FIELDS_BY_KEY.get(key)
        if field_def is None:
            continue
        try:
            overrides[key] = _coerce(field_def, value)
        except SettingsError:
            # A stale or hand-edited invalid value must not brick the app.
            continue
    return Settings(network=network, overrides=overrides, setup_complete=setup_complete)


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
    return int(number) if field_def.kind == "int" else float(number)


def validate_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    if settings.network not in NETWORKS:
        errors.append("network: must be mainnet, testnet4 or signet")
    for key, value in settings.overrides.items():
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

    prune = settings.overrides.get("core.prune_mib")
    if prune is not None and 1 < int(prune) < 550:
        errors.append("core.prune_mib: use 0 (full), 1 (manual) or >= 550 (automatic)")
    if prune is not None and int(prune) >= 550 and not bool(settings.overrides.get("pruning.allow_auto_prune", False)):
        errors.append("pruning.allow_auto_prune: must be enabled when core.prune_mib >= 550")

    min_free = settings.overrides.get("pruning.min_free_gib")
    critical = settings.overrides.get("pruning.critical_free_gib")
    if min_free is not None and critical is not None and int(critical) > int(min_free):
        errors.append("pruning.critical_free_gib: cannot be greater than pruning.min_free_gib")

    return errors


def parse_form(form: dict[str, list[str]], previous: Settings | None = None) -> tuple[Settings, list[str]]:
    """Parse the settings form.

    ``form`` is the output of ``urllib.parse.parse_qs``.  Each field has a
    ``default__<key>`` checkbox and a ``value__<key>`` control.
    """

    previous = previous or default_settings()
    errors: list[str] = []
    network = _first(form, "network") or previous.network
    if network not in NETWORKS:
        errors.append("network: select mainnet, testnet4 or signet")

    overrides: dict[str, Any] = {}
    for field_def in FIELDS:
        checkbox = f"default__{field_def.key}"
        if _first(form, checkbox) in ("1", "on", "true", "yes"):
            continue
        raw = _first(form, f"value__{field_def.key}")
        if raw is None:
            # A missing control means "use the default"; this is friendlier for
            # partially submitted or older forms.
            continue
        try:
            overrides[field_def.key] = _coerce(field_def, raw)
        except SettingsError as exc:
            errors.append(str(exc))

    settings = Settings(network=network, overrides=overrides, setup_complete=True)
    errors.extend(validate_settings(settings))
    # Preserve the order and remove duplicates.
    return settings, list(dict.fromkeys(errors))


def parse_setup_form(form: dict[str, list[str]], previous: Settings | None = None) -> tuple[Settings, list[str]]:
    previous = previous or default_settings()
    network = _first(form, "network")
    errors: list[str] = []
    if network not in NETWORKS:
        errors.append("network: select mainnet, testnet4 or signet")
    if network == "mainnet" and _first(form, "mainnet_ack") not in ("1", "on", "true", "yes"):
        errors.append("mainnet_ack: confirm that you understand the mainnet sync")
    settings = Settings(network=network, overrides=dict(previous.overrides), setup_complete=not errors)
    return settings, errors


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
    return str(value)


def default_label(field_def: Field) -> str:
    if field_def.kind == "bool":
        return "true" if field_def.default else "false"
    if field_def.kind == "list":
        return ""
    return str(field_def.default)


def overrides_for_network(settings: Settings) -> Iterable[tuple[str, Any]]:
    return settings.overrides.items()
