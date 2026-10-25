import asyncio
import json
import os
import tempfile
import subprocess
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.backups import inventory, RETENTION
from src.main import action, history, positive_id, backup_options, delete_backup
from src.runtime import Runtime
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from src.main import events, authentication, errors, COOKIE


class BackupRetention(unittest.TestCase):
    def test_age_not_mtime_and_safe_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            now = datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp()
            for name in ("20261014T000000-1.tar.gz", "20261014T000000-2.tar.gz.age",
                         "20261015T000000-1.tar.gz", "20261025T000000-1.tar.gz", "notes.txt"):
                (directory / name).write_text("test")
            (directory / "20261013T000000-1.tar.gz").symlink_to(directory / "notes.txt")
            items = inventory(directory, now)
            self.assertEqual(len(items), 2)
            self.assertFalse((directory / "20261014T000000-1.tar.gz").exists())
            self.assertFalse((directory / "20261014T000000-2.tar.gz.age").exists())
            self.assertTrue((directory / "notes.txt").exists())
            self.assertTrue((directory / "20261013T000000-1.tar.gz").is_symlink())
            self.assertEqual(items[0]["expires"], now + RETENTION)

    def test_failed_encryption_exposes_no_plaintext_and_preserves_old_copy(self):
        with tempfile.TemporaryDirectory() as tmp, patch("src.runtime.DATA", Path(tmp)):
            data = Path(tmp)
            for name in ("simplex", "manager", "backups"):
                (data / name).mkdir()
            (data / "simplex" / "fixture_chat.db").write_bytes(b"fixture")
            old = data / "backups" / "20261025T000000-1.tar.gz.age"
            old.write_bytes(b"old")
            runtime = Runtime(SimpleNamespace(value={"backup_recipient": "age1" + "a" * 58}), None)
            def fail(command, **kwargs):
                self.assertFalse(list((data / "backups").glob("*.tar.gz")))
                raise subprocess.CalledProcessError(1, ["age"])
            with patch("src.runtime.subprocess.run", side_effect=fail):
                with self.assertRaises(subprocess.CalledProcessError):
                    runtime.archive()
            self.assertEqual(list((data / "backups").iterdir()), [old])

    def test_low_space_does_not_create_archive(self):
        with tempfile.TemporaryDirectory() as tmp, patch("src.runtime.DATA", Path(tmp)), \
                patch("src.runtime.storage", return_value={"free": 1, "data": 100}):
            (Path(tmp) / "backups").mkdir()
            runtime = Runtime(SimpleNamespace(value={}), None)
            with self.assertRaises(ValueError):
                runtime.archive()
            self.assertEqual(list((Path(tmp) / "backups").iterdir()), [])


