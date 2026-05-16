import json
import os
import signal
import subprocess
import tempfile
import shutil
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import requests

app = FastAPI(title="FRP Client Manager")

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "db.json"
FRPC_CONFIG_PATH = Path("/etc/frp/frpc.toml")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

FRPC_PROCESS = None


class Proxy(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r'^[a-zA-Z0-9_-]+$',
    )
    local_port: int = Field(..., ge=1, le=65535)
    remote_port: int = Field(..., ge=1, le=65535)


class AppConfig(BaseModel):
    vps_domain: str = ""
    token: str = ""
    proxies: list[Proxy] = []


def _load_config() -> AppConfig:
    try:
        with open(DB_PATH, "r") as f:
            data = json.load(f)
        return AppConfig(**data)
    except (FileNotFoundError, json.JSONDecodeError):
        return AppConfig()


def _save_config(config: AppConfig) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DB_PATH, "w") as f:
        json.dump(config.model_dump(mode="json"), f, indent=2)


def _atomic_write_frpc(config: AppConfig) -> None:
    lines = [
        f'serverAddr = "{config.vps_domain}"',
        "serverPort = 7000",
        "",
    ]
    for proxy in config.proxies:
        lines.extend([
            "[[proxies]]",
            f'name = "{proxy.name}"',
            'type = "tcp"',
            'localIP = "127.0.0.1"',
            f"localPort = {proxy.local_port}",
            f"remotePort = {proxy.remote_port}",
            "transport.useEncryption = true",
            "",
        ])
    content = "\n".join(lines)

    FRPC_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(FRPC_CONFIG_PATH.parent),
        prefix=".tmp_",
        suffix=".toml",
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        shutil.move(tmp_path, str(FRPC_CONFIG_PATH))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _sync_with_vps(config: AppConfig) -> None:
    if not config.vps_domain or not config.token:
        raise HTTPException(
            status_code=400, detail="VPS domain and token not configured"
        )
    url = f"https://{config.vps_domain}/api/sync"
    payload = {
        "proxies": [
            {"name": p.name, "remote_port": p.remote_port}
            for p in config.proxies
        ]
    }
    headers = {"Authorization": f"Bearer {config.token}"}
    try:
        resp = requests.post(
            url, json=payload, headers=headers, timeout=30
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=502, detail="Cannot connect to VPS - check the domain"
        )
    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=502, detail="Connection to VPS timed out"
        )
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        detail = e.response.text if e.response is not None else str(e)
        raise HTTPException(
            status_code=502, detail=f"VPS returned {code}: {detail}"
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=502, detail=f"Sync failed: {str(e)}"
        )


def _start_frpc() -> None:
    global FRPC_PROCESS
    if not shutil.which("frpc"):
        return
    if FRPC_PROCESS is not None and FRPC_PROCESS.poll() is None:
        return
    FRPC_PROCESS = subprocess.Popen(
        ["frpc", "-c", str(FRPC_CONFIG_PATH)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _restart_frpc() -> None:
    global FRPC_PROCESS
    if FRPC_PROCESS is not None and FRPC_PROCESS.poll() is None:
        try:
            FRPC_PROCESS.send_signal(signal.SIGHUP)
            return
        except Exception:
            pass
    if FRPC_PROCESS is not None:
        try:
            FRPC_PROCESS.kill()
            FRPC_PROCESS.wait(timeout=5)
        except Exception:
            pass
    _start_frpc()


def _monitor_frpc() -> None:
    global FRPC_PROCESS
    while True:
        time.sleep(10)
        if FRPC_PROCESS is not None and FRPC_PROCESS.poll() is not None:
            _start_frpc()


@app.on_event("startup")
async def _ensure_frpc_config():
    global FRPC_PROCESS
    FRPC_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if FRPC_CONFIG_PATH.is_dir():
        try:
            FRPC_CONFIG_PATH.rmdir()
        except OSError:
            shutil.rmtree(FRPC_CONFIG_PATH, ignore_errors=True)
    if not FRPC_CONFIG_PATH.exists():
        config = _load_config()
        if not config.vps_domain:
            config.vps_domain = "0.0.0.0"
        _atomic_write_frpc(config)
    _start_frpc()
    threading.Thread(target=_monitor_frpc, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    config = _load_config()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "vps_domain": config.vps_domain,
            "configured": bool(config.vps_domain and config.token),
            "proxies": config.proxies,
        },
    )


@app.post("/setup")
async def setup(data: dict) -> dict:
    vps_domain = data.get("vps_domain", "").strip()
    token = data.get("token", "").strip()
    if not vps_domain:
        raise HTTPException(status_code=400, detail="VPS domain is required")
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")
    config = _load_config()
    config.vps_domain = vps_domain
    config.token = token
    _save_config(config)
    return {"status": "ok", "message": "VPS configuration saved"}


@app.post("/proxy/add")
async def add_proxy(data: dict) -> dict:
    name = data.get("name", "").strip()
    local_port = data.get("local_port")
    remote_port = data.get("remote_port")

    if not name:
        raise HTTPException(status_code=400, detail="Proxy name is required")
    if not isinstance(local_port, int) or local_port < 1 or local_port > 65535:
        raise HTTPException(
            status_code=400,
            detail="local_port must be an integer between 1 and 65535",
        )
    if not isinstance(remote_port, int) or remote_port < 1 or remote_port > 65535:
        raise HTTPException(
            status_code=400,
            detail="remote_port must be an integer between 1 and 65535",
        )

    config = _load_config()
    if not config.vps_domain or not config.token:
        raise HTTPException(
            status_code=400,
            detail="Configure VPS connection first via /setup",
        )

    for p in config.proxies:
        if p.name == name:
            raise HTTPException(
                status_code=409,
                detail=f"Proxy '{name}' already exists",
            )
        if p.remote_port == remote_port:
            raise HTTPException(
                status_code=409,
                detail=f"Remote port {remote_port} is already in use by proxy '{p.name}'",
            )

    new_proxy = Proxy(
        name=name,
        local_port=local_port,
        remote_port=remote_port,
    )
    config.proxies.append(new_proxy)

    _atomic_write_frpc(config)
    _save_config(config)
    _sync_with_vps(config)
    _restart_frpc()

    return {"status": "ok", "message": f"Proxy '{name}' added and synced"}


@app.post("/proxy/delete/{name}")
async def delete_proxy(name: str) -> dict:
    config = _load_config()
    if not config.vps_domain or not config.token:
        raise HTTPException(
            status_code=400,
            detail="Configure VPS connection first via /setup",
        )

    idx = None
    for i, p in enumerate(config.proxies):
        if p.name == name:
            idx = i
            break

    if idx is None:
        raise HTTPException(
            status_code=404,
            detail=f"Proxy '{name}' not found",
        )

    config.proxies.pop(idx)

    _atomic_write_frpc(config)
    _save_config(config)
    _sync_with_vps(config)
    _restart_frpc()

    return {"status": "ok", "message": f"Proxy '{name}' deleted and synced"}
