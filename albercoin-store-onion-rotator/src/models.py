from pydantic import BaseModel
from typing import Optional


class AppInfo(BaseModel):
    app_id: str
    hostname_path: str
    onion_address: str
    status: str


class RotateRequest(BaseModel):
    app_ids: list[str]


class RotateResult(BaseModel):
    app_id: str
    old_onion: str
    new_onion: str
    status: str
    message: str


class RotateResponse(BaseModel):
    results: list[RotateResult]


class LogEntry(BaseModel):
    timestamp: str
    level: str
    message: str


class HealthResponse(BaseModel):
    status: str
    tor_data_dir: str
    tor_data_accessible: bool
    docker_accessible: bool
    dry_run: bool
