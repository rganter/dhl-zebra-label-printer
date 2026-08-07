from __future__ import annotations

import socket

from PIL import Image

from .config import PrinterProfile
from .label_processor import render_for_printer


def image_to_zpl(image: Image.Image) -> bytes:
    mono = image.convert("1")
    width, height = mono.size
    row_bytes = (width + 7) // 8
    packed = bytearray()
    pixels = mono.load()
    for y in range(height):
        for byte_x in range(row_bytes):
            value = 0
            for bit in range(8):
                x = byte_x * 8 + bit
                if x < width and pixels[x, y] == 0:
                    value |= 1 << (7 - bit)
            packed.append(value)
    hex_data = packed.hex().upper()
    total = len(packed)
    return (
        f"^XA\n^PW{width}\n^LL{height}\n^LH0,0\n^FO0,0\n"
        f"^GFA,{total},{total},{row_bytes},{hex_data}\n^FS\n^XZ\n"
    ).encode("ascii")


def build_zpl(pdf: bytes, profile: PrinterProfile) -> bytes:
    image = render_for_printer(pdf, profile.width_dots, profile.height_dots)
    return image_to_zpl(image)


def print_label(pdf: bytes, profile: PrinterProfile, timeout: float = 10.0) -> None:
    payload = build_zpl(pdf, profile)
    with socket.create_connection((profile.host, profile.port), timeout=timeout) as connection:
        connection.sendall(payload)

