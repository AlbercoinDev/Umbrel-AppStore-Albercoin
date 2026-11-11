"""Download, verify and install the pinned e-lite and Bitcoin Core binaries."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

E_LITE_VERSION = "0.1.0-alpha.29.9.2"
E_LITE_SHA256 = "b2570e5bf062ee6e56ad6f8aa46a5afb7646c57db1112893a5535b8b7dd78680"
E_LITE_URL = (
    "https://github.com/AlbercoinDev/e-lite/releases/download/"
    f"v{E_LITE_VERSION}/e-lite_x86_64-unknown-linux-gnu.tar.gz"
)

BITCOIN_VERSION = "32.1"
BITCOIN_SHA256 = "61545c17c3120fb7412f896d248b11993af04ec8817aad3649c55b2afb044963"
BITCOIN_URL = f"https://bitcoincore.org/bin/bitcoin-core-{BITCOIN_VERSION}/bitcoin-{BITCOIN_VERSION}-x86_64-linux-gnu.tar.gz"

TOR_VERSION = "16.0"
TOR_SHA256 = "cc950ebe9acb80188325c3a2cd8817def7dd7237d6fdcd861bb87b234a0cb2fd"
TOR_URL = (
    "https://dist.torproject.org/torbrowser/"
    f"{TOR_VERSION}/tor-expert-bundle-linux-x86_64-{TOR_VERSION}.tar.gz"
)

DOWNLOAD_CHUNK = 1024 * 1024
MIN_FREE_BYTES = 300 * 1024 * 1024


class RuntimeError_(RuntimeError):
    pass


def supported_architecture(machine: str | None = None) -> bool:
    machine = (machine or platform.machine()).lower()
    return machine in ("x86_64", "amd64")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(DOWNLOAD_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: Path, destination: Path) -> None:
    """Extract a tarball while rejecting paths outside ``destination``."""

    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise RuntimeError_(f"unsafe archive member: {member.name}")
            target = (destination / name).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError_(f"unsafe archive member: {member.name}")
        tar.extractall(destination, filter="data")


def _download(url: str, destination: Path, expected_sha256: str, progress: Callable[[int, int], None]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    downloaded = 0
    total = 0
    request = urllib.request.Request(url, headers={"User-Agent": "albercoin-e-lite-umbrel/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        length = response.headers.get("Content-Length")
        total = int(length) if length and length.isdigit() else 0
        with part.open("wb") as handle:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                progress(downloaded, total)
    actual = digest.hexdigest()
    if actual != expected_sha256:
        part.unlink(missing_ok=True)
        raise RuntimeError_(f"checksum mismatch: expected {expected_sha256}, got {actual}")
    os.replace(part, destination)


class RuntimeManager:
    """Background installer for the two pinned binaries."""

    def __init__(self, runtime_dir: Path, log: Callable[[str], None] | None = None) -> None:
        self.runtime_dir = runtime_dir
        self.log = log or (lambda _message: None)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state = "pending"
        self._message = "Preparando componentes"
        self._downloaded = 0
        self._total = 0
        self._error = ""
        self._kind = ""

    # -------------------------------------------------------------- properties
    @property
    def e_lite_dir(self) -> Path:
        return self.runtime_dir / f"e-lite-{E_LITE_VERSION}"

    @property
    def e_lite_binary(self) -> Path:
        return self.e_lite_dir / "e-lite"

    @property
    def public_servers_template(self) -> Path:
        return self.e_lite_dir / "public-servers.toml"

    @property
    def bitcoin_dir(self) -> Path:
        return self.runtime_dir / f"bitcoin-{BITCOIN_VERSION}"

    @property
    def bitcoin_binary(self) -> Path:
        return self.bitcoin_dir / "bin" / "bitcoind"

    @property
    def tor_dir(self) -> Path:
        return self.runtime_dir / f"tor-{TOR_VERSION}"

    @property
    def tor_binary(self) -> Path:
        return self.tor_dir / "tor"

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "phase": self._state,
                "message": self._message,
                "downloaded": self._downloaded,
                "total": self._total,
                "kind": self._kind,
                "error": self._error,
                "e_lite_version": E_LITE_VERSION,
                "bitcoin_version": BITCOIN_VERSION,
                "tor_version": TOR_VERSION,
            }

    def ready(self) -> bool:
        return (
            self.e_lite_binary.is_file()
            and os.access(self.e_lite_binary, os.X_OK)
            and self.bitcoin_binary.is_file()
            and self.tor_binary.is_file()
        )

    # ------------------------------------------------------------------ actions
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="runtime-installer", daemon=True)
            self._thread.start()

    def retry(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._error = ""
            self._state = "pending"
            self._message = "Reintentando la preparación"
        self.start()

    def wait(self, timeout: float) -> bool:
        thread = self._thread
        if thread is None:
            return self.ready()
        thread.join(timeout)
        return not thread.is_alive()

    def _set(self, **values: object) -> None:
        with self._lock:
            for key, value in values.items():
                if key == "state":
                    self._state = str(value)
                elif key == "message":
                    self._message = str(value)
                elif key == "downloaded":
                    self._downloaded = int(value)
                elif key == "total":
                    self._total = int(value)
                elif key == "error":
                    self._error = str(value)
                elif key == "kind":
                    self._kind = str(value)

    def _run(self) -> None:
        try:
            if not supported_architecture():
                raise RuntimeError_(
                    "e-lite solo publica binarios x86_64; esta instalación es "
                    f"{platform.machine()}. Usa un nodo Umbrel amd64."
                )
            if self.ready():
                self._verify_binaries()
                self._cleanup_old()
                self._set(state="ready", message="Componentes listos", error="", kind="")
                return
            free = shutil.disk_usage(self.runtime_dir).free
            if free < MIN_FREE_BYTES:
                raise RuntimeError_(
                    f"espacio insuficiente: hay {free // (1024 * 1024)} MiB libres y se "
                    f"necesitan al menos {MIN_FREE_BYTES // (1024 * 1024)} MiB"
                )
            self._install_e_lite()
            self._install_bitcoin()
            self._install_tor()
            self._verify_binaries()
            self._cleanup_old()
            self._set(state="ready", message="Componentes listos", error="", kind="")
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.log(f"runtime error: {exc}")
            self._set(state="error", message="No se pudieron preparar los componentes", error=str(exc), kind="")
        finally:
            self._set(kind="")

    def _install_e_lite(self) -> None:
        target_dir = self.e_lite_dir
        binary_ready = (target_dir / "e-lite").is_file() and os.access(target_dir / "e-lite", os.X_OK)
        template_ready = (target_dir / "public-servers.toml").is_file()
        if binary_ready and template_ready:
            return
        if not binary_ready:
            self._set(state="downloading", message="Descargando e-lite", kind="e-lite", downloaded=0, total=0)
            with tempfile.TemporaryDirectory(dir=self.runtime_dir) as temp:
                archive = Path(temp) / "e-lite.tar.gz"
                _download(
                    E_LITE_URL,
                    archive,
                    E_LITE_SHA256,
                    lambda done, total: self._set(downloaded=done, total=total, state="downloading", message="Descargando e-lite"),
                )
                self._set(state="installing", message="Instalando e-lite")
                extract_dir = Path(temp) / "extract"
                safe_extract(archive, extract_dir)
                binary = extract_dir / "e-lite"
                if not binary.is_file():
                    raise RuntimeError_("el archivo de e-lite no contiene el binario esperado")
                target_dir.mkdir(parents=True, exist_ok=True)
                temporary = target_dir / "e-lite.new"
                shutil.copy2(binary, temporary)
                os.chmod(temporary, 0o755)
                temporary.replace(target_dir / "e-lite")
                template = extract_dir / "public-servers.toml"
                if template.is_file():
                    shutil.copy2(template, target_dir / "public-servers.toml")
                example = extract_dir / "config.example.toml"
                if example.is_file():
                    shutil.copy2(example, target_dir / "config.example.toml")
        elif not template_ready:
            raise RuntimeError_("el runtime de e-lite no contiene public-servers.toml; reinstala la app")

    def _install_bitcoin(self) -> None:
        target_dir = self.bitcoin_dir
        if (target_dir / "bin" / "bitcoind").is_file() and os.access(target_dir / "bin" / "bitcoind", os.X_OK):
            return
        self._set(state="downloading", message="Descargando Bitcoin Core", kind="bitcoin", downloaded=0, total=0)
        with tempfile.TemporaryDirectory(dir=self.runtime_dir) as temp:
            archive = Path(temp) / "bitcoin.tar.gz"
            _download(
                BITCOIN_URL,
                archive,
                BITCOIN_SHA256,
                lambda done, total: self._set(downloaded=done, total=total, state="downloading", message="Descargando Bitcoin Core"),
            )
            self._set(state="installing", message="Instalando Bitcoin Core")
            extract_dir = Path(temp) / "extract"
            safe_extract(archive, extract_dir)
            extracted = extract_dir / f"bitcoin-{BITCOIN_VERSION}"
            if not (extracted / "bin" / "bitcoind").is_file():
                raise RuntimeError_("el archivo de Bitcoin Core no contiene bitcoind")
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(extracted, target_dir)

    def _install_tor(self) -> None:
        target_dir = self.tor_dir
        if (target_dir / "tor").is_file() and os.access(target_dir / "tor", os.X_OK):
            return
        self._set(state="downloading", message="Descargando Tor", kind="tor", downloaded=0, total=0)
        with tempfile.TemporaryDirectory(dir=self.runtime_dir) as temp:
            archive = Path(temp) / "tor.tar.gz"
            _download(
                TOR_URL,
                archive,
                TOR_SHA256,
                lambda done, total: self._set(downloaded=done, total=total, state="downloading", message="Descargando Tor"),
            )
            self._set(state="installing", message="Instalando Tor")
            extract_dir = Path(temp) / "extract"
            safe_extract(archive, extract_dir)
            extracted = extract_dir / "tor"
            if not (extracted / "tor").is_file():
                raise RuntimeError_("el archivo de Tor no contiene el binario esperado")
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(extracted, target_dir)
            os.chmod(target_dir / "tor", 0o755)

    def _verify_binaries(self) -> None:
        verify_home = self.runtime_dir / "verify-home"
        verify_home.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(verify_home)
        tor_env = env.copy()
        tor_env["LD_LIBRARY_PATH"] = str(self.tor_dir) + (
            ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
        )
        checks = (
            ([str(self.e_lite_binary), "--version"], E_LITE_VERSION, env),
            ([str(self.bitcoin_binary), "-version"], f"v{BITCOIN_VERSION}", env),
            ([str(self.tor_binary), "--version"], "Tor version", tor_env),
        )
        for command, expected, command_env in checks:
            try:
                output = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                    env=command_env,
                )
            except OSError as exc:
                raise RuntimeError_(f"no se pudo ejecutar {command[0]}: {exc}") from exc
            combined = (output.stdout or "") + (output.stderr or "")
            if output.returncode != 0 or expected not in combined:
                raise RuntimeError_(f"versión inesperada de {Path(command[0]).name}: {combined.strip()[:200]}")
        shutil.rmtree(verify_home, ignore_errors=True)

    def _cleanup_old(self) -> None:
        keep = {f"e-lite-{E_LITE_VERSION}", f"bitcoin-{BITCOIN_VERSION}", f"tor-{TOR_VERSION}"}
        if not self.runtime_dir.is_dir():
            return
        for child in self.runtime_dir.iterdir():
            if child.name in keep or child.name.endswith(".part"):
                continue
            if child.is_dir() and (
                child.name.startswith("e-lite-")
                or child.name.startswith("bitcoin-")
                or child.name.startswith("tor-")
            ):
                shutil.rmtree(child, ignore_errors=True)
