"""Export and import per-network snapshots (e-lite index + Bitcoin Core)."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

from .config_writer import AppPaths
from .settings import NETWORKS, Settings
from .supervisor import SupervisorManager

ALLOWED_EXTRA_ROOT = Path("/exports")
MANIFEST_NAME = "e-lite-snapshot.json"
BUNDLE_FORMAT = "umbrel-1"
EXCLUDE_INDEX = {
    "rawtx-cache", "tor", "tls", "LOG", "LOCK", "e-lite.log", "e-lite.log.1",
    "banner.txt", "core-paused-by-e-lite", "core-child.pid", "bitcoind.log", "bitcoin",
}
# e-lite packs only chain data in --with-core (AUD-126). The wrapper mirrors that
# allowlist: wallets, keys, bitcoin.conf and settings.json never enter a bundle.
CORE_CHAIN_DIRS = ("blocks", "chainstate", "indexes")
IMPORT_MARKER = ".import-in-progress.json"
IMPORT_STAGING_SUFFIX = ".import-partial"
DIGEST_RE = re.compile(r"^[0-9a-fA-F]{64}$")
PROGRESS_STALL_SECONDS = 20
PROGRESS_SAMPLE_SECONDS = 5
EXPORT_TIMEOUT_SECONDS = 6 * 3600
IMPORT_TIMEOUT_SECONDS = 4 * 3600
BASE_MARGIN_BYTES = 256 * 1024 * 1024
FACTS_MAX_AGE_SECONDS = 24 * 3600
FACTS_FILE = "core-facts.json"
DEFAULT_INDEX_RATIO = 0.55
_TIP_RE = re.compile(r"UpdateTip: new best=([0-9a-f]{64})\s+height=(\d+)")
_VERSION_RE = re.compile(r"Bitcoin Core version v([0-9][0-9A-Za-z.\-]*)")


class Cancelled(RuntimeError):
    """Raised when the operator cancels the running job."""


# --------------------------------------------------------------------------- helpers
def _du(path: Path, exclude: set[str] | None = None) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not exclude or d not in exclude]
        for name in files:
            if exclude and name in exclude:
                continue
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def core_chain_subdir(network: str) -> str:
    """Chain data lives at the datadir root on mainnet and in `<chain>/` elsewhere."""
    return "" if network == "mainnet" else network


def core_chain_paths(core_dir: Path, network: str) -> list[Path]:
    """Existing chain-data directories of a Core datadir, never wallets or config."""
    sub = core_chain_subdir(network)
    base = core_dir / sub if sub else core_dir
    return [base / name for name in CORE_CHAIN_DIRS if (base / name).is_dir()]


def core_packed_bytes(core_dir: Path, network: str) -> int:
    return sum(_du(path) for path in core_chain_paths(core_dir, network))


def core_tar_members(core_dir: Path, network: str) -> list[str]:
    return [f"./{path.relative_to(core_dir).as_posix()}" for path in core_chain_paths(core_dir, network)]


def core_blocks_path(core_dir: Path, network: str) -> Path:
    sub = core_chain_subdir(network)
    return core_dir / sub / "blocks" if sub else core_dir / "blocks"


def import_staging(paths: AppPaths, network: str) -> Path:
    """Sibling staging directory e-lite uses for an atomic import (AUD-141)."""
    target = paths.network_e_lite_data(network)
    return target.with_name(f".{target.name}{IMPORT_STAGING_SUFFIX}")


def import_marker(paths: AppPaths, network: str) -> Path:
    return paths.network_root(network) / IMPORT_MARKER


def valid_digest(value: str) -> str:
    """Normalise an expected index digest, or return an empty string."""
    text = (value or "").strip().lower()
    return text if DIGEST_RE.fullmatch(text) else ""


def index_complete(path: Path) -> bool:
    return (path / "CURRENT").is_file()


def core_complete(core_dir: Path, network: str) -> bool:
    return core_blocks_path(core_dir, network).is_dir()


def free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def same_device(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def space_margin(total: int) -> int:
    return max(BASE_MARGIN_BYTES, int(total * 0.10))


def export_tmp_candidates(output: Path) -> tuple[Path, ...]:
    """e-lite writes `<output>.tmp` via Rust `with_extension("tmp")`."""
    return output.with_suffix(".tmp"), output.with_name(output.name + ".tmp")


def cleanup_tmp_files(output: Path) -> None:
    for candidate in export_tmp_candidates(output):
        try:
            if candidate.exists():
                candidate.unlink()
        except OSError:
            pass


def read_manifest(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.getmember(MANIFEST_NAME)
        handle = tar.extractfile(member)
        if handle is None:
            raise ValueError("snapshot manifest is not readable")
        return json.loads(handle.read().decode("utf-8", errors="replace"))


def restored_estimate(manifest: dict, archive_bytes: int) -> int:
    """Bytes the restore of an upstream snapshot needs on the data disk."""
    core = manifest.get("core") or {}
    core_bytes = int(core.get("bytes") or 0)
    if core_bytes:
        return core_bytes + max(0, archive_bytes - core_bytes) * 2
    return archive_bytes * 2


def bundle_space_needed(bundle: dict) -> int:
    index_raw = int(bundle.get("index_raw_bytes") or 0)
    core_bytes = int(bundle.get("core_bytes") or 0)
    total = index_raw + core_bytes
    return total + space_margin(total)


def facts_path(paths: AppPaths, network: str) -> Path:
    return paths.network_config_dir(network) / FACTS_FILE


def load_facts(paths: AppPaths, network: str) -> dict:
    try:
        data = json.loads(facts_path(paths, network).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_facts(paths: AppPaths, network: str, facts: dict) -> None:
    if not facts.get("tip_height"):
        return
    payload = dict(facts)
    payload.setdefault("source", "live")
    payload["updated_at"] = int(time.time())
    try:
        facts_path(paths, network).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def clear_facts(paths: AppPaths, network: str) -> None:
    try:
        facts_path(paths, network).unlink()
    except OSError:
        pass


def facts_age(facts: dict) -> int:
    try:
        return max(0, int(time.time()) - int(facts.get("updated_at") or 0))
    except (TypeError, ValueError):
        return 0


def parse_export_output(lines: list[str]) -> dict:
    """Read the facts e-lite prints after a snapshot (no archive scan needed)."""
    result: dict = {}
    for line in lines:
        text = line.strip()
        if text.startswith("network:"):
            result["network"] = text.split(":", 1)[1].strip()
        elif text.startswith("tip:"):
            parts = text.split()
            if len(parts) >= 3:
                try:
                    result["tip_height"] = int(parts[1])
                except ValueError:
                    continue
                result["index_hash"] = parts[2]
        elif text.startswith("txnum:"):
            try:
                result["txnum"] = int(text.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif text.startswith("logical_snapshot_hash:"):
            result["digest"] = text.split(":", 1)[1].strip()
    return result


def parse_debug_log(path: Path) -> dict:
    """Best-effort tip/version from Core's debug.log (fallback when stopped)."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 4 * 1024 * 1024))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return {}
    tip = None
    for match in _TIP_RE.finditer(text):
        tip = match
    result: dict = {}
    if tip:
        result["tip_hash"] = tip.group(1)
        result["tip_height"] = int(tip.group(2))
    version = _VERSION_RE.search(text)
    if version:
        result["core_version"] = version.group(1)
    return result


