"""Local TLS certificates for the Electrum TLS listener.

The app creates a private CA and a server certificate signed by it.  The CA
certificate can be downloaded from the web UI so advanced users can add it to a
wallet trust store.  The leaf certificate is regenerated when the SAN list
changes, for example when the Tor onion address appears.
"""

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


def read_onion(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""
    return value if _ONION.match(value) else ""


def build_sans(domain: str, hostname: str, onion: str, public_hostnames: list[str] | tuple[str, ...] = ()) -> list[str]:
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
        domain: str,
        hostname: str,
        log: Callable[[str], None] | None = None,
        public_hostnames_loader: Callable[[], list[str]] | None = None,
    ) -> None:
        self.paths = paths
        self.domain = domain
        self.hostname = hostname
        self.log = log or (lambda _message: None)
        self.public_hostnames_loader = public_hostnames_loader or (lambda: [])
        self._lock = threading.Lock()
        self._last_error = ""

    def status(self) -> dict[str, object]:
        marker = self.paths.tls_dir / "sans.txt"
        try:
            sans = marker.read_text(encoding="utf-8").splitlines() if marker.is_file() else []
        except OSError:
            sans = []
        return {
            "ca_available": self.paths.ca_certificate.is_file(),
            "certificate_available": self.paths.certificate_chain.is_file(),
            "error": self._last_error,
            "sans": sans,
            "public_hostnames": list(self.public_hostnames_loader()),
        }

    def ensure(self) -> bool:
        """Create or refresh the certificate.  Returns True when files exist."""

        with self._lock:
            try:
                self.paths.tls_dir.mkdir(parents=True, exist_ok=True)
                onion = read_onion(self.paths.onion_file)
                public_hostnames = self.public_hostnames_loader()
                sans = build_sans(self.domain, self.hostname, onion, public_hostnames)
                marker = self.paths.tls_dir / "sans.txt"
                current = marker.read_text(encoding="utf-8").splitlines() if marker.is_file() else []
                if (
                    self.paths.certificate_chain.is_file()
                    and self.paths.certificate_key.is_file()
                    and self.paths.ca_certificate.is_file()
                    and current == sans
                ):
                    self._last_error = ""
                    return True

                self._generate(sans)
                marker.write_text("\n".join(sans) + "\n", encoding="utf-8")
                self._last_error = ""
                self.log("TLS certificate generated/updated")
                return True
            except Exception as exc:  # noqa: BLE001 - reported in the UI
                self._last_error = str(exc)
                self.log(f"TLS error: {exc}")
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

    def _generate(self, sans: list[str]) -> None:
        with tempfile.TemporaryDirectory(dir=self.paths.tls_dir) as temp:
            work = Path(temp)
            ca_key = work / "ca.key"
            ca_crt = work / "ca.crt"
            server_key = work / "server.key"
            csr = work / "server.csr"
            server_crt = work / "server.crt"
            ext = work / "server.ext"
            fullchain = work / "fullchain.pem"

            # Keep the CA stable across leaf regenerations.
            if self.paths.ca_key.is_file() and self.paths.ca_certificate.is_file():
                shutil.copy2(self.paths.ca_key, ca_key)
                shutil.copy2(self.paths.ca_certificate, ca_crt)
            else:
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

            install(ca_crt, self.paths.ca_certificate, 0o644)
            install(ca_key, self.paths.ca_key, 0o600)
            install(server_crt, self.paths.certificates, 0o644)
            install(server_key, self.paths.certificate_key, 0o600)
            install(fullchain, self.paths.certificate_chain, 0o644)
