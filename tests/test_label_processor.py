import fitz
import pytest

from app.label_processor import UnsupportedLabelError, process_dhl_pdf


def synthetic_dhl_pdf(rotated: bool = True) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    if rotated:
        page.insert_text((50, 350), "DHL Online Frankierung", fontsize=12, rotate=90)
        page.insert_text((50, 780), "Sendungsinformation - fuer Ihre Unterlagen.", fontsize=12, rotate=90)
        for x in (80, 220, 380, 550):
            page.draw_line((x, 70), (x, 350), width=0.5)
    else:
        page.insert_text((80, 80), "DHL Online Frankierung", fontsize=12)
        page.insert_text((80, 500), "Sendungsinformation - fuer Ihre Unterlagen.", fontsize=12)
    # The production detector deliberately uses the encoding-independent words.
    result = doc.tobytes()
    doc.close()
    return result


def test_rejects_unknown_pdf():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Not a shipping label")
    data = doc.tobytes()
    doc.close()
    with pytest.raises(UnsupportedLabelError):
        process_dhl_pdf(data)


def test_rejects_multiple_pages():
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    with pytest.raises(UnsupportedLabelError, match="einseitige"):
        process_dhl_pdf(data)


def test_rejection_contains_safe_source_preview():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Unknown layout")
    data = doc.tobytes()
    doc.close()
    with pytest.raises(UnsupportedLabelError) as raised:
        process_dhl_pdf(data)
    assert raised.value.preview_png.startswith(b"\x89PNG")


def test_rotated_label_is_trimmed_to_its_ruled_content():
    result = process_dhl_pdf(synthetic_dhl_pdf())
    x0, y0, x1, y1 = result.crop
    assert x0 > 0
    assert y0 > 0
    assert x1 < 595
    assert y1 < 421
    assert result.preview_png.startswith(b"\x89PNG")
