from PIL import Image, ImageDraw

from app.config import PrinterProfile
from app.printer import image_to_zpl


def test_profile_converts_mm_to_dots():
    profile = PrinterProfile("test", "127.0.0.1", dpi=203)
    assert profile.width_dots == 799
    assert profile.height_dots == 1199


def test_zpl_has_expected_graphic_size():
    image = Image.new("1", (16, 2), 1)
    ImageDraw.Draw(image).rectangle((0, 0, 7, 0), fill=0)
    zpl = image_to_zpl(image).decode("ascii")
    assert "^PW16" in zpl
    assert "^LL2" in zpl
    assert "^GFA,4,4,2,FF000000" in zpl

