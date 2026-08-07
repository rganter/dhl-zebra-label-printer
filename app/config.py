from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class PrinterProfile:
    name: str
    host: str
    port: int = 9100
    dpi: int = 203
    label_width_mm: float = 100
    label_height_mm: float = 150

    @property
    def width_dots(self) -> int:
        return round(self.label_width_mm / 25.4 * self.dpi)

    @property
    def height_dots(self) -> int:
        return round(self.label_height_mm / 25.4 * self.dpi)


def load_printers(path: str | Path | None = None) -> list[PrinterProfile]:
    config_path = Path(path or os.getenv("PRINTER_CONFIG", "/config/printers.yml"))
    if not config_path.exists():
        local_example = Path(__file__).parents[1] / "config" / "printers.example.yml"
        config_path = local_example
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    printers = []
    for item in raw.get("printers", []):
        profile = PrinterProfile(**item)
        if profile.dpi not in (203, 300):
            raise ValueError(f"Ungültige DPI für {profile.name}: nur 203 oder 300")
        if not (1 <= profile.port <= 65535):
            raise ValueError(f"Ungültiger Port für {profile.name}")
        printers.append(profile)
    return printers

