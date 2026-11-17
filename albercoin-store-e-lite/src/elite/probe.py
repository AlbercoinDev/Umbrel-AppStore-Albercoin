"""Probe the public providers of one network through its managed Tor.

The panel uses it to show how fast every Electrum server and Esplora endpoint
answers: connection+TLS, `server.version`, a batch of transactions and one old
transaction (to know whether the provider still serves pruned history). The
test transactions are fetched from the network's Esplora tip and from block
100000, so the probe works on mainnet, testnet4 and signet; no wallet data
leaves the node.
"""

from __future__ import annotations

import json
import socket
import ssl
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

PROBE_TIMEOUT = 6.0
BATCH_SIZES = (20, 10, 5)


class ProbeError(RuntimeError):
    """A provider did not answer in time or with a usable answer."""


def electrum_endpoints(values: list[str]) -> list[tuple[str, int, bool]]:
    """`host:port:s` and `host:port:t` entries from public-servers.toml."""
    endpoints: list[tuple[str, int, bool]] = []
    for value in values:
        parts = str(value).strip().rsplit(":", 2)
        if len(parts) != 3 or parts[0].startswith("["):
            continue
        host, port, mode = parts
        try:
            number = int(port)
        except ValueError:
            continue
        if mode not in ("s", "t"):
            continue
        endpoints.append((host, number, mode == "s"))
    return endpoints


def esplora_endpoints(values: list[str]) -> list[tuple[str, str]]:
    """`(host, path)` for every https Esplora URL."""
    endpoints: list[tuple[str, str]] = []
    for value in values:
        parsed = urlsplit(str(value).strip())
        if parsed.scheme != "https" or not parsed.hostname:
            continue
        endpoints.append((parsed.hostname, parsed.path.rstrip("/")))
    return endpoints


def _socks_connect(socks_port: int, host: str, port: int, timeout: float) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", socks_port), timeout=timeout)
    sock.settimeout(timeout)
    sock.sendall(b"\x05\x01\x00")
    if sock.recv(2) != b"\x05\x00":
        sock.close()
        raise ProbeError("SOCKS5 authentication refused")
    name = host.encode()
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack("!H", port))
    reply = sock.recv(4)
    if len(reply) < 4 or reply[1] != 0:
        sock.close()
        raise ProbeError(f"SOCKS5 connect failed (code {reply[1] if len(reply) > 1 else '?'})")
    if reply[3] == 3:
        sock.recv(sock.recv(1)[0])
    elif reply[3] == 1:
        sock.recv(4)
    elif reply[3] == 4:
        sock.recv(16)
    sock.recv(2)
    return sock


def _tls_connect(socks_port: int, host: str, port: int, timeout: float) -> tuple[socket.socket, float]:
    started = time.perf_counter()
    raw = _socks_connect(socks_port, host, port, timeout)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        sock = context.wrap_socket(raw, server_hostname=host)
    except Exception:
        raw.close()
        raise
    return sock, (time.perf_counter() - started) * 1000


def _call(sock: socket.socket, payload: object) -> tuple[object, float]:
    started = time.perf_counter()
    sock.sendall((json.dumps(payload) + "\n").encode())
    buffer = b""
    while b"\n" not in buffer:
        chunk = sock.recv(65536)
        if not chunk:
            raise ProbeError("connection closed")
        buffer += chunk
    return json.loads(buffer.split(b"\n", 1)[0]), (time.perf_counter() - started) * 1000


def _http_get(socks_port: int, host: str, path: str, timeout: float) -> tuple[int, bytes]:
    sock, _ = _tls_connect(socks_port, host, 443, timeout)
    sock.settimeout(timeout)
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
        "User-Agent: e-lite-probe\r\nConnection: close\r\n\r\n"
    )
    sock.sendall(request.encode())
    data = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    sock.close()
    head, _, body = data.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1]) if head.startswith(b"HTTP/") else 0
    return status, body


def _probe_txids_from(socks_port: int, host: str, path: str, timeout: float) -> tuple[list[str], str]:
    status, body = _http_get(socks_port, host, f"{path}/blocks/tip/hash", timeout)
    if status != 200:
        return [], ""
    tip = body.decode().strip()
    status, body = _http_get(socks_port, host, f"{path}/block/{tip}/txids", timeout)
    if status != 200:
        return [], ""
    batch = [str(item) for item in json.loads(body) if len(str(item)) == 64][:20]
    status, body = _http_get(socks_port, host, f"{path}/block-height/100000", timeout)
    if status != 200:
        return [], ""
    old_hash = body.decode().strip()
    status, body = _http_get(socks_port, host, f"{path}/block/{old_hash}/txids", timeout)
    if status != 200:
        return [], ""
    old = [str(item) for item in json.loads(body) if len(str(item)) == 64]
    if not batch or not old:
        return [], ""
    return batch, old[1] if len(old) > 1 else old[0]


def probe_txids(socks_port: int, esplora: list[tuple[str, str]], timeout: float) -> tuple[list[str], str]:
    """Twenty tip transactions and one from block 100000. Every Esplora
    endpoint is asked in parallel; the first complete answer wins."""
    if not esplora:
        return [], ""
    with ThreadPoolExecutor(max_workers=len(esplora)) as pool:
        jobs = [pool.submit(_probe_txids_from, socks_port, host, path, timeout) for host, path in esplora]
        for job in as_completed(jobs):
            try:
                batch, old = job.result()
            except Exception:  # noqa: BLE001
                continue
            if batch and old:
                return batch, old
    return [], ""


