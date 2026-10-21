"""One upstream socket, correlated responses, bounded event subscriptions."""
import asyncio
import contextlib
import json
import time
import uuid
import re
from .diagnostics import technical, guidance

import aiohttp


class Broker:
    def __init__(self, diagnostics=None):
        self.diagnostics = diagnostics
        self.ws = None
        self.pending = {}
        self.listeners = set()
        self.connected = asyncio.Event()
        self.last_event = None
        self.last_event_at = None
        self.generation = 0

    def log(self, level, phase, summary, **context):
        if self.diagnostics:
            self.diagnostics.record(level, "simplex", phase, summary, **context)

    async def run(self):
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.ws_connect("http://127.0.0.1:5225", heartbeat=20,
                                                  max_msg_size=16 * 1024 * 1024) as ws:
                        self.ws = ws
                        self.generation += 1
                        self.connected.set()
                        self.log("info", "connected", "WebSocket local conectado; no confirma conectividad SMP.", generation=self.generation)
                        async for msg in ws:
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                continue
                            packet = json.loads(msg.data)
                            if not isinstance(packet, dict):
                                continue
                            corr = packet.get("corrId")
                            future = self.pending.get(corr) if isinstance(corr, str) else None
                            if future is not None:
                                if not future.done():
                                    future.set_result(packet)
                                continue
                            # Responses that arrive after a timeout must not leak to another client.
                            if corr:
                                continue
                            resp = packet.get("resp", {})
                            self.last_event = resp.get("type", "unknown") if isinstance(resp, dict) else "unknown"
                            self.last_event_at = time.time()
                            details = technical(resp) if self.last_event == "chatError" else event_context(resp)
                            failed_event = self.last_event == "chatError" or any(code in json.dumps(details) for code in ("sndError", "sndWarning"))
                            self.log("error" if failed_event else "info", "event",
                                     "Evento recibido del cliente SimpleX.", event=self.last_event,
                                     details=details, next_check=guidance(details) if failed_event else None)
                            for queue in tuple(self.listeners):
                                if queue.full():
                                    # A slow client is closed, rather than silently dropping events.
                                    while not queue.empty():
                                        queue.get_nowait()
                                    queue.put_nowait(None)
                                    self.listeners.discard(queue)
                                else:
                                    queue.put_nowait(packet)
                except (aiohttp.ClientError, OSError, ValueError) as exc:
                    self.log("warning", "connection_error", "No se pudo mantener la conexión local. Comprueba el proceso SimpleX.", error_type=type(exc).__name__, errno=getattr(exc, "errno", None))
                finally:
                    self.ws = None
                    self.connected.clear()
                    self.log("warning", "disconnected", "WebSocket local desconectado; se intentará reconectar.")
                    for future in tuple(self.pending.values()):
                        if not future.done():
                            future.set_exception(ConnectionError("SimpleX desconectado; consulta el historial antes de reenviar."))
                    for queue in tuple(self.listeners):
                        if not queue.full():
                            queue.put_nowait(None)
                await asyncio.sleep(2)

    async def request(self, command, timeout=30, source="panel"):
        corr = "manager-" + uuid.uuid4().hex
        started = time.monotonic()
        operation = "send" if command.startswith("/_send ") else "history" if command.startswith("/_get chat ") else "snapshot" if command.startswith("/_get chats ") else "command"
        context = {"tracking": corr, "operation": operation, "source": source}
        target = re.match(r"/_send ([@#][1-9][0-9]*)(?: |$)", command)
        if target:
            context["target"] = target.group(1)
        self.log("info", "requested", "Operación recibida; el contenido de la orden no se registra.", **context)
        if not self.connected.is_set() or self.ws is None:
            self.log("error", "unavailable", "Cliente local desconectado: la orden no se ha enviado.", **context)
            raise ConnectionError("SimpleX está iniciándose o desconectado.")
        if len(self.pending) >= 64:
            raise ConnectionError("Demasiadas operaciones pendientes.")
        future = asyncio.get_running_loop().create_future()
        self.pending[corr] = future
        try:
            await self.ws.send_json({"corrId": corr, "cmd": command})
            self.log("info", "submitted", "Orden enviada al cliente local; pendiente de respuesta.", **context)
            packet = await asyncio.wait_for(future, timeout)
            resp = packet.get("resp", {})
            failed = resp.get("type") in ("chatCmdError", "chatError")
            self.log("error" if failed else "info", "rejected" if failed else "response",
                     "SimpleX rechazó la orden; consulta los códigos del error." if failed else "Respuesta del cliente recibida; aceptación no equivale a entrega.",
                     **context, duration_ms=round((time.monotonic() - started) * 1000), details=technical(resp) if failed else event_context(resp), next_check=guidance(technical(resp)) if failed else None)
            return packet
        except asyncio.TimeoutError:
            self.log("error", "timeout", "Sin respuesta a tiempo. El resultado es incierto: consulta el historial antes de reenviar.", **context, timeout_seconds=timeout)
            raise
        except (ConnectionError, aiohttp.ClientError, OSError) as exc:
            self.log("error", "transport_error", "Falló el transporte local; comprueba el historial antes de reenviar.", **context, error_type=type(exc).__name__)
            raise ConnectionError("Conexión local interrumpida.") from exc
        finally:
            self.pending.pop(corr, None)

    async def disconnect(self):
        if self.ws:
            with contextlib.suppress(aiohttp.ClientError):
                await self.ws.close()

    async def response(self, command):
        packet = await self.request(command)
        resp = packet.get("resp", packet)
        if resp.get("type") in ("chatCmdError", "chatError"):
            raise ValueError(json.dumps(resp, ensure_ascii=False))
        return resp


def event_context(value):
    """Only retain useful IDs and status codes from successful responses/events."""
    result = []
    def visit(obj, depth=0):
        if depth > 12 or len(result) >= 100:
            return
        if isinstance(obj, dict):
            item = {key: obj[key] for key in ("contactId", "groupId", "itemId", "contactRequestId") if isinstance(obj.get(key), int)}
            status = obj.get("itemStatus")
            if isinstance(status, dict):
                item["itemStatus"] = technical(status)
            if item:
                result.append(item)
            for child in obj.values():
                if isinstance(child, (dict, list)):
                    visit(child, depth + 1)
        elif isinstance(obj, list):
            for child in obj[:100]:
                visit(child, depth + 1)
    visit(value)
    return {"type": technical({"type": value.get("type")}).get("type"), "references": result}
