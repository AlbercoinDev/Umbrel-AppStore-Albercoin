"""Destructive integration checks on NEW disposable identities only, inside Docker.

docker run --rm --entrypoint /opt/venv/bin/python \
  -v "$PWD:/work:ro" -w /work simplex-chat-cli:1.0.4-test tests/integration.py
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import aiohttp


async def main():
    with tempfile.TemporaryDirectory(prefix="simplex-features-") as tmp:
        root = Path(tmp)
        data = root / "albercoin-store-simplex-chat-cli" / "data"
        data.mkdir(parents=True)
        env = dict(os.environ, APP_DATA=str(data), APP_PASSWORD="DisposableIntegrationPassword")
        manager = await asyncio.create_subprocess_exec(sys.executable, "-m", "src.main", env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        peer_process = await asyncio.create_subprocess_exec("/usr/local/bin/simplex-chat", "-d", str(root / "peer"),
            "-p", "5226", "--user-display-name", "DisposablePeer", "-y",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
                async def api(path, body=None):
                    async with session.request("POST" if body is not None else "GET", "http://127.0.0.1:2191/api/" + path,
                        json=body, headers={"X-SimpleX-UI": "1"}) as response:
                        value = await response.json()
                        assert response.status == 200, (path, value)
                        return value
                for _ in range(60):
                    try:
                        await api("login", {"password": env["APP_PASSWORD"]})
                        if (await api("status"))["connected"]:
                            break
                    except aiohttp.ClientError:
                        pass
                    await asyncio.sleep(1)
                else:
                    raise AssertionError("Manager did not connect")
                async def action(kind, **kwargs):
                    return await api("action", dict(action=kind, **kwargs))
                async def console(command):
                    return await action("console", command=command)
                # Local group exercises history and destructive operations without SMP load.
                user = (await api("snapshot"))["user"]["userId"]
                group = await console(f'/_group {user} ' + json.dumps({"displayName": "DisposableGroup", "fullName": ""}))
                gid = group["groupInfo"]["groupId"]
                target = "#" + str(gid)
                filtered = await api("snapshot", {"search": "DisposableGroup", "unread": False, "before": ""})
                assert any(c["chatInfo"].get("groupInfo", {}).get("groupId") == gid for c in filtered["chats"])
                for i in range(105):
                    await action("send", target=target, text=f"fixture-message-{i:03}")
                history = (await api("history?target=" + target.replace("#", "%23")))["chat"]["chatItems"]
                assert len(history) == 100
                oldest = history[0]["meta"]["itemId"]
                previous = await api(f"history?target=%23{gid}&before={oldest}")
                assert len(previous["chat"]["chatItems"]) >= 5
                found = await api(f"history?target=%23{gid}&search=fixture-message-000")
                assert len(found["chat"]["chatItems"]) == 1
                ident = history[-1]["meta"]["itemId"]
                await action("edit", target=target, id=ident, text="edited-fixture")
                await action("send", target=target, text="quoted-fixture", quote=ident)
                quoted = (await api("history", {"target": target}))["chat"]["chatItems"][-1]
                assert quoted.get("quotedItem")
                await action("delete_message", target=target, id=quoted["meta"]["itemId"], mode="internal", confirm=target)
                await action("delete_message", target=target, id=ident, mode="broadcast", confirm=target)
                await action("clear", target=target, confirm=target)
                cleared = (await api(f"history?target=%23{gid}"))["chat"]["chatItems"]
                assert not any(i.get("content", {}).get("msgContent") for i in cleared), [(i.get("content", {}).get("type")) for i in cleared]
                print("PASS: real SimpleX pagination, full-history search, editing, quote, deletion and clear")
                # Encryption and offline restoration use only disposable identity data.
                identity = root / "identity.txt"
                subprocess.run(["age-keygen", "-o", str(identity)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                recipient = subprocess.check_output(["age-keygen", "-y", str(identity)], text=True).strip()
                await api("backup-options", {"interval": 0, "recipient": recipient})
                backup = (await api("backups", {}))["name"]
                assert backup.endswith(".tar.gz.age")
                assert not list((data / "backups").glob("*.tar.gz"))
                print("PASS: encrypted backup leaves no plaintext archive")
                # Connect a second disposable identity over the public SMP infrastructure.
                for _ in range(45):
                    try:
                        ws = await session.ws_connect("http://127.0.0.1:5226")
                        break
                    except aiohttp.ClientError:
                        await asyncio.sleep(1)
                else:
                    raise AssertionError("Peer did not start")
                async def peer(command):
                    await ws.send_json({"corrId": "integration", "cmd": command})
                    while True:
                        packet = await ws.receive_json(timeout=60)
                        if packet.get("corrId") == "integration":
                            result = packet["resp"]
                            assert result.get("type") not in ("chatCmdError", "chatError"), result
                            return result
                for _ in range(30):
                    if (await api("status"))["connected"]:
                        break
                    await asyncio.sleep(1)
                await action("create_address")
                link = (await api("address"))["address"]
                await peer("/connect " + link)
                for _ in range(60):
                    chats = (await api("snapshot"))["chats"]
                    request = next((c["chatInfo"].get("contactRequest") or c["chatInfo"].get("contact")
                        for c in chats if c["chatInfo"]["type"] == "contactRequest" or
                        (c["chatInfo"].get("contact", {}).get("contactRequestId") and not c["chatInfo"].get("contact", {}).get("activeConn"))), None)
                    if request:
                        break
                    await asyncio.sleep(1)
                assert request, "No request received"
                await action("accept", id=request["contactRequestId"])
                for _ in range(60):
                    contacts = [c["chatInfo"]["contact"] for c in (await api("snapshot"))["chats"] if c["chatInfo"]["type"] == "direct" and c["chatInfo"]["contact"].get("localDisplayName") == "DisposablePeer"]
                    if contacts and contacts[0].get("activeConn", {}).get("connStatus", {}).get("type") in ("ready", "sndReady"):
                        break
                    await asyncio.sleep(1)
                direct = "@" + str(contacts[0]["contactId"])
                sent = await action("send", target=direct, text="disposable-roundtrip")
                peer_user = (await peer("/user"))["user"]["userId"]
                for _ in range(60):
                    peer_chats = (await peer(f"/_get chats {peer_user} pcc=on count=200"))["chats"]
                    peer_contact = next((c["chatInfo"]["contact"] for c in peer_chats if c["chatInfo"]["type"] == "direct" and c["chatInfo"]["contact"].get("localDisplayName") == "SimpleX"), None)
                    if peer_contact:
                        ptarget = "@" + str(peer_contact["contactId"])
                        phistory = await peer(f"/_get chat {ptarget} count=100")
                        if "disposable-roundtrip" in json.dumps(phistory):
                            break
                    await asyncio.sleep(1)
                else:
                    raise AssertionError("Message not delivered")
                await peer(f'/_send {ptarget} text disposable-reply')
                for _ in range(60):
                    if "disposable-reply" in json.dumps(await api("history?target=" + direct)):
                        break
                    await asyncio.sleep(1)
                else:
                    raise AssertionError("Reply not delivered")
                received = (await api("history", {"target": direct, "search": "disposable-reply"}))["chat"]["chatItems"]
                await action("read", target=direct, ids=[received[-1]["meta"]["itemId"]])
                await action("delete_contact", target=direct, confirm=direct)
                await action("leave", target=target, confirm=target)
                report = json.dumps(await api("diagnostics"))
                assert "disposable-reply" not in report and "edited-fixture" not in report
                await ws.close()
                print("PASS: two real identities, bidirectional text, mark-read, contact delete and group leave; private diagnostics")
            manager.terminate(); await manager.wait()
            subprocess.run([sys.executable, "scripts/restore.py", "--data", str(data), "--archive", str(data / "backups" / backup),
                "--identity", str(identity), "--confirm", "APP-STOPPED-RESTORE"], check=True, stdout=subprocess.DEVNULL)
            assert list((data / "simplex").glob("*_chat.db"))
            print("PASS: encrypted offline restoration")
        finally:
            for process in (manager, peer_process):
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 30)
                    except asyncio.TimeoutError:
                        process.kill(); await process.wait()


if __name__ == "__main__":
    asyncio.run(main())
