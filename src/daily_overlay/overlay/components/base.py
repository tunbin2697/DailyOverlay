from __future__ import annotations

from abc import ABC, abstractmethod

from PySide6.QtGui import QColor, QPainter

from daily_overlay.ai.schema import ComponentConfig, Palette, TimeShift
from daily_overlay.context.win32_watcher import WindowContext
from daily_overlay.context.zones import ZoneRect


def parse_hex_color(hex_str: str, fallback: str = "#7FB3D5") -> QColor:
    color = QColor(hex_str)
    if not color.isValid():
        color = QColor(fallback)
    return color


class OverlayComponent(ABC):
    def __init__(self, config: ComponentConfig, palette: Palette) -> None:
        self.config = config
        self.palette = palette
        self.primary = parse_hex_color(palette.primary, "#7FB3D5")
        self.secondary = parse_hex_color(palette.secondary, "#F5CBA7")
        self.accent = parse_hex_color(palette.accent, "#E59866")

    @abstractmethod
    def update(
        self,
        dt: float,
        ctx: WindowContext,
        zones: list[ZoneRect],
        time_shift: TimeShift,
    ) -> None:
        """Advance animation state."""

    @abstractmethod
    def paint(self, painter: QPainter, global_opacity: float) -> None:
        """Render the component onto the transparent overlay."""
