import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from src.diagnostics import Diagnostics, technical
from src.simplex_client import Broker, event_context
from src.main import valid_link


class PrivacyTests(unittest.TestCase):
    def test_error_preserves_codes_but_not_secrets_or_parser_input(self):
        raw = {"type": "chatCmdError", "chatError": {"type": "errorAgent", "agentError": {
            "type": "BROKER", "message": "ws://simplex:secret@host:2192/ws",
            "command": '/_send @1 text private message', "token": "secret", "contactId": 1}}}
        safe = technical(raw)
        self.assertEqual(safe["chatError"]["agentError"]["type"], "BROKER")
        encoded = json.dumps(safe)
        for secret in ("secret", "private message", "ws://"):
            self.assertNotIn(secret, encoded)

    def test_event_extraction_keeps_message_ids_and_delivery_not_content(self):
        safe = event_context({"type": "chatItemsStatusesUpdated", "chatItems": [{"contact": {"contactId": 7, "profile": {"displayName": "private name"}}, "chatItem": {"meta": {"itemId": 12, "itemStatus": {"type": "sndRcvd", "msgRcptStatus": "ok"}}, "content": {"msgContent": {"text": "private text"}}}}]})
        self.assertIn({"itemId": 12, "itemStatus": {"type": "sndRcvd", "msgRcptStatus": "ok"}}, safe["references"])
        self.assertNotIn("private", json.dumps(safe))

    def test_log_rotation_survives_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Diagnostics(Path(directory))
            handler = log.logger.handlers[0]
            handler.maxBytes = 250
            for i in range(30):
                log.record("info", "simplex", "test", "Test", sequence=i)
            for path in Path(directory).glob("diagnostics.jsonl*"):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            reopened = Diagnostics(Path(directory))
            self.assertEqual(reopened.entries()[-1]["sequence"], 29)
            self.assertLessEqual(len(list(Path(directory).glob("diagnostics.jsonl*"))), 4)
            self.assertEqual(log.path.stat().st_mode & 0o777, 0o600)
            handler.close()
            reopened.logger.handlers[0].close()

    def test_relay_short_links_are_supported(self):
        self.assertTrue(valid_link("https://smp5.simplex.im/a#abc"))
        self.assertTrue(valid_link("simplex:/contact#/?v=2"))
        self.assertFalse(valid_link("https://user:secret@host/a"))
        self.assertFalse(valid_link("javascript:alert(1)"))


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_out_of_order_responses_are_correlated_and_timeout_is_cleaned(self):
        broker = Broker()
        broker.connected.set()

        class Socket:
            sent = []
            async def send_json(self, packet):
                self.sent.append(packet)
        socket = Socket()
        broker.ws = socket
        first = asyncio.create_task(broker.request("/user"))
        second = asyncio.create_task(broker.request("/version"))
        await asyncio.sleep(0)
        for packet in reversed(socket.sent):
            broker.pending[packet["corrId"]].set_result({"resp": {"type": packet["cmd"]}})
        self.assertEqual((await first)["resp"]["type"], "/user")
        self.assertEqual((await second)["resp"]["type"], "/version")
        with self.assertRaises(asyncio.TimeoutError):
            await broker.request("/user", timeout=.01)
        self.assertEqual(broker.pending, {})


if __name__ == "__main__":
    unittest.main()
