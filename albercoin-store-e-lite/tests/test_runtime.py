from __future__ import annotations

import hashlib
import http.server
import io
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path

from elite import runtime
from elite.runtime import RuntimeManager, safe_extract, sha256_file, supported_architecture


class ArchiveHandler(http.server.BaseHTTPRequestHandler):
    payload = b""

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *_args: object) -> None:
        return


class RuntimeTests(unittest.TestCase):
    def test_supported_architecture(self) -> None:
        self.assertTrue(supported_architecture("x86_64"))
        self.assertTrue(supported_architecture("amd64"))
        self.assertFalse(supported_architecture("aarch64"))

    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file"
            path.write_bytes(b"hello")
            self.assertEqual(sha256_file(path), hashlib.sha256(b"hello").hexdigest())

    def test_safe_extract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "ok.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                data = b"hello"
                info = tarfile.TarInfo("e-lite")
                info.size = len(data)
                info.mode = 0o755
                tar.addfile(info, io.BytesIO(data))
            destination = root / "out"
            safe_extract(archive, destination)
            self.assertEqual((destination / "e-lite").read_bytes(), b"hello")

    def test_safe_extract_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo("../evil")
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(runtime.RuntimeError_):
                safe_extract(archive, root / "out")
            self.assertFalse((root / "evil").exists())

    def test_download_verifies_hash(self) -> None:
        payload = b"download-me"
        ArchiveHandler.payload = payload
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ArchiveHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "file.bin"
                runtime._download(  # noqa: SLF001
                    f"http://127.0.0.1:{server.server_address[1]}/file",
                    destination,
                    hashlib.sha256(payload).hexdigest(),
                    lambda _done, _total: None,
                )
                self.assertEqual(destination.read_bytes(), payload)
        finally:
            server.shutdown()
            server.server_close()

    def test_download_rejects_bad_hash(self) -> None:
        ArchiveHandler.payload = b"bad"
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ArchiveHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "file.bin"
                with self.assertRaises(runtime.RuntimeError_):
                    runtime._download(  # noqa: SLF001
                        f"http://127.0.0.1:{server.server_address[1]}/file",
                        destination,
                        "0" * 64,
                        lambda _done, _total: None,
                    )
                self.assertFalse(destination.exists())
        finally:
            server.shutdown()
            server.server_close()

    def test_ready_requires_all_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeManager(Path(directory))
            self.assertFalse(manager.ready())
            manager.e_lite_dir.mkdir(parents=True)
            manager.e_lite_binary.write_text("#!/bin/sh\n")
            manager.e_lite_binary.chmod(0o755)
            self.assertFalse(manager.ready())
            manager.bitcoin_dir.mkdir(parents=True)
            (manager.bitcoin_dir / "bin").mkdir()
            manager.bitcoin_binary.write_text("#!/bin/sh\n")
            manager.bitcoin_binary.chmod(0o755)
            self.assertFalse(manager.ready())
            manager.tor_dir.mkdir(parents=True)
            manager.tor_binary.write_text("#!/bin/sh\n")
            manager.tor_binary.chmod(0o755)
            self.assertTrue(manager.ready())


if __name__ == "__main__":
    unittest.main()
