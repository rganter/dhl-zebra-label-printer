from __future__ import annotations

import io
import re
from dataclasses import dataclass

import fitz
from PIL import Image, ImageOps


class UnsupportedLabelError(ValueError):
    """Raised when a PDF cannot be cropped safely."""

    def __init__(self, message: str, preview_png: bytes | None = None):
        super().__init__(message)
        self.preview_png = preview_png


@dataclass(frozen=True)
class ProcessedLabel:
    pdf: bytes
    preview_png: bytes
    crop: tuple[float, float, float, float]


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-")).strip().lower()


def _matching_rect(page: fitz.Page, words: list[tuple], phrase: str) -> fitz.Rect | None:
    wanted = _normalise(phrase).split()
    tokens = [_normalise(str(word[4])) for word in words]
    for start in range(len(tokens)):
        joined = ""
        for end in range(start, min(len(tokens), start + len(wanted) + 4)):
            joined = f"{joined} {tokens[end]}".strip()
            if phrase in _normalise(joined) or _normalise(phrase) in joined:
                rect = fitz.Rect(words[start][:4])
                for word in words[start + 1 : end + 1]:
                    rect.include_rect(fitz.Rect(word[:4]))
                return rect
    return None


def _search_union(page: fitz.Page, *parts: str) -> fitz.Rect | None:
    rect: fitz.Rect | None = None
    for part in parts:
        hits = page.search_for(part)
        if not hits:
            return None
        if rect is None:
            rect = fitz.Rect(hits[0])
        else:
            rect.include_rect(hits[0])
    return rect


def _find_anchors(page: fitz.Page) -> tuple[fitz.Rect, fitz.Rect]:
    # search_for retains the correct rectangles for text whose writing direction
    # is rotated inside an otherwise unrotated A4 page.
    shipping = _search_union(page, "DHL Online Frankierung")
    receipt = _search_union(page, "Sendungsinformation", "Ihre Unterlagen")
    if not shipping or not receipt:
        raise UnsupportedLabelError(
            "Die erforderlichen DHL-Textanker wurden nicht gefunden. Das PDF wird aus Sicherheitsgründen nicht gedruckt."
        )
    return shipping, receipt


def _shipping_rotation(page: fitz.Page) -> int:
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", []))
            if "DHL Online Frankierung" not in text:
                continue
            dx, dy = line.get("dir", (1.0, 0.0))
            if abs(dx) >= abs(dy):
                return 0 if dx > 0 else 180
            return 270 if dy < 0 else 90
    raise UnsupportedLabelError("Die Orientierung der Versandmarke konnte nicht bestimmt werden.")


def _crop_from_anchors(page: fitz.Page, shipping: fitz.Rect, receipt: fitz.Rect) -> fitz.Rect:
    bounds = page.rect
    dx = receipt.x0 + receipt.width / 2 - (shipping.x0 + shipping.width / 2)
    dy = receipt.y0 + receipt.height / 2 - (shipping.y0 + shipping.height / 2)
    if abs(dx) > abs(dy):
        split = (shipping.x1 + receipt.x0) / 2 if dx > 0 else (receipt.x1 + shipping.x0) / 2
        crop = fitz.Rect(bounds.x0, bounds.y0, split, bounds.y1) if dx > 0 else fitz.Rect(split, bounds.y0, bounds.x1, bounds.y1)
    else:
        split = (shipping.y1 + receipt.y0) / 2 if dy > 0 else (receipt.y1 + shipping.y0) / 2
        crop = fitz.Rect(bounds.x0, bounds.y0, bounds.x1, split) if dy > 0 else fitz.Rect(bounds.x0, split, bounds.x1, bounds.y1)

    if not crop.contains(shipping) or crop.intersects(receipt):
        raise UnsupportedLabelError("Die Trennung von Versandmarke und Beleg ist geometrisch nicht eindeutig.")
    page_fraction = crop.get_area() / bounds.get_area()
    ratio = crop.width / crop.height
    portrait_ratio = min(ratio, 1 / ratio)
    if not 0.28 <= page_fraction <= 0.78 or not 0.52 <= portrait_ratio <= 0.82:
        raise UnsupportedLabelError(
            "Der erkannte Labelbereich hat unplausible Abmessungen. Bitte die Quellvorschau prüfen."
        )
    return crop


def _render_page(page: fitz.Page, dpi: int, alpha: bool = False) -> bytes:
    pix = page.get_pixmap(dpi=dpi, alpha=alpha)
    return pix.tobytes("png")


def process_dhl_pdf(data: bytes, width_mm: float = 100, height_mm: float = 150) -> ProcessedLabel:
    if len(data) > 20 * 1024 * 1024:
        raise UnsupportedLabelError("Die PDF-Datei ist größer als 20 MB.")
    try:
        source = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise UnsupportedLabelError("Die Datei ist kein lesbares PDF.") from exc
    try:
        if source.page_count != 1:
            raise UnsupportedLabelError("Aktuell werden ausschließlich einseitige DHL-PDFs unterstützt.")
        page = source[0]
        source_preview = _render_page(page, 110)
        try:
            shipping, receipt = _find_anchors(page)
            crop = _crop_from_anchors(page, shipping, receipt)
            rotate = _shipping_rotation(page)
        except UnsupportedLabelError as exc:
            exc.preview_png = source_preview
            raise

        target_w = width_mm / 25.4 * 72
        target_h = height_mm / 25.4 * 72
        output = fitz.open()
        out_page = output.new_page(width=target_w, height=target_h)
        out_page.show_pdf_page(out_page.rect, source, 0, clip=crop, rotate=rotate, keep_proportion=True)
        pdf_bytes = output.tobytes(garbage=4, deflate=True)
        preview = _render_page(out_page, 120)
        output.close()
        return ProcessedLabel(pdf_bytes, preview, tuple(crop))
    finally:
        source.close()


def render_for_printer(pdf: bytes, width: int, height: int) -> Image.Image:
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        page = doc[0]
        scale = min(width / page.rect.width, height / page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
        image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
        canvas = Image.new("L", (width, height), 255)
        canvas.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
        return ImageOps.autocontrast(canvas).convert("1", dither=Image.Dither.NONE)
    finally:
        doc.close()
