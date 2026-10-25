"""Per-relay routing and isolated official CLI probes; never log credentials."""
import asyncio
import contextlib
import hashlib
import re
import tempfile
import time
from pathlib import Path

from .config import DATA


def servers(config):
    result = []
    for protocol, key in (("smp", "smp_servers"), ("xftp", "xftp_servers")):
        for uri in config.get(key, "").split():
            match = re.fullmatch(rf"{protocol}://([A-Za-z0-9_=-]+(?::[^@\s/]+)?)@([^/\s]+)(?:/)?", uri)
            if not match:
                raise ValueError(f"Dirección {protocol.upper()} no válida; utiliza una URI completa.")
            identity, authority = match.groups()
            hosts, sep, port = authority.rpartition(":")
            if not sep:
                hosts, port = authority, ""
            elif not port.isdigit() or not 1 <= int(port) <= 65535:
                raise ValueError("Puerto no válido.")
            hosts = hosts.split(",")
            if not all(re.fullmatch(r"[A-Za-z0-9.-]+", h) and len(h) <= 253 for h in hosts):
                raise ValueError("Nombre de servidor no válido.")
            onions = [h.lower() for h in hosts if h.lower().endswith(".onion")]
            if any(not re.fullmatch(r"[a-z2-7]{56}\.onion", h) for h in onions):
                raise ValueError("Se requiere una dirección onion v3 válida.")
            host = onions[0] if onions else hosts[0]
            route = "tor" if onions else "clearnet"
            result.append({"id": hashlib.sha256(uri.encode()).hexdigest()[:16], "protocol": protocol,
                           "host": host, "route": route,
                           "available": "mixed" if onions and len(onions) < len(hosts) else route,
                           "uri": f"{protocol}://{identity}@{host}" + (":" + port if port else "")})
    if len(result) > 32:
        raise ValueError("Máximo 32 servidores personalizados.")
    return result


def network_args(config):
    targets = servers(config)
    args = []
    for protocol, flag in (("smp", "--server"), ("xftp", "--xftp-server")):
        values = [s["uri"] for s in targets if s["protocol"] == protocol]
        if values:
            args += [flag, " ".join(values)]
    if config.get("socks_proxy"):
        args += ["--socks-proxy", config["socks_proxy"], "--socks-mode", "always"]
    elif any(s["route"] == "tor" for s in targets):
        args += ["--socks-proxy", "127.0.0.1:9050", "--socks-mode", "onion"]
    return args


