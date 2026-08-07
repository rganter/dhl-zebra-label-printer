from __future__ import annotations

import secrets
import time
import base64
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import PrinterProfile, load_printers
from .label_processor import ProcessedLabel, UnsupportedLabelError, process_dhl_pdf
from .printer import print_label

BASE_DIR = Path(__file__).parent
app = FastAPI(title="DHL Zebra Label Printer")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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


@app.get("/preview/{token}")
def preview(token: str):
    return Response(_get_label(token).preview_png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/download/{token}")
def download(token: str):
    return Response(
        _get_label(token).pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="dhl-label-100x150mm.pdf"', "Cache-Control": "no-store"},
    )


@app.post("/print/{token}", response_class=HTMLResponse)
def do_print(request: Request, token: str, printer: int = Form(...)):
    printers = _printers()
    if printer < 0 or printer >= len(printers):
        raise HTTPException(400, "Unbekannter Drucker")
    try:
        print_label(_get_label(token).pdf, printers[printer])
    except OSError as exc:
        return templates.TemplateResponse(
            request,
            "index.html",
            {"printers": printers, "token": token, "error": f"Drucker nicht erreichbar: {exc}"},
            status_code=502,
        )
    del _labels[token]
    return templates.TemplateResponse(
        request, "index.html", {"printers": printers, "success": f"An {printers[printer].name} gesendet."}
    )
