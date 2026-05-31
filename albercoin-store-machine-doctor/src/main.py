from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from modules.cpu.report import latest_report
from modules.cpu.sensors import read_temperatures
from modules.cpu.service import cpu_check_service

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Machine Doctor", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("shutdown")
def shutdown() -> None:
    cpu_check_service.shutdown()


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": "Machine Doctor"}


@app.get("/api/cpu/info")
def cpu_info() -> dict:
    return {"cpu": cpu_check_service.cpu_info()}


@app.get("/api/cpu/status")
def cpu_status() -> dict:
    return cpu_check_service.status()


@app.post("/api/cpu/start")
def cpu_start() -> JSONResponse:
    result = cpu_check_service.start()
    if "error" in result:
        return JSONResponse(result, status_code=409)
    return JSONResponse(result)


@app.post("/api/cpu/cancel")
def cpu_cancel() -> dict:
    return cpu_check_service.cancel()


@app.get("/api/sensors")
def sensors() -> dict:
    return read_temperatures()


@app.get("/api/reports/latest")
def report_latest() -> JSONResponse:
    report = latest_report()
    if report is None:
        return JSONResponse({"report": None}, status_code=404)
    return JSONResponse({"report": report})
