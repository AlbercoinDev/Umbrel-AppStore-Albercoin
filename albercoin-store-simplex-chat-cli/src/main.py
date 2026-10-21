import asyncio
import base64
import contextlib
import io
import json
import re
import secrets
import signal
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType
import qrcode
import qrcode.image.svg

from .config import Config, DATA
from .runtime import Runtime
from .simplex_client import Broker
from .diagnostics import Diagnostics

STATIC = Path(__file__).parent / "static"
COOKIE = "simplex_session"
APP_VERSION = "1.0.3"
TARGET = re.compile(r"^[@#][1-9][0-9]*$")


def valid_link(link):
    if not isinstance(link, str) or len(link) > 16384 or re.search(r"\s", link):
        return False
    parsed = urlsplit(link)
    return parsed.scheme == "simplex" or (parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password)


class Manager:
    def __init__(self):
        self.config = Config()
        self.diagnostics = Diagnostics(DATA / "manager")
        self.broker = Broker(self.diagnostics)
        self.runtime = Runtime(self.config, self.broker)
        self.sessions = {}
        self.login_attempts = deque(maxlen=100)
        self.integration_sockets = set()
        self.connector_commands = 0
        self.connector_last_command = None
        self.tasks = []

    async def user(self):
        resp = await self.broker.response("/user")
        user = resp.get("user")
        if not isinstance(user, dict) or "userId" not in user:
            raise ValueError("El cliente todavía no tiene un perfil activo.")
        return user

    async def close_integrations(self):
        for ws in tuple(self.integration_sockets):
            await ws.close(code=1008, message=b"Connector revoked or reconfigured")

    async def startup(self, app):
        # Preserve the old database offline before a future binary version migration.
        marker = DATA / "manager/simplex-version"
        if list((DATA / "simplex").glob("*_chat.db")) and (
                not marker.exists() or marker.read_text() != "v7.0.3"):
            await asyncio.to_thread(self.runtime.archive)
        marker.write_text("v7.0.3")
        self.tasks = [asyncio.create_task(self.broker.run()), asyncio.create_task(self.runtime.supervise())]

    async def cleanup(self, app):
        self.runtime.stopping = True
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.close_integrations()
        await self.runtime.stop()


@web.middleware
async def errors(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException:
        raise
    except (ConnectionError, asyncio.TimeoutError):
        response = web.json_response({"error": "Cliente no disponible o respuesta agotada. Consulta el historial antes de repetir un envío."}, status=503)
    except (ValueError, KeyError, TypeError) as error:
        response = web.json_response({"error": str(error)}, status=400)
    except OSError:
        response = web.json_response({"error": "Error de almacenamiento o proceso. Comprueba el espacio libre y los permisos del volumen."}, status=500)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"
    return response


@web.middleware
async def authentication(request, handler):
    manager = request.app["manager"]
    if request.method not in ("GET", "HEAD"):
        # No CORS and a mandatory non-simple header: cross-origin forms cannot mutate state.
        if request.headers.get("X-SimpleX-UI") != "1":
            raise web.HTTPForbidden(text="Missing request header")
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
    if request.path.startswith("/api/") and request.path not in ("/api/login", "/api/password"):
        session = request.cookies.get(COOKIE, "")
        expires = manager.sessions.get(session, 0)
        if expires < time.time():
            manager.sessions.pop(session, None)
            return web.json_response({"error": "Inicia sesión."}, status=401)
    return await handler(request)


async def login(request):
    manager = request.app["manager"]
    now = time.time()
    while manager.login_attempts and now - manager.login_attempts[0] > 60:
        manager.login_attempts.popleft()
    if len(manager.login_attempts) >= 10:
        return web.json_response({"error": "Espera un minuto antes de volver a intentarlo."}, status=429)
    manager.login_attempts.append(now)
    body = await request.json()
    password = body.get("password", "")
    if not isinstance(password, str) or len(password) > 1024:
        raise ValueError("Contraseña no válida.")
    if body.get("username", "admin") != "admin" or not await asyncio.to_thread(manager.config.verify, password):
        return web.json_response({"error": "Credenciales incorrectas."}, status=401)
    manager.sessions = {key: value for key, value in manager.sessions.items() if value > now}
    if len(manager.sessions) >= 32:
        manager.sessions.pop(next(iter(manager.sessions)))
    token = secrets.token_urlsafe(32)
    manager.sessions[token] = now + 12 * 3600
    response = web.json_response({"ok": True})
    response.set_cookie(COOKIE, token, httponly=True, samesite="Strict", max_age=12 * 3600,
                        secure=request.secure or request.headers.get("X-Forwarded-Proto") == "https")
    return response


async def logout(request):
    request.app["manager"].sessions.pop(request.cookies.get(COOKIE, ""), None)
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE)
    return response


