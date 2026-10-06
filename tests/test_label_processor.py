import fitz
import pytest

from app.label_processor import (
    UnsupportedLabelError,
    format_label_pdf,
    process_dhl_pdf,
    render_label_preview,
)


def synthetic_dhl_pdf(
    rotated: bool = True,
    label_anchor: str = "DHL Online Frankierung",
    return_footer: bool = False,
) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    if rotated:
        page.insert_text((50, 350), label_anchor, fontsize=12, rotate=90)
        page.insert_text((50, 780), "Sendungsinformation - fuer Ihre Unterlagen.", fontsize=12, rotate=90)
        for x in (80, 220, 380, 550):
            page.draw_line((x, 70), (x, 350), width=0.5)
        if return_footer:
            page.insert_text((575, 340), "Retoure@GKP", fontsize=8, rotate=90)
    else:
        page.insert_text((80, 80), label_anchor, fontsize=12)
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
    assert render_label_preview(result.pdf, 103, 199).startswith(b"\x89PNG")


def test_rotated_return_label_is_trimmed_to_its_ruled_content():
    result = process_dhl_pdf(synthetic_dhl_pdf(label_anchor="DHL Retoure"))
    x0, y0, x1, y1 = result.crop
    assert x0 > 0
    assert y0 > 0
    assert x1 < 595
    assert y1 < 421
    assert render_label_preview(result.pdf, 103, 199).startswith(b"\x89PNG")


def test_return_footer_and_trailing_whitespace_are_removed():
    result = process_dhl_pdf(
        synthetic_dhl_pdf(label_anchor="DHL Retoure", return_footer=True)
    )
    _, _, x1, _ = result.crop
    assert x1 < 570


@pytest.mark.parametrize("width_mm,height_mm", [(100, 150), (103, 199), (102, 210)])
def test_output_pdf_uses_selected_media_size(width_mm, height_mm):
    label = process_dhl_pdf(synthetic_dhl_pdf())
    formatted = format_label_pdf(label.pdf, width_mm, height_mm)
    document = fitz.open(stream=formatted, filetype="pdf")
    try:
        page = document[0]
        assert page.rect.width == pytest.approx(width_mm / 25.4 * 72, abs=0.01)
        assert page.rect.height == pytest.approx(height_mm / 25.4 * 72, abs=0.01)
    finally:
        document.close()
