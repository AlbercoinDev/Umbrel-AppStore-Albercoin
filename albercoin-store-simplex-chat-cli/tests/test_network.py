import unittest
from src.network import servers, network_args, Network
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

ONION = 'a' * 56 + '.onion'


class RoutingTests(unittest.TestCase):
    def test_direct(self):
        config = {'smp_servers': 'smp://abc@relay.example:5223'}
        self.assertEqual(servers(config)[0]['route'], 'clearnet')
        self.assertNotIn('--socks-proxy', network_args(config))

    def test_mixed_prefers_onion_without_fallback(self):
        config = {'smp_servers': f'smp://abc:secret@relay.example,{ONION}:5223'}
        target = servers(config)[0]
        self.assertEqual(target['available'], 'mixed')
        self.assertNotIn('relay.example', target['uri'])
        self.assertEqual(network_args(config)[-1], 'onion')

    def test_external_proxy_preserved(self):
        self.assertEqual(network_args({'socks_proxy': '10.0.0.1:9050'})[-1], 'always')

    def test_reject_invalid(self):
        for uri in ('smp://abc@short.onion', 'smp://abc@host:0', 'https://host', 'smp://abc@host:99999'):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                servers({'smp_servers': uri})


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_and_private_logs(self):
        config = SimpleNamespace(value={'smp_servers': 'smp://abc:secret@relay.example'})
        logs = SimpleNamespace(record=lambda *a, **kw: entries.append((a, kw)))
        entries = []
        network = Network(config, logs)
        child = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'SMP server test passed', None)))
        with patch('src.network.asyncio.create_subprocess_exec', AsyncMock(return_value=child)):
            network.start(force=True)
            await network.task
        self.assertEqual(network.results[0]['status'], 'ok')
        self.assertNotIn('secret', repr(entries))
        self.assertNotIn('uri', network.results[0])

    async def test_failure_classified(self):
        config = SimpleNamespace(value={'smp_servers': 'smp://abc@relay.example'})
        network = Network(config, SimpleNamespace(record=lambda *a, **kw: None))
        child = SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b'failed at CreateQueue AUTH', None)))
        with patch('src.network.asyncio.create_subprocess_exec', AsyncMock(return_value=child)):
            network.start(force=True)
            await network.task
        self.assertEqual(network.results[0]['code'], 'auth')
        with self.assertRaises(ValueError):
            network.start()
