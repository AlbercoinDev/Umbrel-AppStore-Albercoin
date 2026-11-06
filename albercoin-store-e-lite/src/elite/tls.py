"""Per-network TLS certificates for the Electrum TLS listeners."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

from .config_writer import AppPaths

_ONION = re.compile(r"^[a-z2-7]{56}\.onion$")
_CA_LOCK = threading.Lock()


def read_onion(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""
    return value if _ONION.match(value) else ""


def build_sans(
    domain: str,
    hostname: str,
    onion: str,
    public_hostnames: list[str] | tuple[str, ...] = (),
) -> list[str]:
    dns = {"localhost", "umbrel.local"}
    if domain:
        dns.add(domain)
    if hostname:
        dns.add(hostname)
    if onion:
        dns.add(onion)
    for host in public_hostnames:
        if host:
            dns.add(host.strip().lower().rstrip("."))
    entries = [f"DNS:{item}" for item in sorted(dns)]
    entries.extend(["IP:127.0.0.1", "IP:::1"])
    return entries


class TLSManager:
    def __init__(
        self,
        paths: AppPaths,
        network: str,
        domain: str,
        hostname: str,
        public_hostnames_loader: Callable[[], list[str]] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.paths = paths
        self.network = network
        self.domain = domain
        self.hostname = hostname
        self.public_hostnames_loader = public_hostnames_loader or (lambda: [])
        self.log = log or (lambda _message: None)
        self._lock = threading.Lock()
        self._last_error = ""

    def status(self) -> dict[str, object]:
        marker = self.paths.network_sans_marker(self.network)
        try:
            sans = marker.read_text(encoding="utf-8").splitlines() if marker.is_file() else []
        except OSError:
            sans = []
        onion = read_onion(self.paths.network_onion_file(self.network))
        try:
            public_hostnames = list(self.public_hostnames_loader())
        except Exception:
            public_hostnames = []
        return {
            "ca_available": self.paths.ca_certificate.is_file(),
            "certificate_available": self.paths.network_certificate_chain(self.network).is_file(),
            "error": self._last_error,
            "sans": sans,
            "public_hostnames": public_hostnames,
            "onion": onion,
        }

    def ensure(self) -> bool:
        """Create or refresh this network's certificate."""

        with self._lock:
            try:
                self.paths.network_tls_dir(self.network).mkdir(parents=True, exist_ok=True)
                onion = read_onion(self.paths.network_onion_file(self.network))
                public_hostnames = self.public_hostnames_loader()
                sans = build_sans(self.domain, self.hostname, onion, public_hostnames)
                marker = self.paths.network_sans_marker(self.network)
                current = marker.read_text(encoding="utf-8").splitlines() if marker.is_file() else []
                if (
                    self.paths.network_certificate_chain(self.network).is_file()
                    and self.paths.network_certificate_key(self.network).is_file()
                    and self.paths.ca_certificate.is_file()
                    and current == sans
                ):
                    self._last_error = ""
                    return True

                self._generate(sans)
                marker.write_text("\n".join(sans) + "\n", encoding="utf-8")
                self._last_error = ""
                self.log(f"TLS certificate generated/updated for {self.network}")
                return True
            except Exception as exc:  # noqa: BLE001 - reported in the UI
                self._last_error = str(exc)
                self.log(f"TLS error for {self.network}: {exc}")
                return False

    def _openssl(self, args: list[str], cwd: Path) -> None:
        executable = shutil.which("openssl")
        if not executable:
            raise RuntimeError("openssl is not installed")
        result = subprocess.run(
            [executable, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"openssl failed: {detail[:300]}")

    def _ensure_ca(self, work: Path) -> tuple[Path, Path]:
        ca_key = work / "ca.key"
        ca_crt = work / "ca.crt"
        with _CA_LOCK:
            if self.paths.ca_key.is_file() and self.paths.ca_certificate.is_file():
                shutil.copy2(self.paths.ca_key, ca_key)
                shutil.copy2(self.paths.ca_certificate, ca_crt)
                return ca_key, ca_crt
            self._openssl(
                [
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:3072",
                    "-nodes",
                    "-sha256",
                    "-days",
                    "3650",
                    "-keyout",
                    str(ca_key),
                    "-out",
                    str(ca_crt),
                    "-subj",
                    "/CN=e-lite Local CA/O=Albercoin",
                ],
                work,
            )
            self.paths.ca_key.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ca_key, self.paths.ca_key.with_name(self.paths.ca_key.name + ".new"))
            shutil.copy2(ca_crt, self.paths.ca_certificate.with_name(self.paths.ca_certificate.name + ".new"))
            os.chmod(self.paths.ca_key.with_name(self.paths.ca_key.name + ".new"), 0o600)
            os.chmod(self.paths.ca_certificate.with_name(self.paths.ca_certificate.name + ".new"), 0o644)
            os.replace(self.paths.ca_key.with_name(self.paths.ca_key.name + ".new"), self.paths.ca_key)
            os.replace(
                self.paths.ca_certificate.with_name(self.paths.ca_certificate.name + ".new"),
                self.paths.ca_certificate,
            )
            return ca_key, ca_crt

    def _generate(self, sans: list[str]) -> None:
        tls_dir = self.paths.network_tls_dir(self.network)
        with tempfile.TemporaryDirectory(dir=tls_dir) as temp:
            work = Path(temp)
            ca_key, ca_crt = self._ensure_ca(work)
            server_key = work / "server.key"
            csr = work / "server.csr"
            server_crt = work / "server.crt"
            ext = work / "server.ext"
            fullchain = work / "fullchain.pem"

            self._openssl(
                [
                    "req",
                    "-new",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-sha256",
                    "-keyout",
                    str(server_key),
                    "-out",
                    str(csr),
                    "-subj",
                    f"/CN={self.domain or 'umbrel.local'}/O=Albercoin e-lite",
                ],
                work,
            )
            ext.write_text(
                "basicConstraints=CA:FALSE\n"
                "keyUsage=digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\n"
                "subjectAltName=" + ",".join(sans) + "\n",
                encoding="utf-8",
            )
            self._openssl(
                [
                    "x509",
                    "-req",
                    "-in",
                    str(csr),
                    "-CA",
                    str(ca_crt),
                    "-CAkey",
                    str(ca_key),
                    "-CAcreateserial",
                    "-out",
                    str(server_crt),
                    "-days",
                    "3650",
                    "-sha256",
                    "-extfile",
                    str(ext),
                ],
                work,
            )
            fullchain.write_bytes(server_crt.read_bytes() + ca_crt.read_bytes())

            def install(source: Path, destination: Path, mode: int) -> None:
                temporary = destination.with_name(destination.name + ".new")
                shutil.copy2(source, temporary)
                os.chmod(temporary, mode)
                os.replace(temporary, destination)

            install(server_crt, self.paths.network_certificates(self.network), 0o644)
            install(server_key, self.paths.network_certificate_key(self.network), 0o600)
            install(fullchain, self.paths.network_certificate_chain(self.network), 0o644)
