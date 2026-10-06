from __future__ import annotations

import base64
import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import PrinterProfile, load_printers
from .label_processor import (
    ProcessedLabel,
    UnsupportedLabelError,
    format_label_pdf,
    process_dhl_pdf,
    render_label_preview,
)
from .printer import print_label

BASE_DIR = Path(__file__).parent
APP_VERSION = os.getenv("APP_VERSION", "0.2.1")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["app_version"] = APP_VERSION


@dataclass
class CachedLabel:
    created: float
    label: ProcessedLabel


_labels: dict[str, CachedLabel] = {}
MAX_AGE_SECONDS = 15 * 60


def _cleanup() -> None:
    cutoff = time.monotonic() - MAX_AGE_SECONDS
    for token in [key for key, value in _labels.items() if value.created < cutoff]:
        del _labels[token]


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(60)
        _cleanup()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _labels.clear()
    task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        _labels.clear()


app = FastAPI(title="DHL Zebra Label Printer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.middleware("http")
async def prevent_sensitive_response_caching(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


def _get_label(token: str) -> ProcessedLabel:
    _cleanup()
    cached = _labels.get(token)
    if not cached:
        raise HTTPException(404, "Label abgelaufen oder nicht vorhanden")
    return cached.label


def _printers() -> list[PrinterProfile]:
    try:
        return load_printers()
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"Druckerkonfiguration ungültig: {exc}") from exc


def _printer(index: int) -> PrinterProfile:
    printers = _printers()
    if index < 0 or index >= len(printers):
        raise HTTPException(400, "Unbekanntes Druckerprofil")
    return printers[index]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"printers": _printers()})


@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, pdf: UploadFile = File(...)):
    data = await pdf.read(20 * 1024 * 1024 + 1)
    try:
        label = process_dhl_pdf(data)
    except UnsupportedLabelError as exc:
        source_preview = None
        if exc.preview_png:
            source_preview = "data:image/png;base64," + base64.b64encode(exc.preview_png).decode("ascii")
        return templates.TemplateResponse(
            request,
            "index.html",
            {"printers": _printers(), "error": str(exc), "source_preview": source_preview},
            status_code=422,
        )
    finally:
        await pdf.close()
        del data
    _cleanup()
    token = secrets.token_urlsafe(24)
    _labels[token] = CachedLabel(time.monotonic(), label)
    return templates.TemplateResponse(
        request, "index.html", {"printers": _printers(), "token": token, "success": "Versandmarke erkannt."}
    )


@app.get("/preview/{token}/{printer}")
def preview(token: str, printer: int):
    profile = _printer(printer)
    image = render_label_preview(
        _get_label(token).pdf, profile.label_width_mm, profile.label_height_mm
    )
    return Response(image, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/download/{token}/{printer}")
def download(token: str, printer: int):
    profile = _printer(printer)
    formatted = format_label_pdf(
        _get_label(token).pdf, profile.label_width_mm, profile.label_height_mm
    )
    dimensions = f"{profile.label_width_mm:g}x{profile.label_height_mm:g}mm"
    return Response(
        formatted,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="dhl-label-{dimensions}.pdf"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/print/{token}", response_class=HTMLResponse)
def do_print(request: Request, token: str, printer: int = Form(...)):
    printers = _printers()
    profile = _printer(printer)
    try:
        print_label(_get_label(token).pdf, profile)
    except OSError as exc:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "printers": printers,
                "token": token,
                "selected_printer": printer,
                "error": f"Drucker nicht erreichbar: {exc}",
            },
            status_code=502,
        )
    del _labels[token]
    return templates.TemplateResponse(
        request, "index.html", {"printers": printers, "success": f"An {profile.name} gesendet."}
    )
