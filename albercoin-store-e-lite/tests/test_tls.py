from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from elite.config_writer import AppPaths
from elite.tls import TLSManager, build_sans, read_onion


class TLSTests(unittest.TestCase):
    def test_build_sans(self) -> None:
        sans = build_sans("umbrel.local", "e-lite", "abc.onion", ["elite.example.org"])
        self.assertIn("DNS:umbrel.local", sans)
        self.assertIn("DNS:e-lite", sans)
        self.assertIn("DNS:localhost", sans)
        self.assertIn("DNS:abc.onion", sans)
        self.assertIn("DNS:elite.example.org", sans)
        self.assertIn("IP:127.0.0.1", sans)

    def test_read_onion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hostname"
            path.write_text("a" * 56 + ".onion\n")
            self.assertEqual(read_onion(path), "a" * 56 + ".onion")
            path.write_text("not-an-onion")
            self.assertEqual(read_onion(path), "")

    @unittest.skipUnless(shutil.which("openssl"), "openssl is required")
    def test_generate_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            manager = TLSManager(
                paths,
                "signet",
                "umbrel.local",
                "e-lite",
                public_hostnames_loader=lambda: ["elite.example.org"],
            )
            self.assertTrue(manager.ensure())
            self.assertTrue(paths.ca_certificate.is_file())
            self.assertTrue(paths.network_certificate_chain("signet").is_file())
            self.assertEqual(paths.network_certificate_key("signet").stat().st_mode & 0o777, 0o600)
            output = subprocess.run(
                ["openssl", "x509", "-in", str(paths.network_certificates("signet")), "-noout", "-ext", "subjectAltName"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            self.assertIn("DNS:umbrel.local", output)
            self.assertIn("DNS:e-lite", output)
            self.assertIn("DNS:elite.example.org", output)

    @unittest.skipUnless(shutil.which("openssl"), "openssl is required")
    def test_certificate_refreshes_when_onion_appears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = AppPaths.from_root(Path(directory))
            paths.ensure()
            manager = TLSManager(paths, "signet", "umbrel.local", "e-lite")
            self.assertTrue(manager.ensure())
            first = paths.network_certificates("signet").read_bytes()
            onion_file = paths.network_onion_file("signet")
            onion_file.parent.mkdir(parents=True, exist_ok=True)
            onion = "b" * 56 + ".onion"
            onion_file.write_text(onion)
            self.assertTrue(manager.ensure())
            second = paths.network_certificates("signet").read_bytes()
            self.assertNotEqual(first, second)
            output = subprocess.run(
                ["openssl", "x509", "-in", str(paths.network_certificates("signet")), "-noout", "-ext", "subjectAltName"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            self.assertIn(onion, output)


if __name__ == "__main__":
    unittest.main()