async def change_password(request):
    manager = request.app["manager"]
    now = time.time()
    while manager.login_attempts and now - manager.login_attempts[0] > 60:
        manager.login_attempts.popleft()
    if len(manager.login_attempts) >= 10:
        return web.json_response({"error": "Espera un minuto antes de volver a intentarlo."}, status=429)
    manager.login_attempts.append(now)
    body = await request.json()
    current, new = body.get("current", ""), body.get("new", "")
    if not isinstance(current, str) or not isinstance(new, str):
        raise ValueError("Contraseña no válida.")
    if len(new) < 8 or len(new) > 1024:
        raise ValueError("La nueva contraseña debe tener entre 8 y 1024 caracteres.")
    if secrets.compare_digest(current, new):
        raise ValueError("La nueva contraseña debe ser distinta de la actual.")
    if not await asyncio.to_thread(manager.config.verify, current):
        return web.json_response({"error": "La contraseña actual no es correcta."}, status=401)
    await asyncio.to_thread(manager.config.set_password, new)
    manager.sessions.clear()
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE)
    return response


async def status(request):
    m = request.app["manager"]
    return web.json_response({"version": APP_VERSION, "revision": "diagnostics-1", "simplex_version": "7.0.3",
        "connected": m.broker.connected.is_set(), "generation": m.broker.generation,
        "last_exit_code": m.runtime.last_exit_code, "last_event": m.broker.last_event,
        "last_event_at": m.broker.last_event_at,
        "integration_enabled": m.config.value["integration_enabled"],
        "integration_connections": len(m.integration_sockets), "integration_commands": m.connector_commands,
        "integration_last_command": m.connector_last_command})


async def diagnostics(request):
    m = request.app["manager"]
    state = json.loads((await status(request)).text)
    state.update({"process_running": m.runtime.process is not None and m.runtime.process.returncode is None,
                  "proxy_configured": bool(m.config.value.get("socks_proxy")),
                  "smp_custom": bool(m.config.value.get("smp_servers")),
                  "xftp_custom": bool(m.config.value.get("xftp_servers"))})
    entries = await asyncio.to_thread(m.diagnostics.entries)
    return web.json_response({"state": state, "entries": entries,
        "notes": "Horas UTC. Hasta 500 registros recientes; rotación de 4 × 1 MiB. No se incluyen mensajes ni credenciales. La conexión local no acredita entrega SMP. Los campos de texto privado se omiten."})


async def client_diagnostic(request):
    body = await request.json()
    phase = body.get("phase")
    if phase not in ("history", "snapshot", "send", "other"):
        raise ValueError("Fase no válida.")
    request.app["manager"].diagnostics.record("warning", "panel", phase,
        "El navegador informa de un fallo al consultar o ejecutar la operación.")
    return web.json_response({"ok": True})


async def snapshot(request):
    m = request.app["manager"]
    user = await m.user()
    chats = await m.broker.response(f'/_get chats {int(user["userId"])} pcc=on count=200')
    return web.json_response({"user": user, "chats": chats.get("chats", [])})


async def history(request):
    target = request.query.get("target", "")
    if not TARGET.fullmatch(target):
        raise ValueError("Conversación no válida.")
    resp = await request.app["manager"].broker.response(f"/_get chat {target} count=100")
    return web.json_response(resp)