def core_facts(paths: AppPaths, network: str, metrics_port: int) -> dict:
    """Live /status facts, else persisted facts, else debug.log."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{metrics_port}/status", timeout=3) as response:
            data = json.load(response)
        core = data.get("core") or {}
        facts = {
            "tip_height": int(data.get("core_height") or 0),
            "pruned": bool(core.get("pruned")),
            "prune_height": int(core.get("pruneheight") or 0),
            "source": "live",
        }
        parsed = parse_debug_log(paths.network_core_log(network))
        if parsed.get("core_version"):
            facts["core_version"] = parsed["core_version"]
        if facts["tip_height"]:
            save_facts(paths, network, facts)
            return facts
    except Exception:
        pass
    persisted = load_facts(paths, network)
    if persisted.get("tip_height"):
        persisted["source"] = "persisted"
        return persisted
    fallback_log = paths.network_core_data(network) / "debug.log"
    sub = core_chain_subdir(network)
    if sub:
        fallback_log = paths.network_core_data(network) / sub / "debug.log"
    parsed = parse_debug_log(paths.network_core_log(network)) or parse_debug_log(fallback_log)
    if parsed.get("tip_height"):
        parsed["source"] = "debug-log"
        return parsed
    return {}


def _sidecar_path(archive: Path) -> Path:
    name = archive.name
    suffix = ".tar.gz"
    if name.endswith(suffix):
        return archive.with_name(name[: -len(suffix)] + ".manifest.json")
    return archive.with_suffix(".manifest.json")


def write_sidecar(archive: Path, manifest: dict) -> None:
    try:
        _sidecar_path(archive).write_text(json.dumps(manifest), encoding="utf-8")
    except OSError:
        pass


def _latest_previous(root: Path, prefix: str) -> Path | None:
    if not root.is_dir():
        return None
    items = [entry for entry in root.iterdir() if entry.name.startswith(prefix)]
    return max(items, key=lambda entry: entry.name) if items else None


def network_leftovers(paths: AppPaths, network: str) -> list[Path]:
    """`.pre-import-*` directories left by a previous import, per network."""
    root = paths.network_root(network)
    if not root.is_dir():
        return []
    prefixes = (
        f"{paths.network_e_lite_data(network).name}.pre-import-",
        f"{paths.network_core_data(network).name}.pre-import-",
    )
    return sorted((entry for entry in root.iterdir() if entry.name.startswith(prefixes)), key=lambda p: p.name)


def _ratio_file(paths: AppPaths) -> Path:
    return paths.config / "backup-stats.json"


def load_ratios(paths: AppPaths) -> dict:
    try:
        data = json.loads(_ratio_file(paths).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_ratio(paths: AppPaths, network: str, index_ratio: float) -> None:
    try:
        data = load_ratios(paths)
        data[network] = round(min(1.0, max(0.05, index_ratio)), 3)
        _ratio_file(paths).write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def validate_bundle(bundle: dict, index_file: Path, core_file: Path) -> list[str]:
    """Return blocking errors for a bundle before restoring it."""
    if bundle.get("format") != BUNDLE_FORMAT:
        return ["manifiesto de copia no reconocido"]
    if not index_file.is_file():
        return ["falta el archivo de índice de la copia"]
    errors = []
    if int(bundle.get("index_bytes") or 0) and index_file.stat().st_size != int(bundle["index_bytes"]):
        errors.append("el tamaño del índice no coincide con el manifiesto")
    if core_file.is_file() or bundle.get("core_file"):
        if not core_file.is_file():
            errors.append("falta el archivo de Core de la copia")
        elif int(bundle.get("core_bytes") or 0) and core_file.stat().st_size != int(bundle["core_bytes"]):
            errors.append("el tamaño de Core no coincide con el manifiesto")
    index_height = int(bundle.get("index_height") or 0)
    core_tip = int(bundle.get("core_tip_height") or 0)
    if core_tip and index_height > core_tip:
        errors.append(f"el índice ({index_height}) está por delante de Core ({core_tip})")
    prune_height = int(bundle.get("core_prune_height") or 0)
    if prune_height and index_height - 288 < prune_height:
        errors.append("el margen de poda es menor de 288 bloques")
    return errors


def _snapshot_files(destination: Path, scan_limit: int = 1) -> list[dict]:
    items: list[dict] = []
    if not destination.is_dir():
        return items
    for entry in sorted(destination.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True):
        if entry.name.endswith(".index.tar.gz"):
            continue
        manifest: dict | None = None
        sidecar = _sidecar_path(entry)
        try:
            if sidecar.is_file():
                manifest = json.loads(sidecar.read_text(encoding="utf-8"))
            elif scan_limit > 0:
                manifest = read_manifest(entry)
                scan_limit -= 1
                write_sidecar(entry, manifest)
        except Exception:
            manifest = None
        core = (manifest or {}).get("core") or {}
        items.append(
            {
                "name": entry.name,
                "kind": "snapshot",
                "bytes": entry.stat().st_size,
                "network": (manifest or {}).get("network", ""),
                "height": (manifest or {}).get("height", 0),
                "hash": (manifest or {}).get("hash", ""),
                "format": (manifest or {}).get("format", 1),
                "has_core": bool(core),
                "core_tip": core.get("tip_height", 0),
                "created": entry.stat().st_mtime,
                "manifest_read": manifest is not None,
            }
        )
    return items


def _bundle_files(destination: Path) -> list[dict]:
    items: list[dict] = []
    if not destination.is_dir():
        return items
    for entry in sorted(destination.glob("*.umbrel.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("format") != BUNDLE_FORMAT:
            continue
        items.append(
            {
                "name": entry.name,
                "kind": "bundle",
                "bytes": int(data.get("index_bytes") or 0) + int(data.get("core_bytes") or 0),
                "network": data.get("network", ""),
                "height": int(data.get("index_height") or 0),
                "hash": data.get("index_hash", ""),
                "format": 2,
                "has_core": bool(data.get("core_file")),
                "core_tip": int(data.get("core_tip_height") or 0),
                "created": entry.stat().st_mtime,
                "manifest_read": True,
            }
        )
    return items


# --------------------------------------------------------------------------- manager
class BackupManager:
    """Run one export/import job at a time and publish its progress."""

    SIZES_TTL_SECONDS = 600

    def __init__(self, paths: AppPaths, supervisor: SupervisorManager, settings_loader, log=None) -> None:
        self.paths = paths
        self.supervisor = supervisor
        self.settings_loader = settings_loader
        self.log = log or (lambda _message: None)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._state: dict = self._idle()
        self._sizes: dict[str, dict] = {}
        self._listing: dict[str, tuple[tuple, list[dict]]] = {}
        self._pid: int | None = None
        self._du_cache: dict[str, tuple[float, int]] = {}
        self._last_bytes = 0
        self._last_bytes_at = 0.0
        self._recovery_notice: dict | None = None

    @staticmethod
    def _idle() -> dict:
        return {
            "active": False,
            "ok": None,
            "action": "",
            "network": "",
            "phase": "idle",
            "message": "",
            "phase_detail": "",
            "phase_done": 0,
            "phase_total": 0,
            "phase_percent": 0.0,
            "phase_eta_seconds": None,
            "done": 0,
            "total": 0,
            "space_needed": 0,
            "eta_seconds": None,
            "path": "",
            "error": "",
            "started_at": None,
            "finished_at": None,
            "log": [],
            "tail": [],
            "phases": [],
            "phase_started_at": None,
            "warnings": [],
            "no_core": False,
            "digest": "",
        }

    # ------------------------------------------------------------------ helpers
    def destinations(self) -> list[dict]:
        result = []
        for root in (ALLOWED_EXTRA_ROOT, self.paths.exports_dir):
            if not root.is_dir():
                continue
            try:
                probe = root / ".e-lite-write-test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                writable = True
            except OSError:
                writable = False
            result.append(
                {
                    "path": str(root),
                    "label": str(root),
                    "writable": writable,
                    "free": free_bytes(root),
                    "same_device": same_device(root, self.paths.data),
                }
            )
        return result

    def default_destination(self) -> Path:
        for item in self.destinations():
            if item["writable"] and item["path"] == str(ALLOWED_EXTRA_ROOT):
                return ALLOWED_EXTRA_ROOT
        return self.paths.exports_dir

    def _allowed(self, destination: str) -> Path:
        target = Path(destination).resolve()
        for root in (ALLOWED_EXTRA_ROOT, self.paths.exports_dir):
            try:
                root_resolved = root.resolve()
            except OSError:
                continue
            if target == root_resolved or root_resolved in target.parents:
                return target
        raise ValueError("destino no permitido")

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    # -------------------------------------------------------------------- sizes
    def sizes(self, network: str) -> dict:
        with self._lock:
            entry = dict(self._sizes.get(network) or {})
        stale = not entry or (time.time() - entry.get("computed_at", 0) > self.SIZES_TTL_SECONDS)
        if stale and not entry.get("computing"):
            with self._lock:
                entry = self._sizes.setdefault(network, {**entry, "computing": True})
            threading.Thread(target=self._compute_sizes, args=(network,), name=f"backup-sizes-{network}", daemon=True).start()
        return {"index": entry.get("index", 0), "core": entry.get("core", 0), "computing": bool(entry.get("computing")), "ready": bool(entry.get("ready"))}

    def _compute_sizes(self, network: str) -> None:
        try:
            index = _du(self.paths.network_e_lite_data(network), EXCLUDE_INDEX)
            core = core_packed_bytes(self.paths.network_core_data(network), network)
        except OSError:
            index = core = 0
        with self._lock:
            self._sizes[network] = {"index": index, "core": core, "computed_at": time.time(), "computing": False, "ready": True}

    def cached_sizes(self, network: str) -> dict | None:
        with self._lock:
            entry = dict(self._sizes.get(network) or {})
        if entry.get("ready") and (time.time() - entry.get("computed_at", 0) <= self.SIZES_TTL_SECONDS):
            return entry
        return None

    def _invalidate_sizes(self, network: str) -> None:
        with self._lock:
            self._sizes.pop(network, None)

    def files(self, destination: str) -> list[dict]:
        try:
            root = self._allowed(destination)
        except (ValueError, OSError):
            return []
        key_parts = []
        for entry in sorted(root.glob("*")):
            try:
                stat = entry.stat()
                key_parts.append((entry.name, stat.st_size, int(stat.st_mtime)))
            except OSError:
                continue
        key = (str(root), tuple(key_parts))
        with self._lock:
            cached = self._listing.get(str(key))
        if cached and cached[0] == key:
            return cached[1]
        items = _snapshot_files(root) + _bundle_files(root)
        items.sort(key=lambda item: item.get("created") or 0, reverse=True)
        with self._lock:
            self._listing[str(key)] = (key, items)
        return items

    def leftovers(self, network: str) -> list[dict]:
        return [{"name": entry.name, "path": str(entry)} for entry in network_leftovers(self.paths, network)]

    def cleanup_previous(self, network: str) -> int:
        if self._active():
            return 0
        removed = 0
        for entry in network_leftovers(self.paths, network):
            try:
                shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def cleanup_stale(self) -> None:
        self.recover_interrupted()
        for network in NETWORKS:
            shutil.rmtree(import_staging(self.paths, network), ignore_errors=True)
        for destination in (ALLOWED_EXTRA_ROOT, self.paths.exports_dir):
            if not destination.is_dir():
                continue
            for entry in destination.glob("*.tmp"):
                try:
                    if time.time() - entry.stat().st_mtime > 300:
                        entry.unlink()
                except OSError:
                    pass

    def recovery_notice(self) -> dict | None:
        return dict(self._recovery_notice) if self._recovery_notice else None

    def recover_interrupted(self) -> int:
        """Undo the leftovers of an import interrupted by a crash or power loss.

        e-lite extracts into a staging directory and only renames it into place
        at the end (AUD-141), so a crash leaves the previous data in
        `.pre-import-*` and no target. Without this the next start would open an
        empty index instead of the previous one.
        """
        recovered = 0
        for network in NETWORKS:
            marker = import_marker(self.paths, network)
            if not marker.is_file():
                continue
            try:
                recovered += self._recover_network(network, marker)
            except OSError as exc:
                # Never block start-up: keep the marker so the next boot retries.
                self.log(f"backup: could not recover interrupted import on {network}: {exc}")
        return recovered

    def _recover_network(self, network: str, marker: Path) -> int:
        recovered = 0
        target = self.paths.network_e_lite_data(network)
        core_target = self.paths.network_core_data(network)
        shutil.rmtree(import_staging(self.paths, network), ignore_errors=True)
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        has_core = bool(payload.get("has_core", True))
        target_ok = index_complete(target)
        core_ok = not has_core or core_complete(core_target, network)
        if not (target_ok and core_ok):
            # Half a restore is worse than none: put both previous halves back
            # (or drop the partial data of a first install).
            root = self.paths.network_root(network)
            previous_index = _latest_previous(root, f"{target.name}.pre-import-")
            previous_core = _latest_previous(root, f"{core_target.name}.pre-import-")
            if previous_index is not None:
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                os.replace(previous_index, target)
                recovered += 1
            elif target.exists():
                shutil.rmtree(target, ignore_errors=True)
            if has_core:
                if previous_core is not None:
                    if core_target.exists():
                        shutil.rmtree(core_target, ignore_errors=True)
                    os.replace(previous_core, core_target)
                    recovered += 1
                elif core_target.exists():
                    shutil.rmtree(core_target, ignore_errors=True)
        try:
            marker.unlink()
        except OSError:
            pass
        clear_facts(self.paths, network)
        self._invalidate_sizes(network)
        self._recovery_notice = {"network": network, "recovered": recovered > 0}
        self.log(f"backup: recovered interrupted import on {network}")
        return recovered

    def _update(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def _phase(self, phase: str, message: str, detail: str = "") -> None:
        now = time.time()
        with self._lock:
            started = self._state.get("phase_started_at")
            if started:
                self._state["phases"] = list(self._state.get("phases") or []) + [
                    {"phase": self._state.get("phase"), "seconds": round(now - started, 1)}
                ]
            self._state.update(
                phase=phase, message=message, phase_detail=detail, phase_started_at=now,
                phase_done=0, phase_total=0, phase_percent=0.0, phase_eta_seconds=None,
            )
        self.log(f"backup: {phase}: {message}")

    def _active(self) -> bool:
        with self._lock:
            return bool(self._state.get("active"))

    # ----------------------------------------------------------------- progress
    def _written_bytes(self) -> int | None:
        """Bytes written by the running child (`wchar`), O(1) instead of walking the tree."""
        with self._lock:
            pid = self._pid
        if not pid:
            return None
        try:
            for line in Path(f"/proc/{pid}/io").read_text(encoding="utf-8").splitlines():
                if line.startswith("wchar:"):
                    return int(line.split(":", 1)[1].strip())
        except (OSError, ValueError):
            return None
        return None

    def _sampled_du(self, path: Path) -> int:
        key = str(path)
        now = time.monotonic()
        with self._lock:
            cached = self._du_cache.get(key)
        if cached and now - cached[0] < PROGRESS_SAMPLE_SECONDS:
            return cached[1]
        value = _du(path) if path.exists() else 0
        with self._lock:
            self._du_cache[key] = (now, value)
        return value

    def _import_bytes(self, path: Path) -> int:
        written = self._written_bytes()
        return written if written is not None else self._sampled_du(path)

    # -------------------------------------------------------------------- start
    def start_export(self, network: str, destination: str, compress: str = "3") -> tuple[bool, str]:
        return self._start("export", network, destination, {"compress": compress})

    def start_import(self, network: str, destination: str, filename: str, verify: str = "basic", delete_previous: bool = False, expect_digest: str = "") -> tuple[bool, str]:
        digest = (expect_digest or "").strip()
        if digest and not valid_digest(digest):
            return False, "el digest esperado debe ser de 64 caracteres hexadecimales"
        return self._start(
            "import", network, destination,
            {"filename": filename, "verify": verify, "delete_previous": delete_previous, "expect_digest": valid_digest(digest)},
        )

    def cancel(self) -> bool:
        if not self._active():
            return False
        self._cancel.set()
        return True

    def _start(self, action: str, network: str, destination: str, extra: dict) -> tuple[bool, str]:
        if network not in NETWORKS:
            return False, "red desconocida"
        if self._active():
            return False, "ya hay una copia en curso"
        try:
            target = self._allowed(destination)
            target.mkdir(parents=True, exist_ok=True)
        except (ValueError, OSError) as exc:
            return False, f"destino no válido: {exc}"
        self._cancel.clear()
        with self._lock:
            self._state = self._idle()
            self._state.update({"active": True, "action": action, "network": network, "started_at": time.time(), "phase": "preparing", "message": "Preparando", "phase_started_at": time.time()})
        args = {"network": network, "destination": target, **extra}
        self._thread = threading.Thread(target=self._run, args=(action, args), name=f"backup-{action}", daemon=True)
        self._thread.start()
        return True, ""

    # --------------------------------------------------------------------- run
    def _run(self, action: str, args: dict) -> None:
        network = args["network"]
        enabled = False
        try:
            settings = self.settings_loader()
            enabled = settings.network(network).enabled
        except Exception:
            enabled = False
        try:
            if action == "export":
                self._run_export(args, enabled)
            else:
                self._run_import(args, enabled)
        except Cancelled:
            self._update(active=False, ok=False, phase="cancelled", message="Cancelado", error="", finished_at=time.time())
            self.log("backup: cancelled")
            if enabled:
                self.supervisor.resume(network)
        except Exception as exc:  # noqa: BLE001
            self._update(active=False, ok=False, phase="error", error=str(exc), message="Error", finished_at=time.time())
            self.log(f"backup: error: {exc}")
            if enabled:
                self.supervisor.resume(network)
        finally:
            with self._lock:
                self._state["active"] = False
                if self._state.get("finished_at") is None:
                    self._state["finished_at"] = time.time()

    def _facts(self, network: str) -> dict:
        from .config_writer import network_metrics_port

        return core_facts(self.paths, network, network_metrics_port(network))

    def _stop(self, network: str) -> None:
        self._phase("stopping", "Parando la red (Core puede tardar hasta 2 min en vaciar su caché)")
        self.supervisor.suspend(network)
        node = self.supervisor.nodes[network]
        for _ in range(10):
            if not node.status().get("running"):
                return
            if self._cancel.is_set():
                raise Cancelled()
            time.sleep(1)
        raise RuntimeError("la red no se detuvo a tiempo; cancelado antes de tocar los datos")

    def _restart(self, network: str, enabled: bool) -> None:
        if enabled:
            self._phase("starting", "Reactivando la red")
            self.supervisor.resume(network)

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    # ------------------------------------------------------------------- export
    def _run_export(self, args: dict, enabled: bool) -> None:
        network = args["network"]
        destination: Path = args["destination"]
        config_file = self.paths.network_config_file(network)
        if not config_file.is_file():
            raise RuntimeError("la red aún no tiene configuración; iníciala primero")
        index_dir = self.paths.network_e_lite_data(network)
        core_dir = self.paths.network_core_data(network)
        cached = self.cached_sizes(network)
        if cached:
            index_raw = int(cached.get("index") or 0)
            core_raw = int(cached.get("core") or 0)
        else:
            self._phase("measuring", "Calculando tamaños (puede tardar)…")
            index_raw = _du(index_dir, EXCLUDE_INDEX)
            core_raw = core_packed_bytes(core_dir, network)
        if index_raw == 0:
            raise RuntimeError("el índice está vacío; no hay nada que exportar")
        facts = self._facts(network)
        ratios = load_ratios(self.paths)
        index_ratio = float(ratios.get(network) or DEFAULT_INDEX_RATIO)
        index_est = int(index_raw * index_ratio)
        total = core_raw + index_est
        needed = total + space_margin(total)
        warnings: list[str] = []
        if not facts.get("tip_height"):
            warnings.append("no se pudo leer la punta de Core; la copia no se comprobará contra ella")
        elif facts_age(facts) > FACTS_MAX_AGE_SECONDS:
            warnings.append("los datos de Core tienen más de 24 h; podrían estar desactualizados")
        if same_device(destination, self.paths.data):
            warnings.append("el destino está en el mismo disco que los datos: no sirve como backup")
        members = core_tar_members(core_dir, network)
        if not members:
            raise RuntimeError("Core no tiene datos de cadena (blocks/); no hay nada que copiar")
        warnings.append("solo se copian los datos de cadena de Core; las wallets y bitcoin.conf se guardan aparte")
        free = free_bytes(destination)
        if free < needed:
            raise RuntimeError(
                f"espacio insuficiente en el destino: hay {free // (1024**3)} GiB libres y se necesitan unos {needed // (1024**3)} GiB"
            )
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        base = destination / f"e-lite-{network}-{stamp}"
        index_file = base.with_name(base.name + ".index.tar.gz")
        core_file = base.with_name(base.name + ".core.tar")
        bundle_file = base.with_name(base.name + ".umbrel.json")
        self._update(total=total, space_needed=needed, path=str(bundle_file), warnings=warnings)
        self._stop(network)
        try:
            self._phase("packing-core", "Empaquetando Core…")
            tar_command = ["tar", "-C", str(core_dir), "-cf", str(core_file), *members]
            self._run_process(tar_command, lambda: self._progress_export(core_file, index_file, core_raw, index_est), EXPORT_TIMEOUT_SECONDS)
            self._check_cancel()
            self._phase("preparing-index", "Preparando índice (apertura + digest)…")
            compress = str(args.get("compress") or "3")
            if compress not in ("0", "3", "6"):
                compress = "3"
            command = [
                str(self.paths.runtime / f"e-lite-{_elite_version()}" / "e-lite"),
                "export-snapshot", "--config", str(config_file),
                "--hash-files", "--compress-level", compress, "--output", str(index_file),
            ]
            lines = self._run_process(command, lambda: self._progress_export(core_file, index_file, core_raw, index_est), EXPORT_TIMEOUT_SECONDS)
            self._check_cancel()
            self._phase("finalizing", "Validando la copia…")
            parsed = parse_export_output(lines)
            if not parsed.get("tip_height"):
                fallback = read_manifest(index_file)
                parsed = {
                    "network": fallback.get("network") or "",
                    "tip_height": int(fallback.get("height") or 0),
                    "index_hash": fallback.get("hash", ""),
                    "digest": fallback.get("digest", ""),
                }
            if parsed.get("network") not in ("", network):
                raise RuntimeError(f"la copia generada pertenece a {parsed.get('network')}")
            height = int(parsed.get("tip_height") or 0)
            tip = int(facts.get("tip_height") or 0)
            prune_height = int(facts.get("prune_height") or 0)
            if tip and height > tip:
                raise RuntimeError(f"el índice ({height}) está por delante de Core ({tip}); no se puede exportar")
            if prune_height and height - 288 < prune_height:
                warnings.append("el margen de poda es menor de 288 bloques; la importación puede necesitar resincronizar")
            index_bytes = index_file.stat().st_size
            core_bytes = core_file.stat().st_size
            bundle = {
                "format": BUNDLE_FORMAT,
                "network": network,
                "created": int(time.time()),
                "app_version": _app_version(),
                "e_lite_version": _elite_version(),
                "index_file": index_file.name,
                "core_file": core_file.name,
                "index_bytes": index_bytes,
                "index_raw_bytes": index_raw,
                "index_ratio": round(index_bytes / max(1, index_raw), 3),
                "core_bytes": core_bytes,
                "index_height": height,
                "index_hash": parsed.get("index_hash", ""),
                "index_digest": parsed.get("digest", ""),
                "core_tip_height": tip,
                "core_tip_hash": facts.get("tip_hash", ""),
                "core_pruned": bool(facts.get("pruned")),
                "core_prune_height": prune_height,
                "core_version": facts.get("core_version", ""),
                "facts_source": facts.get("source", ""),
                "facts_updated_at": int(facts.get("updated_at") or 0),
            }
            bundle_file.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
            save_ratio(self.paths, network, bundle["index_ratio"])
            with self._lock:
                self._listing.clear()
            size = index_bytes + core_bytes
            self._update(ok=True, phase="done", message=f"Copia completada · altura {height}", phase_detail="", done=size, total=size, phase_done=size, phase_total=size, phase_percent=100.0, phase_eta_seconds=0, eta_seconds=0, path=str(bundle_file), warnings=warnings, digest=bundle["index_digest"], finished_at=time.time())
            self._append_job_log(f"export {bundle_file.name} (height {height}, {size} bytes, digest {bundle['index_digest'] or 'n/d'})")
            self._restart(network, enabled)
        except BaseException:
            cleanup_tmp_files(index_file)
            for partial in (core_file, bundle_file, _sidecar_path(index_file)):
                try:
                    if partial.exists():
                        partial.unlink()
                except OSError:
                    pass
            raise

    def _progress_export(self, core_file: Path, index_file: Path, core_raw: int, index_est: int) -> None:
        core_done = 0
        try:
            if core_file.exists():
                core_done = core_file.stat().st_size
        except OSError:
            core_done = 0
        index_done = 0
        for candidate in export_tmp_candidates(index_file):
            try:
                if candidate.exists():
                    index_done = candidate.stat().st_size
                    break
            except OSError:
                continue
        if not index_done:
            try:
                if index_file.exists():
                    index_done = index_file.stat().st_size
            except OSError:
                index_done = 0
        total = core_raw + index_est
        with self._lock:
            phase = self._state.get("phase")
        if phase == "preparing-index" and index_done > 0:
            self._phase("exporting-index", "Empaquetando índice…")
            phase = "exporting-index"
        if phase == "preparing-index":
            self._update(phase_detail="Abriendo índice y calculando digest…")
            self._set_progress(core_done, total, 0, 0)
            return
        if phase == "packing-core":
            phase_done, phase_total = core_done, core_raw
            detail = f"Core {_fmt(core_done)} / {_fmt(core_raw)}"
        else:
            phase_done, phase_total = index_done, index_est
            detail = f"Índice {_fmt(index_done)} / {_fmt(index_est)}"
        self._update(phase_detail=detail)
        self._set_progress(core_done + index_done, total, phase_done, phase_total)

    # ------------------------------------------------------------------- import
    def _run_import(self, args: dict, enabled: bool) -> None:
        network = args["network"]
        destination: Path = args["destination"]
        archive = destination / os.path.basename(args["filename"])
        if not archive.is_file():
            raise RuntimeError("el archivo seleccionado no existe")
        if archive.name.endswith(".umbrel.json"):
            return self._import_bundle(args, enabled, archive)
        return self._import_snapshot(args, enabled, archive)

    def _prepare_targets(self, network: str) -> tuple[Path, Path, Path, Path]:
        target = self.paths.network_e_lite_data(network)
        core_target = self.paths.network_core_data(network)
        # Unique even if two imports land in the same second.
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + f"-{time.time_ns() % 1_000_000:06d}"
        previous_index = target.with_name(f"{target.name}.pre-import-{stamp}")
        previous_core = core_target.with_name(f"{core_target.name}.pre-import-{stamp}")
        return target, core_target, previous_index, previous_core

    def _write_marker(self, network: str, has_core: bool) -> None:
        marker = import_marker(self.paths, network)
        try:
            marker.write_text(
                json.dumps({"network": network, "started": int(time.time()), "has_core": bool(has_core)}),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _clear_marker(self, network: str) -> None:
        try:
            import_marker(self.paths, network).unlink()
        except OSError:
            pass

    def _rollback(self, target: Path, core_target: Path, previous_index: Path, previous_core: Path, core_touched: bool) -> bool:
        """Put the previous data back. Returns False when it could not be restored."""
        ok = True
        if previous_index.exists():
            try:
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                os.replace(previous_index, target)
            except OSError:
                ok = False
        elif target.exists():
            # First install: there is nothing to restore, drop the partial data.
            shutil.rmtree(target, ignore_errors=True)
        if core_touched:
            if previous_core.exists():
                try:
                    if core_target.exists():
                        shutil.rmtree(core_target, ignore_errors=True)
                    os.replace(previous_core, core_target)
                except OSError:
                    ok = False
            elif core_target.exists():
                shutil.rmtree(core_target, ignore_errors=True)
        return ok

    def _import_command(self, network: str, archive: Path, expect_digest: str = "") -> list[str]:
        command = [
            str(self.paths.runtime / f"e-lite-{_elite_version()}" / "e-lite"),
            "import-snapshot", "--config", str(self.paths.network_config_file(network)), "--input", str(archive),
        ]
        digest = valid_digest(expect_digest)
        if digest:
            command += ["--expect-digest", digest]
        return command

    def _import_bundle(self, args: dict, enabled: bool, bundle_file: Path) -> None:
        network = args["network"]
        destination = bundle_file.parent
        bundle = json.loads(bundle_file.read_text(encoding="utf-8"))
        index_file = destination / str(bundle.get("index_file") or "")
        core_file = destination / str(bundle.get("core_file") or "")
        errors = validate_bundle(bundle, index_file, core_file)
        if errors:
            raise RuntimeError("; ".join(errors))
        has_core = core_file.is_file()
        index_raw = int(bundle.get("index_raw_bytes") or 0) or int(bundle.get("index_bytes") or 0) * 2
        core_bytes = int(bundle.get("core_bytes") or 0)
        total = index_raw + core_bytes
        needed = bundle_space_needed(bundle)
        free = free_bytes(self.paths.data)
        if free < needed:
            raise RuntimeError(
                f"espacio insuficiente en el disco de datos: hay {free // (1024**3)} GiB libres y se necesitan unos {needed // (1024**3)} GiB"
            )
        warnings: list[str] = []
        if not has_core:
            warnings.append("esta copia no incluye Core; el índice no debe superar la punta actual de Core")
        if valid_digest(str(bundle.get("index_digest") or "")):
            warnings.append("se comprobará el digest del índice guardado en la copia")
        self._update(total=total, space_needed=needed, path=str(bundle_file), warnings=warnings, no_core=not has_core)
        self._stop(network)
        target, core_target, previous_index, previous_core = self._prepare_targets(network)
        self._write_marker(network, has_core)
        if target.exists():
            os.replace(target, previous_index)
        if has_core and core_target.exists():
            os.replace(core_target, previous_core)
        try:
            if has_core:
                self._phase("extracting-core", "Restaurando Core…")
                core_target.mkdir(parents=True, exist_ok=True)
                command = ["tar", "-C", str(core_target), "-xf", str(core_file)]
                self._run_process(command, lambda: self._progress_import_core(core_target, core_bytes, total), IMPORT_TIMEOUT_SECONDS)
                self._check_cancel()
            self._phase("extracting-index", "Restaurando el índice…")
            command = self._import_command(network, index_file, str(bundle.get("index_digest") or ""))
            self._run_process(command, lambda: self._progress_import_index(target, core_bytes, total, network), IMPORT_TIMEOUT_SECONDS)
            self._check_cancel()
            self._phase("finalizing", "Validando datos restaurados…")
            if has_core and not core_complete(core_target, network):
                raise RuntimeError("el Core restaurado no contiene blocks/")
        except BaseException:
            shutil.rmtree(import_staging(self.paths, network), ignore_errors=True)
            if self._rollback(target, core_target, previous_index, previous_core, has_core):
                self._clear_marker(network)
            raise
        self._clear_marker(network)
        clear_facts(self.paths, network)
        self._invalidate_sizes(network)
        with self._lock:
            self._listing.clear()
        if args.get("delete_previous"):
            shutil.rmtree(previous_index, ignore_errors=True)
            shutil.rmtree(previous_core, ignore_errors=True)
        self._update(ok=True, phase="done", message=f"Restaurado a altura {bundle.get('index_height', 0)}", phase_detail="", done=total, total=total, phase_done=total, phase_total=total, phase_percent=100.0, phase_eta_seconds=0, eta_seconds=0, digest=str(bundle.get("index_digest") or ""), finished_at=time.time())
        self._append_job_log(f"import {bundle_file.name} (height {bundle.get('index_height')}, digest {bundle.get('index_digest') or 'n/d'})")
        self._restart(network, enabled)

    def _import_snapshot(self, args: dict, enabled: bool, archive: Path) -> None:
        network = args["network"]
        manifest = read_manifest(archive)
        if manifest.get("network") != network:
            raise RuntimeError(f"la copia pertenece a {manifest.get('network')}, no a {network}")
        core_section = manifest.get("core") or {}
        total = restored_estimate(manifest, archive.stat().st_size)
        needed = total + space_margin(total)
        free = free_bytes(self.paths.data)
        if free < needed:
            raise RuntimeError(
                f"espacio insuficiente en el disco de datos: hay {free // (1024**3)} GiB libres y se necesitan unos {needed // (1024**3)} GiB"
            )
        warnings: list[str] = []
        if not core_section:
            warnings.append("esta copia no incluye Core; el índice no debe superar la punta actual de Core")
        expected = valid_digest(str(args.get("expect_digest") or ""))
        if expected:
            warnings.append("se comprobará el digest esperado indicado")
        self._update(total=total, space_needed=needed, path=str(archive), warnings=warnings, no_core=not core_section)
        self._stop(network)
        target, core_target, previous_index, previous_core = self._prepare_targets(network)
        self._write_marker(network, bool(core_section))
        if target.exists():
            os.replace(target, previous_index)
        if core_section and core_target.exists():
            os.replace(core_target, previous_core)
        command = self._import_command(network, archive, expected)
        if args.get("verify") == "basic":
            command += ["--verify", "basic"]
        elif args.get("verify") == "full":
            command += ["--verify", "full"]
        try:
            self._phase("extracting-index", "Restaurando el snapshot…")
            self._run_process(command, lambda: self._progress_import_index(target, 0, total, network), IMPORT_TIMEOUT_SECONDS)
            self._check_cancel()
            restored_core = target / "bitcoin"
            if core_section:
                if not restored_core.is_dir():
                    raise RuntimeError("el snapshot no restauró el datadir de Core")
                if core_target.exists():
                    shutil.rmtree(core_target, ignore_errors=True)
                os.replace(restored_core, core_target)
        except BaseException:
            shutil.rmtree(import_staging(self.paths, network), ignore_errors=True)
            if self._rollback(target, core_target, previous_index, previous_core, bool(core_section)):
                self._clear_marker(network)
            raise
        self._clear_marker(network)
        clear_facts(self.paths, network)
        self._invalidate_sizes(network)
        with self._lock:
            self._listing.clear()
        if args.get("delete_previous"):
            shutil.rmtree(previous_index, ignore_errors=True)
            shutil.rmtree(previous_core, ignore_errors=True)
        self._update(ok=True, phase="done", message=f"Restaurado a altura {manifest.get('height', 0)}", phase_detail="", done=total, total=total, phase_done=total, phase_total=total, phase_percent=100.0, phase_eta_seconds=0, eta_seconds=0, digest=expected, finished_at=time.time())
        self._append_job_log(f"import {archive.name} (height {manifest.get('height')})")
        self._restart(network, enabled)

    def _progress_import_core(self, core_target: Path, core_bytes: int, total: int) -> None:
        done = self._import_bytes(core_target)
        self._update(phase_detail=f"Core {_fmt(done)} / {_fmt(core_bytes)}")
        self._set_progress(done, total, done, core_bytes)

    def _progress_import_index(self, target: Path, core_bytes: int, total: int, network: str) -> None:
        staging = import_staging(self.paths, network)
        done = self._import_bytes(staging if staging.exists() else target)
        index_total = max(0, total - core_bytes)
        now = time.monotonic()
        if done > self._last_bytes:
            self._last_bytes = done
            self._last_bytes_at = now
        with self._lock:
            phase = self._state.get("phase")
        # Ignore the first bytes of start-up logging: only a writer that has
        # already produced real data can be "stalled" by the digest pass.
        stalled = done > 1024 * 1024 and bool(self._last_bytes_at) and (now - self._last_bytes_at) >= PROGRESS_STALL_SECONDS
        if phase == "extracting-index" and done > 0 and ((index_total and done >= index_total * 0.999) or stalled):
            self._phase("verifying-index", "Verificando el índice restaurado…")
            self._set_progress(core_bytes + done, total, 0, 0)
            return
        if phase == "verifying-index":
            self._update(phase_detail="Comprobando digest del índice restaurado…")
            self._set_progress(core_bytes + done, total, 0, 0)
            return
        if index_total:
            self._update(phase_detail=f"Índice {_fmt(done)} / {_fmt(index_total)}")
            self._set_progress(core_bytes + done, total, done, index_total)
        else:
            self._update(phase_detail=f"Índice restaurado: {_fmt(done)}")
            self._set_progress(core_bytes + done, max(total, core_bytes + done), 0, 0)

    # ------------------------------------------------------------------ process
    def _run_process(self, command: list[str], progress, timeout: int) -> list[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.paths.data)
        env["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env, start_new_session=True
        )
        lines: list[str] = []
        reader = threading.Thread(target=lambda: self._drain(process, lines), daemon=True)
        reader.start()
        deadline = time.time() + timeout
        with self._lock:
            self._pid = process.pid
            self._last_bytes = 0
            self._last_bytes_at = time.monotonic()
            self._du_cache.clear()
        try:
            while process.poll() is None:
                if self._cancel.is_set():
                    raise Cancelled()
                progress()
                if time.time() > deadline:
                    raise RuntimeError(f"el proceso superó el límite de {timeout // 3600} h; cancelado")
                time.sleep(1)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                process.terminate()
            try:
                process.wait(30)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
            raise
        finally:
            with self._lock:
                self._pid = None
        reader.join(timeout=5)
        self._update(log=lines[-40:])
        if process.returncode != 0:
            raise RuntimeError(" | ".join(lines[-3:]) or f"el comando terminó con código {process.returncode}")
        return lines

    def _drain(self, process: subprocess.Popen[str], lines: list[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            text = line.rstrip("\n")
            lines.append(text)
            del lines[:-200]
            with self._lock:
                self._state["tail"] = lines[-5:]
            lowered = text.lower()
            if "verify=full" in lowered or "starting core on the restored datadir" in lowered:
                self._update(message="Verificando con Core (verifychain)…")

    def _append_job_log(self, message: str) -> None:
        try:
            path = self.paths.exports_dir / "backup.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}Z {message}\n")
        except OSError:
            pass

    def _set_progress(self, done: int, total: int, phase_done: int = 0, phase_total: int = 0) -> None:
        now = time.time()
        with self._lock:
            started = self._state.get("started_at") or now
            elapsed = max(0.001, now - started)
            rate = done / elapsed
            total = max(total, done)
            eta = int(max(0, total - done) / rate) if rate > 0 and total > done else 0
            phase_started = self._state.get("phase_started_at") or now
            phase_elapsed = max(0.001, now - phase_started)
            phase_rate = phase_done / phase_elapsed
            phase_total = max(phase_total, phase_done)
            phase_eta = int(max(0, phase_total - phase_done) / phase_rate) if phase_rate > 0 and phase_total > phase_done else 0
            percent = min(100.0, phase_done / phase_total * 100.0) if phase_total else 0.0
            self._state.update(
                done=done, total=total, eta_seconds=eta,
                phase_done=phase_done, phase_total=phase_total,
                phase_percent=round(percent, 1), phase_eta_seconds=phase_eta,
            )


def _fmt(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TiB"


def _elite_version() -> str:
    from .runtime import E_LITE_VERSION

    return E_LITE_VERSION


def _app_version() -> str:
    from . import APP_VERSION

    return APP_VERSION
