"""One upstream socket, correlated responses, bounded event subscriptions."""
import asyncio
import contextlib
import json
import time
import uuid

import aiohttp


class Broker:
    def __init__(self):
        self.ws = None
        self.pending = {}
        self.listeners = set()
        self.connected = asyncio.Event()
        self.last_event = None
        self.last_event_at = None
        self.generation = 0

    async def run(self):
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.ws_connect("http://127.0.0.1:5225", heartbeat=20,
                                                  max_msg_size=16 * 1024 * 1024) as ws:
                        self.ws = ws
                        self.generation += 1
                        self.connected.set()
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
                    print(f"SimpleX WebSocket error: {exc}", flush=True)
                finally:
                    self.ws = None
                    self.connected.clear()
                    for future in tuple(self.pending.values()):
                        if not future.done():
                            future.set_exception(ConnectionError("SimpleX desconectado; consulta el historial antes de reenviar."))
                    for queue in tuple(self.listeners):
                        if not queue.full():
                            queue.put_nowait(None)
                await asyncio.sleep(2)

    async def request(self, command, timeout=30):
        if not self.connected.is_set() or self.ws is None:
            raise ConnectionError("SimpleX está iniciándose o desconectado.")
        if len(self.pending) >= 64:
            raise ConnectionError("Demasiadas operaciones pendientes.")
        corr = "manager-" + uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[corr] = future
        try:
            await self.ws.send_json({"corrId": corr, "cmd": command})
            return await asyncio.wait_for(future, timeout)
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