async def action(request):
    m = request.app["manager"]
    body = await request.json()
    kind = body.get("action")
    if kind == "send":
        target, text = body.get("target", ""), body.get("text", "")
        if not isinstance(target, str) or not TARGET.fullmatch(target):
            raise ValueError("Destinatario no válido.")
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise ValueError("Escribe un mensaje de hasta 8000 caracteres.")
        current = await m.broker.response(f"/_get chat {target} count=1")
        info = current.get("chat", {}).get("chatInfo", {})
        contact = info.get("contact", {})
        if info.get("type") == "contactRequest" or (contact.get("contactRequestId") and not contact.get("activeConn")):
            m.diagnostics.record("warning", "panel", "send_blocked", "Envío bloqueado: primero hay que aceptar la solicitud de contacto.", operation="send", target=target)
            raise ValueError("Este contacto tiene una solicitud pendiente. Acéptala en «Perfil y contactos» antes de enviar.")
        command = f'/_send {target} json ' + json.dumps([{"msgContent": {"type": "text", "text": text}}])
    elif kind in ("accept", "reject"):
        ident = str(body.get("id", ""))
        if not re.fullmatch(r"[1-9][0-9]*", ident):
            raise ValueError("ID de solicitud no válido.")
        command = "/_" + kind + " " + ident
    elif kind == "connect":
        link = body.get("link", "")
        if not valid_link(link):
            raise ValueError("Pega un enlace de conexión SimpleX válido y sin espacios.")
        command = "/connect " + link
    elif kind == "profile":
        name, full = body.get("name", "").strip(), body.get("full_name", "").strip()
        if not name or len(name) > 64 or len(full) > 256:
            raise ValueError("Nombre obligatorio (máximo 64 caracteres); nombre completo máximo 256.")
        user = await m.user()
        profile = dict(user.get("profile", {}))
        profile["displayName"], profile["fullName"] = name, full
        command = f'/_profile {int(user["userId"])} ' + json.dumps(profile)
    elif kind == "create_address":
        user = await m.user()
        command = f'/_address {int(user["userId"])}'
    elif kind == "console":
        command = body.get("command", "")
        if not isinstance(command, str) or not command.startswith("/") or len(command) > 32768 or "\n" in command or "\r" in command:
            raise ValueError("Introduce una única orden de SimpleX.")
        # Administrative console deliberately has full CLI API permissions.
    else:
        raise ValueError("Acción desconocida.")
    return web.json_response(await m.broker.response(command))


def find_link(value):
    if isinstance(value, str) and valid_link(value):
        return value
    if isinstance(value, dict):
        for key in ("connFullLink", "connLinkContact", "contactLink"):
            if key in value and (found := find_link(value[key])):
                return found
        for item in value.values():
            if found := find_link(item):
                return found
    return None


async def address(request):
    m = request.app["manager"]
    user = await m.user()
    packet = await m.broker.request(f'/_show_address {int(user["userId"])}')
    return web.json_response({"address": find_link(packet)})


async def qr(request):
    # Read address from SimpleX instead of accepting arbitrary data in URL logs.
    m = request.app["manager"]
    user = await m.user()
    packet = await m.broker.request(f'/_show_address {int(user["userId"])}')
    link = find_link(packet)
    if not link:
        raise web.HTTPNotFound()
    image = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage)
    stream = io.BytesIO()
    image.save(stream)
    return web.Response(body=stream.getvalue(), content_type="image/svg+xml")


async def settings(request):
    m = request.app["manager"]
    keys = ("smp_servers", "xftp_servers", "socks_proxy")
    if request.method == "GET":
        return web.json_response({key: m.config.value.get(key, "") for key in keys})
    body = await request.json()
    updates = {}
    for key in keys:
        value = body.get(key, "")
        if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
            raise ValueError("Configuración no válida.")
        updates[key] = value.strip()
    proxy = updates["socks_proxy"]
    if proxy:
        match = re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3}):(\d{1,5})", proxy)
        if (not match or any(str(int(part)) != part for part in match.groups())
                or any(int(part) > 255 for part in match.groups()[:4])
                or not 1 <= int(match.group(5)) <= 65535):
            raise ValueError("SOCKS debe ser una IPv4 válida y un puerto entre 1 y 65535, por ejemplo 10.21.0.2:9050.")
    m.config.value.update(updates)
    m.config.save()
    await m.close_integrations()
    await m.runtime.restart()
    return web.json_response({"ok": True})


