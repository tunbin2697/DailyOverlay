from __future__ import annotations

import math

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen

from daily_overlay.ai.schema import ComponentConfig, Palette, TimeShift
from daily_overlay.context.win32_watcher import WindowContext
from daily_overlay.context.zones import ZoneRect
from daily_overlay.overlay.components.base import OverlayComponent


class AmbientWavesComponent(OverlayComponent):
    """Smooth multi-layer ambient waves (ocean, aurora, silk) along edge zones."""

    def __init__(self, config: ComponentConfig, palette: Palette) -> None:
        super().__init__(config, palette)
        self._phase = 0.0
        self._zones: list[ZoneRect] = []
        self._cursor = (0.0, 0.0)

    def update(
        self,
        dt: float,
        ctx: WindowContext,
        zones: list[ZoneRect],
        time_shift: TimeShift,
    ) -> None:
        self._zones = zones
        self._cursor = ctx.cursor_pos
        self._phase += dt * self.config.speed * time_shift.speed * 1.1

    def paint(self, painter: QPainter, global_opacity: float) -> None:
        if not self._zones or global_opacity <= 0.01:
            return

        style = self.config.style.lower()
        layers = 3 if self.config.density >= 0.45 else 2
        colors = [self.primary, self.secondary, self.accent]
        cx, cy = self._cursor

        for zone in self._zones:
            steps = max(24, int(zone.w / 32))
            is_top = zone.y < 10.0

            for idx in range(layers):
                base_color = QColor(colors[idx % len(colors)])
                layer_alpha = min(0.95, global_opacity * (0.52 + 0.18 * (layers - idx) / layers))
                amp = (12.0 + idx * 5.5) * self.config.size
                freq = (0.0045 + idx * 0.0018) * (1.2 if style == "silk" else 1.0)
                phase_shift = self._phase * (1.0 + idx * 0.27) + idx * 1.9
                baseline_ratio = 0.45 + idx * 0.16

                points: list[QPointF] = []
                for s in range(steps + 1):
                    px = zone.x + (zone.w * s / steps)
                    wave = (
                        math.sin(px * freq + phase_shift) * amp
                        + math.cos(px * freq * 0.55 - phase_shift * 0.7) * (amp * 0.45)
                    )

                    # Gently dip away from the mouse cursor so it never feels intrusive
                    if self.config.avoid_cursor:
                        dx = px - cx
                        dy = (zone.y + zone.h * 0.5) - cy
                        dist = math.hypot(dx, dy)
                        if dist < 140.0:
                            push = (1.0 - dist / 140.0) * 18.0
                            wave += -push if is_top else push

                    if is_top:
                        py = zone.y + zone.h * baseline_ratio + wave
                    else:
                        py = zone.bottom - zone.h * (1.0 - baseline_ratio) + wave
                    points.append(QPointF(px, py))

                path = QPainterPath()
                if style == "silk":
                    path.moveTo(points[0])
                    for p in points[1:]:
                        path.lineTo(p)
                    pen_color = QColor(base_color)
                    pen_color.setAlphaF(min(1.0, layer_alpha * 1.7))
                    painter.setBrush(QColor(0, 0, 0, 0))
                    painter.setPen(QPen(pen_color, 2.2 * self.config.size))
                    painter.drawPath(path)
                else:
                    if is_top:
                        path.moveTo(zone.x, zone.y)
                        for p in points:
                            path.lineTo(p)
                        path.lineTo(zone.right, zone.y)
                    else:
                        path.moveTo(zone.x, zone.bottom)
                        for p in points:
                            path.lineTo(p)
                        path.lineTo(zone.right, zone.bottom)
                    path.closeSubpath()

                    grad = QLinearGradient(
                        zone.x,
                        zone.y if is_top else zone.bottom,
                        zone.x,
                        zone.bottom if is_top else zone.y,
                    )
                    c_edge = QColor(base_color)
                    c_edge.setAlphaF(min(1.0, layer_alpha * (1.25 if style == "aurora" else 1.0)))
                    c_fade = QColor(base_color)
                    c_fade.setAlphaF(0.02)
                    grad.setColorAt(0.0, c_edge)
                    grad.setColorAt(1.0, c_fade)

                    painter.setPen(QColor(0, 0, 0, 0))
                    painter.setBrush(grad)
                    painter.drawPath(path)
