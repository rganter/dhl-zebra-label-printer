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
    crop: tuple[float, float, float, float]


_LABEL_ANCHORS = ("DHL Online Frankierung", "DHL Retoure")


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


def _find_anchors(page: fitz.Page) -> tuple[fitz.Rect, fitz.Rect, str]:
    # search_for retains the correct rectangles for text whose writing direction
    # is rotated inside an otherwise unrotated A4 page.
    shipping: fitz.Rect | None = None
    label_anchor = ""
    for candidate in _LABEL_ANCHORS:
        shipping = _search_union(page, candidate)
        if shipping:
            label_anchor = candidate
            break
    receipt = _search_union(page, "Sendungsinformation", "Ihre Unterlagen")
    if not shipping or not receipt:
        raise UnsupportedLabelError(
            "Die erforderlichen DHL-Textanker wurden nicht gefunden. Das PDF wird aus Sicherheitsgründen nicht gedruckt."
        )
    return shipping, receipt, label_anchor


def _label_rotation(page: fitz.Page, label_anchor: str) -> int:
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", []))
            if label_anchor not in text:
                continue
            dx, dy = line.get("dir", (1.0, 0.0))
            if abs(dx) >= abs(dy):
                return 0 if dx > 0 else 180
            return 270 if dy < 0 else 90
    raise UnsupportedLabelError("Die Orientierung des DHL-Labels konnte nicht bestimmt werden.")


def _tight_label_crop(
    page: fitz.Page,
    coarse: fitz.Rect,
    rotation: int,
    return_footer: fitz.Rect | None = None,
) -> fitz.Rect:
    """Trim the A5 carrier section to the actual ruled shipping label.

    DHL's printable label contains several long, parallel separator rules. Their
    shared endpoints define the label width without relying on fixed A4 pixels.
    Text, image and vector bounds inside that width then define its full length.
    """
    drawings = page.get_drawings()
    vertical = rotation in (90, 270)

    def within_coarse(rect: fitz.Rect) -> bool:
        return (
            coarse.x0 <= rect.x0 <= coarse.x1
            and coarse.y0 <= rect.y0 <= coarse.y1
            and coarse.x0 <= rect.x1 <= coarse.x1
            and coarse.y0 <= rect.y1 <= coarse.y1
        )

    if vertical:
        rules = [
            item["rect"]
            for item in drawings
            if item.get("type") == "s"
            and item["rect"].width < 2
            and item["rect"].height > coarse.height * 0.45
            and within_coarse(item["rect"])
        ]
    else:
        rules = [
            item["rect"]
            for item in drawings
            if item.get("type") == "s"
            and item["rect"].height < 2
            and item["rect"].width > coarse.width * 0.45
            and within_coarse(item["rect"])
        ]
    if len(rules) < 3:
        raise UnsupportedLabelError(
            "Die Begrenzung der Versandmarke konnte nicht sicher erkannt werden."
        )

    if vertical:
        cross_min = min(rect.y0 for rect in rules)
        cross_max = max(rect.y1 for rect in rules)
        slab = fitz.Rect(coarse.x0, cross_min - 12, coarse.x1, cross_max + 12) & coarse
    else:
        cross_min = min(rect.x0 for rect in rules)
        cross_max = max(rect.x1 for rect in rules)
        slab = fitz.Rect(cross_min - 12, coarse.y0, cross_max + 12, coarse.y1) & coarse

    bounds: fitz.Rect | None = None

    def is_return_footer(rect: fitz.Rect) -> bool:
        if not return_footer:
            return False
        # The footer is placed after the actual return label along its long
        # axis. Exclude its entire strip, which also removes the adjacent A4
        # marker without relying on a language- or format-specific word.
        if vertical:
            if return_footer.x0 >= coarse.x0 + coarse.width / 2:
                return rect.x0 >= return_footer.x0 - 12
            return rect.x1 <= return_footer.x1 + 12
        if return_footer.y0 >= coarse.y0 + coarse.height / 2:
            return rect.y0 >= return_footer.y0 - 12
        return rect.y1 <= return_footer.y1 + 12

    def include(rect: fitz.Rect) -> None:
        nonlocal bounds
        if is_return_footer(rect):
            return
        clipped = rect & slab
        if clipped.is_empty:
            return
        if bounds is None:
            bounds = fitz.Rect(clipped)
        else:
            bounds.include_rect(clipped)

    for word in page.get_text("words"):
        include(fitz.Rect(word[:4]))
    for image in page.get_image_info():
        include(fitz.Rect(image["bbox"]))
    for drawing in drawings:
        rect = drawing["rect"]
        # Exclude page-wide cut lines while retaining barcode bars and rules.
        if rect.width < coarse.width * 0.9 and rect.height < coarse.height * 0.9:
            include(rect)

    if bounds is None:
        raise UnsupportedLabelError("Im erkannten Versandlabel wurde kein Inhalt gefunden.")
    bounds = fitz.Rect(bounds.x0 - 6, bounds.y0 - 6, bounds.x1 + 6, bounds.y1 + 6) & coarse

    # Retourenlabels contain a non-printable "Retoure@GKP" footer after the
    # shipping barcode. Do not preserve it (or the following blank strip), so
    # the actual label makes best use of the configured media.
    if return_footer:
        if vertical:
            if return_footer.x0 >= coarse.x0 + coarse.width / 2:
                bounds.x1 = min(bounds.x1, return_footer.x0 - 6)
            else:
                bounds.x0 = max(bounds.x0, return_footer.x1 + 6)
        elif return_footer.y0 >= coarse.y0 + coarse.height / 2:
            bounds.y1 = min(bounds.y1, return_footer.y0 - 6)
        else:
            bounds.y0 = max(bounds.y0, return_footer.y1 + 6)

    if vertical:
        bounds.y0 = max(coarse.y0, cross_min - 6)
        bounds.y1 = min(coarse.y1, cross_max + 6)
    else:
        bounds.x0 = max(coarse.x0, cross_min - 6)
        bounds.x1 = min(coarse.x1, cross_max + 6)
    return bounds


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