async def integrations(request):
    m = request.app["manager"]
    if request.method == "POST":
        body = await request.json()
        if type(body.get("enabled")) is not bool:
            raise ValueError("Estado no válido.")
        m.config.value["integration_enabled"] = body["enabled"]
        if body.get("rotate") is True:
            m.config.value["integration_token"] = secrets.token_urlsafe(32)
        m.config.save()
        await m.close_integrations()
    token = m.config.value["integration_token"]
    return web.json_response({"enabled": m.config.value["integration_enabled"],
        "url": f"ws://simplex:{token}@albercoin-store-simplex-chat-cli_server_1:2192/ws"})


async def hermes_config(request):
    m = request.app["manager"]
    if not m.config.value["integration_enabled"]:
        raise ValueError("Activa primero el conector.")
    users = request.query.get("users", "").replace(" ", "")
    if len(users) > 1024 or not re.fullmatch(r"[1-9][0-9]*(,[1-9][0-9]*)*", users):
        raise ValueError("Indica al menos un ID numérico de contacto autorizado.")
    token = m.config.value["integration_token"]
    env = {"SIMPLEX_WS_URL": f"ws://simplex:{token}@albercoin-store-simplex-chat-cli_server_1:2192/ws",
           "SIMPLEX_ALLOWED_USERS": users, "SIMPLEX_ALLOW_ALL_USERS": "false",
           "SIMPLEX_AUTO_ACCEPT": "false", "SIMPLEX_GROUP_ALLOWED": "",
           "SIMPLEX_HOME_CHANNEL": users.split(",")[0]}
    return web.Response(text=json.dumps(env, indent=2), content_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="simplex-hermes.json"'})


async def backups(request):
    m = request.app["manager"]
    if request.method == "POST":
        await m.close_integrations()
        name = await m.runtime.backup()
        return web.json_response({"name": name})
    items = [{"name": p.name, "size": p.stat().st_size} for p in sorted((DATA / "backups").glob("*.tar.gz"), reverse=True)]
    return web.json_response({"backups": items})


async def download_backup(request):
    name = request.match_info["name"]
    if not re.fullmatch(r"[0-9T-]+\.tar\.gz", name):
        raise web.HTTPNotFound()
    path = DATA / "backups" / name
    if not path.is_file() or path.is_symlink():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def connector(request):
    m = request.app["manager"]
    if request.headers.get("Origin"):
        raise web.HTTPForbidden(text="Use a non-browser WebSocket client")
    expected = "simplex:" + m.config.value["integration_token"]
    try:
        scheme, payload = request.headers.get("Authorization", "").split(" ", 1)
        supplied = base64.b64decode(payload, validate=True).decode()
    except (ValueError, UnicodeError):
        scheme, supplied = "", ""
    if not m.config.value["integration_enabled"] or scheme.lower() != "basic" or not secrets.compare_digest(supplied.encode(), expected.encode()):
        m.diagnostics.record("warning", "connector", "authentication_failed", "Conector desactivado o credencial incorrecta. Revisa SIMPLEX_WS_URL y si se revocó el token.", source="hermes/connector")
        return web.Response(status=401, headers={"WWW-Authenticate": 'Basic realm="SimpleX"'})
    if len(m.integration_sockets) >= 8:
        raise web.HTTPServiceUnavailable()
    if not m.broker.connected.is_set() or m.runtime.paused:
        raise web.HTTPServiceUnavailable()
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=65536)
    await ws.prepare(request)
    m.integration_sockets.add(ws)
    m.diagnostics.record("info", "connector", "connected", "Cliente conectado al conector; su identidad de aplicación no se verifica.", source="hermes/connector", clients=len(m.integration_sockets))
    queue = asyncio.Queue(maxsize=256)
    m.broker.listeners.add(queue)

    async def events():
        while True:
            packet = await queue.get()
            if packet is None:
                await ws.close(code=1012, message=b"Reconnect and refresh state")
                return
            await ws.send_json(packet)

    pump = asyncio.create_task(events())
    try:
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue
            corr = None
            try:
                packet = json.loads(message.data)
                corr = packet.get("corrId")
                command = packet.get("cmd")
                if not isinstance(command, str) or not command.startswith("/") or len(command) > 32768:
                    raise ValueError("Invalid SimpleX command")
                if corr is not None and (not isinstance(corr, str) or len(corr) > 256):
                    raise ValueError("Invalid correlation ID")
                m.connector_commands += 1
                m.connector_last_command = time.time()
                response = await m.broker.request(command, source="hermes/connector")
                response["corrId"] = corr
                await ws.send_json(response)
            except (ValueError, TypeError, AttributeError, ConnectionError, asyncio.TimeoutError) as exc:
                m.diagnostics.record("error", "connector", "command_failed", "Orden del conector fallida. Revisa formato, disponibilidad del cliente y operaciones próximas.", source="hermes/connector", error_type=type(exc).__name__)
                await ws.send_json({"corrId": corr if isinstance(corr, str) else None,
                    "resp": {"type": "chatCmdError", "chatError": {"type": "managerError", "message": "Command failed; inspect chat state before retrying"}}})
    finally:
        m.integration_sockets.discard(ws)
        m.diagnostics.record("info", "connector", "disconnected", "Cliente desconectado del conector.", source="hermes/connector", clients=len(m.integration_sockets))
        m.broker.listeners.discard(queue)
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionError):
            await pump
    return ws


