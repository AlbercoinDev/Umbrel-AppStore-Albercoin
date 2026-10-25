"""Disposable two-client exchange; invoked by probe_client.py inside CLI image."""
import asyncio
import json
from pathlib import Path
import signal
import subprocess
import sys

import aiohttp


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.pending = {}
        self.events = asyncio.Queue()
        self.seq = 0
        self.reader = asyncio.create_task(self.read())

    async def read(self):
        async for msg in self.ws:
            packet = json.loads(msg.data)
            corr = packet.get('corrId')
            if corr in self.pending:
                self.pending.pop(corr).set_result(packet['resp'])
            else:
                await self.events.put(packet.get('resp', packet))

    async def command(self, command):
        self.seq += 1
        corr = str(self.seq)
        future = asyncio.get_running_loop().create_future()
        self.pending[corr] = future
        await self.ws.send_json({'corrId': corr, 'cmd': command})
        return await asyncio.wait_for(future, 30)

    async def event(self, kind):
        async def wait():
            while True:
                packet = await self.events.get()
                if packet.get('type') == kind:
                    return packet
        return await asyncio.wait_for(wait(), 40)


def find(value, key):
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            result = find(child, key)
            if result is not None:
                return result
    if isinstance(value, list):
        for child in value:
            result = find(child, key)
            if result is not None:
                return result
    return None


async def run(uri):
    children = []
    async with aiohttp.ClientSession() as http:
        try:
            clients = []
            for name, port in [('Alice', 5225), ('Bob', 5226)]:
                proxy_args = ['--socks-proxy', '127.0.0.1:9050', '--socks-mode', 'onion'] if '.onion' in uri else []
                children.append(subprocess.Popen(['simplex-chat', '-d', '/tmp/' + name,
                    '--user-display-name', name, '-s', uri, '--smp-proxy', 'never',
                    *proxy_args, '-p', str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                for _ in range(100):
                    try:
                        ws = await http.ws_connect(f'http://127.0.0.1:{port}')
                        break
                    except aiohttp.ClientError:
                        await asyncio.sleep(.1)
                else:
                    raise AssertionError('CLI WebSocket did not start')
                clients.append(Client(ws))
            alice, bob = clients
            response = await alice.command('/_address 1')
            link = find(response, 'connFullLink')
            assert isinstance(link, str), 'Address creation returned ' + response.get('type', 'unknown')
            assert uri.split('@', 1)[1].split(':', 1)[0] in link, 'Invitation did not use the disposable SMP'
            response = await bob.command('/connect ' + link)
            assert response.get('type') != 'chatCmdError', 'Connection rejected'
            received = await alice.event('receivedContactRequest')
            request_id = find(received, 'contactRequestId')
            assert request_id is not None
            await alice.command('/_accept ' + str(request_id))
            ac = await alice.event('contactConnected')
            bc = await bob.event('contactConnected')
            for sender, receiver, contact, text in [
                (alice, bob, ac, 'fixture-a'), (bob, alice, bc, 'fixture-b')]:
                contact_id = find(contact, 'contactId')
                response = await sender.command(f'/_send @{contact_id} json ' + json.dumps([{'msgContent': {'type': 'text', 'text': text}}]))
                assert response.get('type') != 'chatCmdError', 'Send rejected'
                async def wait_message():
                    while True:
                        delivered = await receiver.event('newChatItems')
                        content = find(delivered, 'msgContent')
                        if isinstance(content, dict) and content.get('text') == text:
                            return
                await asyncio.wait_for(wait_message(), 40)
            print('Two real CLI identities: text received in both directions using the private SMP (' + ('onion through Tor' if '.onion' in uri else 'local TCP') + ')')
        finally:
            for child in children:
                child.send_signal(signal.SIGINT)
            for child in children:
                child.wait(timeout=20)


if __name__ == '__main__':
    asyncio.run(run(sys.argv[1]))