class Network:
    def __init__(self, config, diagnostics):
        self.config, self.diagnostics = config, diagnostics
        self.tor = self.task = None
        self.results = []
        self.last_started = 0
        self.tor_lock = asyncio.Lock()

    def log(self, level, phase, summary, **context):
        self.diagnostics.record(level, "network", phase, summary, **context)

    async def ensure_tor(self):
        async with self.tor_lock:
            await self._ensure_tor()

    async def _ensure_tor(self):
        if self.config.value.get("socks_proxy") or not any(s["route"] == "tor" for s in servers(self.config.value)):
            return
        if self.tor is None or self.tor.returncode is not None:
            directory = DATA / "manager/tor"
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.tor = await asyncio.create_subprocess_exec(
                "tor", "--DataDirectory", str(directory), "--SocksPort", "127.0.0.1:9050",
                "--ControlSocket", str(directory / "control"), "--CookieAuthentication", "1",
                "--CookieAuthFile", str(directory / "control.authcookie"), "--Log", "notice file /dev/null",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            self.log("info", "tor_start", "Tor local iniciado; esperando circuitos.")

    def start(self, force=False):
        if self.task and not self.task.done():
            return
        if not force and time.monotonic() - self.last_started < 60:
            raise ValueError("Espera 60 segundos entre comprobaciones.")
        self.last_started = time.monotonic()
        targets = servers(self.config.value)
        self.results = [{k: v for k, v in s.items() if k != "uri"} | {"status": "pending"} for s in targets]
        self.task = asyncio.create_task(self.check(targets, dict(self.config.value)))

    async def check(self, targets, config):
        run = str(time.time_ns())
        for target, result in zip(targets, self.results):
            started = time.monotonic()
            context = {"server_id": target["id"], "check_id": run, "protocol": target["protocol"],
                       "route": "proxy" if config.get("socks_proxy") else target["route"]}
            result["route"] = context["route"]
            result.update(status="checking", checked_at=time.time())
            self.log("info", "probe_start", "Comprobación de protocolo iniciada.", **context)
            child = None
            try:
                await self.ensure_tor()
                if target["route"] == "tor" and not config.get("socks_proxy"):
                    from .tor_control import query
                    result["phase"] = "tor_bootstrap"
                    async with asyncio.timeout(180):
                        while True:
                            try:
                                status = await asyncio.to_thread(query, DATA / "manager/tor")
                                result["tor_progress"] = status["progress"]
                                if status["progress"] == 100 and status["circuit"]:
                                    break
                            except OSError:
                                pass
                            await asyncio.sleep(2)
                    self.log("info", "tor_ready", "Tor con circuitos; aún no confirma acceso al servidor.", **context)
                result["phase"] = "protocol"
                with tempfile.TemporaryDirectory(prefix="simplex-probe-") as directory:
                    args = ["/usr/local/bin/simplex-chat", "-d", str(Path(directory) / "client"),
                            "--user-display-name", "NetworkProbe", "-y", "--smp-proxy", "never",
                            "--execute-log", "none", "-e", f'/{target["protocol"]} test {target["uri"]}', "-t", "1"]
                    args += network_args({**config, "smp_servers": target["uri"] if target["protocol"] == "smp" else "",
                                          "xftp_servers": target["uri"] if target["protocol"] == "xftp" else ""})
                    child = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                    try:
                        output, _ = await asyncio.wait_for(child.communicate(), 120)
                    finally:
                        if child.returncode is None:
                            child.kill()
                            await child.wait()
                    text = output.decode(errors="replace")
                    success = child.returncode == 0 and f'{target["protocol"].upper()} server test passed' in text
                    code = "ok" if success else next((c.lower() for c in ("AUTH", "CERTIFICATE", "IDENTITY", "HANDSHAKE", "TLS", "TRANSPORT", "SOCKS", "TIMEOUT", "NETWORK", "HOST") if re.search(rf"\b{c}\b", text, re.I)), "protocol_failed")
                    stage = re.search(r"failed at\s+([A-Za-z]+)", text, re.I)
                    hints = {"auth": "El servidor rechazó la autorización; revisa la contraseña de la URI.",
                             "network": "No se pudo conectar al destino; revisa red, puerto y disponibilidad.",
                             "socks": "Falló el proxy SOCKS; revisa Tor o el proxy externo.",
                             "timeout": "El servidor no completó la operación dentro del plazo.",
                             "host": "No hay un destino compatible o no se pudo resolver el servidor."}
                    hint = hints.get(code, "Falló el transporte seguro, la identidad o una operación del protocolo; consulta la etapa en los logs.")
                    result.update(status="ok" if success else "error", code=code,
                                  detail="Protocolo verificado; no acredita entrega de mensajes." if success else hint + " Podría quedar un recurso temporal sin eliminar.")
                    self.log("info" if success else "error", "probe_result", result["detail"], **context,
                             code=code, exit_status=child.returncode, test_step=stage.group(1) if stage else None)
            except asyncio.CancelledError:
                self.log("warning", "probe_cancelled", "Prueba interrumpida; puede quedar un recurso temporal sin eliminar.", **context)
                raise
            except Exception as exc:
                result.update(status="error", code="timeout" if isinstance(exc, TimeoutError) else "unavailable",
                              detail="No se pudo completar la comprobación; puede quedar un recurso temporal.")
                self.log("error", "probe_failed", result["detail"], **context, code=result["code"], phase_failed=result.get("phase"), error_type=type(exc).__name__)
            finally:
                if child and child.returncode is None:
                    child.kill()
                    await child.wait()
                result["duration"] = round(time.monotonic() - started, 2)
                self.log("info", "probe_finished", "Comprobación finalizada.", **context, duration=result["duration"])

    async def cancel(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def close(self):
        await self.cancel()
        if self.tor and self.tor.returncode is None:
            self.tor.terminate()
            try:
                await asyncio.wait_for(self.tor.wait(), 15)
            except TimeoutError:
                self.tor.kill()
                await self.tor.wait()