async def health(request):
    return web.json_response({"ok": True})


async def index(request):
    return web.FileResponse(STATIC / "index.html")


async def static_file(request):
    name = request.match_info["name"]
    if name not in ("app.js", "styles.css", "help.css"):
        raise web.HTTPNotFound()
    return web.FileResponse(STATIC / name)


async def serve():
    for name in ("simplex", "manager", "backups"):
        (DATA / name).mkdir(parents=True, exist_ok=True)
    manager = Manager()
    app = web.Application(middlewares=[errors, authentication], client_max_size=128 * 1024)
    app["manager"] = manager
    app.add_routes([web.get("/", index), web.get("/healthz", health), web.get("/static/{name}", static_file),
        web.get("/api/diagnostics", diagnostics), web.post("/api/diagnostics/client", client_diagnostic),
        web.post("/api/login", login), web.post("/api/logout", logout), web.post("/api/password", change_password), web.get("/api/status", status),
        web.get("/api/snapshot", snapshot), web.get("/api/history", history), web.post("/api/action", action),
        web.get("/api/address", address), web.get("/api/qr", qr),
        web.get("/api/settings", settings), web.post("/api/settings", settings),
        web.get("/api/integrations", integrations), web.post("/api/integrations", integrations),
        web.get("/api/hermes-config", hermes_config), web.get("/api/backups", backups),
        web.post("/api/backups", backups), web.get("/api/backups/{name}", download_backup)])
    app.on_startup.append(manager.startup)
    app.on_cleanup.append(manager.cleanup)
    integration_app = web.Application(client_max_size=65536)
    integration_app["manager"] = manager
    integration_app.add_routes([web.get("/ws", connector)])
    runner = web.AppRunner(app, access_log=None)
    integration_runner = web.AppRunner(integration_app, access_log=None)
    await runner.setup()
    await integration_runner.setup()
    shutdown = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, shutdown.set)
    try:
        await web.TCPSite(runner, "0.0.0.0", 2191).start()
        await web.TCPSite(integration_runner, "0.0.0.0", 2192).start()
        await shutdown.wait()
    finally:
        await integration_runner.cleanup()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