class ChatCommands(unittest.IsolatedAsyncioTestCase):
    def request(self, body=None, query=None):
        self.broker = SimpleNamespace(response=AsyncMock(return_value={}))
        self.config = SimpleNamespace(value={}, save=lambda: None)
        return SimpleNamespace(json=AsyncMock(return_value=body), query=query or {},
                               app={"manager": SimpleNamespace(broker=self.broker, config=self.config)})

    async def test_destructive_actions_require_confirmation(self):
        for kind in ("clear", "delete_contact", "leave", "delete_message"):
            req = self.request({"action": kind, "target": "@1"})
            with self.assertRaises(ValueError):
                await action(req)
            self.broker.response.assert_not_awaited()

    async def test_command_mapping(self):
        for body, expected in [
            ({"action": "clear"}, "/_clear chat @1"),
            ({"action": "delete_contact"}, "/_delete @1 full notify=on"),
            ({"action": "delete_message", "id": 7}, "/_delete item @1 7 internal"),
            ({"action": "delete_message", "id": 7, "mode": "broadcast"}, "/_delete item @1 7 broadcast"),
        ]:
            req = self.request(dict(target="@1", confirm="@1", **body))
            await action(req)
            self.broker.response.assert_awaited_once_with(expected)

    async def test_read_only_visible_ids(self):
        req = self.request({"action": "read", "target": "@1", "ids": [2, 5]})
        await action(req)
        self.broker.response.assert_awaited_once_with("/_read chat items @1 2,5")

    async def test_group_leave_and_direct_guard(self):
        req = self.request({"action": "leave", "target": "#2", "confirm": "#2"})
        await action(req)
        self.broker.response.assert_awaited_once_with("/_leave #2")
        req = self.request({"action": "delete_contact", "target": "#2", "confirm": "#2"})
        with self.assertRaises(ValueError):
            await action(req)

    async def test_history_pagination_search_and_injection(self):
        req = self.request(query={"target": "@1", "before": "99", "search": "hola mundo"})
        await history(req)
        self.broker.response.assert_awaited_once_with("/_get chat @1 before=99 count=100 search=hola mundo")
        for query in ({"target": "@1\n/user"}, {"target": "@1", "before": "-1"}, {"target": "@1", "search": "x\n/user"}):
            with self.assertRaises(ValueError):
                await history(self.request(query=query))

    async def test_edit_json_and_quote(self):
        req = self.request({"action": "edit", "target": "@1", "id": 4, "text": 'texto\n"nuevo"'})
        await action(req)
        command = self.broker.response.call_args.args[0]
        self.assertEqual(json.loads(command.split(" json ", 1)[1])["msgContent"]["text"], 'texto\n"nuevo"')
        req = self.request({"action": "send", "target": "@1", "quote": 4, "text": "respuesta"})
        await action(req)
        command = self.broker.response.call_args.args[0]
        self.assertEqual(json.loads(command.split(" json ", 1)[1])[0]["quotedItemId"], 4)

    async def test_invalid_ids_and_options(self):
        for value in (True, 0, -1, "1\n/user", None):
            with self.assertRaises(ValueError):
                positive_id(value)
        for body in ({"interval": True}, {"interval": 1}, {"recipient": "secret"}):
            with self.assertRaises(ValueError):
                await backup_options(self.request(body))

    async def test_backup_delete_scope(self):
        with tempfile.TemporaryDirectory() as tmp, patch("src.main.DATA", Path(tmp)):
            directory = Path(tmp) / "backups"; directory.mkdir()
            name = "20261025T120000-1.tar.gz"
            (directory / name).write_bytes(b"test")
            await delete_backup(self.request({"name": name, "confirm": name}))
            self.assertFalse((directory / name).exists())
            with self.assertRaises(ValueError):
                await delete_backup(self.request({"name": "../settings.json", "confirm": "../settings.json"}))


class EventStream(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_content_free_stream_and_session_revocation(self):
        broker = SimpleNamespace(listeners=set())
        manager = SimpleNamespace(broker=broker, sessions={"test": time.time() + 60})
        app = web.Application(middlewares=[errors, authentication])
        app["manager"] = manager
        app.router.add_get("/api/events", events)
        async with TestClient(TestServer(app)) as client:
            unauthorized = await client.get("/api/events")
            self.assertEqual(unauthorized.status, 401)
            client.session.cookie_jar.update_cookies({COOKIE: "test"})
            stream = await client.get("/api/events")
            self.assertEqual(stream.headers["Content-Type"], "text/event-stream")
            self.assertEqual(await stream.content.readuntil(b"\n\n"), b"data: refresh\n\n")
            queue = next(iter(broker.listeners))
            queue.put_nowait({"resp": {"type": "chatItemsDeleted", "private": "message-secret"}})
            payload = await stream.content.readuntil(b"\n\n")
            self.assertIn(b'"invalidate": true', payload)
            self.assertNotIn(b"message-secret", payload)
            manager.sessions.clear()
            queue.put_nowait({"resp": {"type": "newChatItems", "private": "secret"}})
            self.assertEqual(await stream.content.read(), b"")
            self.assertFalse(broker.listeners)


class ScheduledBackups(unittest.IsolatedAsyncioTestCase):
    async def test_maintenance_runs_without_ui_or_manual_backup(self):
        runtime = Runtime(SimpleNamespace(value={"backup_interval": 24, "backup_last": 0}), None)
        runtime.backup = AsyncMock(return_value="test")
        with patch("src.runtime.inventory") as prune, patch("src.runtime.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await runtime.maintain_backups()
            prune.assert_called_once()
            runtime.backup.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