def probe_electrum(
    socks_port: int,
    host: str,
    port: int,
    tls: bool,
    timeout: float,
    batch_txids: list[str],
    old_txid: str,
) -> dict:
    result: dict = {"kind": "electrum", "server": f"{host}:{port}", "ok": False, "tls": tls}
    started = time.perf_counter()
    try:
        if tls:
            sock, setup_ms = _tls_connect(socks_port, host, port, timeout)
        else:
            sock = _socks_connect(socks_port, host, port, timeout)
            setup_ms = 0.0
        result["connect_ms"] = round(setup_ms)
        sock.settimeout(timeout)
        try:
            version, version_ms = _call(
                sock, {"jsonrpc": "2.0", "id": 1, "method": "server.version", "params": ["e-lite-probe", "1.4"]}
            )
            result["version_ms"] = round(version_ms)
            result["version"] = str((version.get("result") or ["", ""])[0])[:40]
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"server.version: {exc}"
            sock.close()
            return result
        if old_txid:
            try:
                reply, tx_ms = _call(
                    sock,
                    {"jsonrpc": "2.0", "id": 2, "method": "blockchain.transaction.get", "params": [old_txid, False]},
                )
                item = reply[0] if isinstance(reply, list) else reply
                result["tx_ms"] = round(tx_ms)
                result["tx_ok"] = isinstance(item, dict) and not item.get("error") and bool(item.get("result"))
            except Exception as exc:  # noqa: BLE001
                result["tx_ok"] = False
                result["tx_error"] = str(exc)[:80]
        for size in (BATCH_SIZES if batch_txids else ()):
            size = min(size, len(batch_txids))
            if size <= 0:
                continue
            try:
                batch = [
                    {"jsonrpc": "2.0", "id": 100 + index, "method": "blockchain.transaction.get", "params": [txid, False]}
                    for index, txid in enumerate(batch_txids[:size])
                ]
                reply, batch_ms = _call(sock, batch)
                items = reply if isinstance(reply, list) else [reply]
                ok = sum(1 for item in items if isinstance(item, dict) and not item.get("error"))
                result["batch_size"] = size
                result["batch_ok"] = ok
                result["batch_ms"] = round(batch_ms)
                result["ok"] = ok == size
                break
            except Exception as exc:  # noqa: BLE001
                result["batch_error"] = str(exc)[:80]
                try:
                    sock.close()
                except OSError:
                    pass
                if size <= min(BATCH_SIZES):
                    break
                try:
                    if tls:
                        sock, _ = _tls_connect(socks_port, host, port, timeout)
                    else:
                        sock = _socks_connect(socks_port, host, port, timeout)
                    sock.settimeout(timeout)
                    _call(
                        sock,
                        {"jsonrpc": "2.0", "id": 300 + size, "method": "server.version", "params": ["e-lite-probe", "1.4"]},
                    )
                except Exception as retry:  # noqa: BLE001
                    result["batch_retry_error"] = str(retry)[:80]
                    break
        if "batch_size" not in result:
            # No batch was tested: a provider that answered the handshake
            # (and, when asked, the old transaction) is usable.
            result["ok"] = bool(result.get("version_ms") is not None and result.get("tx_ok", True))
        result["total_ms"] = round((time.perf_counter() - started) * 1000)
        try:
            sock.close()
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:120]
    return result


def probe_esplora(socks_port: int, host: str, path: str, timeout: float, old_txid: str) -> dict:
    result: dict = {"kind": "esplora", "server": f"https://{host}{path}", "ok": False}
    started = time.perf_counter()
    try:
        for name, request_path in (
            ("tip", "/blocks/tip/height"),
            ("old_tx", f"/tx/{old_txid}/hex" if old_txid else "/blocks/tip/hash"),
        ):
            began = time.perf_counter()
            status, body = _http_get(socks_port, host, f"{path}{request_path}", timeout)
            elapsed = (time.perf_counter() - began) * 1000
            result[f"{name}_ms"] = round(elapsed)
            result[f"{name}_bytes"] = len(body)
            result[f"{name}_ok"] = status == 200 and bool(body)
        result["ok"] = bool(result.get("tip_ok") and result.get("old_tx_ok"))
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:120]
    result["total_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def probe_network(
    electrum: list[tuple[str, int, bool]],
    esplora: list[tuple[str, str]],
    socks_port: int,
    timeout: float = PROBE_TIMEOUT,
) -> dict:
    """Probe every provider in parallel; a dead Tor is reported, not raised."""
    started = time.perf_counter()
    result: dict = {"socks_port": socks_port, "tor_ok": False, "providers": [], "elapsed_ms": 0}
    try:
        probe = socket.create_connection(("127.0.0.1", socks_port), timeout=2.0)
        probe.close()
        result["tor_ok"] = True
    except OSError as exc:
        result["tor_error"] = str(exc)[:80]
    if not result["tor_ok"]:
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return result
    batch_txids, old_txid = probe_txids(socks_port, esplora, timeout)
    result["batch_size"] = len(batch_txids)
    result["old_tx"] = bool(old_txid)
    jobs = []
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(electrum) + len(esplora)))) as pool:
        for host, port, tls in electrum:
            jobs.append(pool.submit(probe_electrum, socks_port, host, port, tls, timeout, batch_txids, old_txid))
        for host, path in esplora:
            jobs.append(pool.submit(probe_esplora, socks_port, host, path, timeout, old_txid))
        for job in jobs:
            try:
                result["providers"].append(job.result())
            except Exception as exc:  # noqa: BLE001
                result["providers"].append({"ok": False, "error": str(exc)[:120]})
    result["providers"].sort(key=lambda item: (item.get("kind", ""), item.get("server", "")))
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    return result