def process_dhl_pdf(data: bytes) -> ProcessedLabel:
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
            shipping, receipt, label_anchor = _find_anchors(page)
            coarse_crop = _crop_from_anchors(page, shipping, receipt)
            rotate = _label_rotation(page, label_anchor)
            return_footer = None
            if label_anchor == "DHL Retoure":
                footer_hits = page.search_for("Retoure@GKP")
                if footer_hits:
                    return_footer = footer_hits[0]
            crop = _tight_label_crop(page, coarse_crop, rotate, return_footer)
        except UnsupportedLabelError as exc:
            exc.preview_png = source_preview
            raise

        if rotate in (90, 270):
            target_w, target_h = crop.height, crop.width
        else:
            target_w, target_h = crop.width, crop.height
        output = fitz.open()
        out_page = output.new_page(width=target_w, height=target_h)
        out_page.show_pdf_page(out_page.rect, source, 0, clip=crop, rotate=rotate, keep_proportion=True)
        pdf_bytes = output.tobytes(garbage=4, deflate=True)
        output.close()
        return ProcessedLabel(pdf_bytes, tuple(crop))
    finally:
        source.close()


def format_label_pdf(pdf: bytes, width_mm: float, height_mm: float) -> bytes:
    """Place a format-neutral vector label on the requested media size."""
    source = fitz.open(stream=pdf, filetype="pdf")
    output = fitz.open()
    try:
        width_points = width_mm / 25.4 * 72
        height_points = height_mm / 25.4 * 72
        page = output.new_page(width=width_points, height=height_points)
        page.show_pdf_page(page.rect, source, 0, keep_proportion=True)
        return output.tobytes(garbage=4, deflate=True)
    finally:
        output.close()
        source.close()


def render_label_preview(pdf: bytes, width_mm: float, height_mm: float, dpi: int = 120) -> bytes:
    formatted = format_label_pdf(pdf, width_mm, height_mm)
    document = fitz.open(stream=formatted, filetype="pdf")
    try:
        return _render_page(document[0], dpi)
    finally:
        document.close()


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
