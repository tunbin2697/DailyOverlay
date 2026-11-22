from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath

from daily_overlay.ai.schema import ComponentConfig, Palette, TimeShift
from daily_overlay.context.win32_watcher import WindowContext
from daily_overlay.context.zones import ZoneRect
from daily_overlay.overlay.components.base import OverlayComponent


@dataclass
class _Fish:
    zone_idx: int
    x: float
    y: float
    vx: float
    vy: float
    scale: float
    phase: float
    color_idx: int


class BoidsFishComponent(OverlayComponent):
    """Calm flocking fish swimming naturally inside safe margin zones."""

    def __init__(self, config: ComponentConfig, palette: Palette) -> None:
        super().__init__(config, palette)
        self._fish: list[_Fish] = []
        self._zones: list[ZoneRect] = []
        self._time = 0.0

    def _ensure_school(self, zones: list[ZoneRect]) -> None:
        if not zones:
            self._fish.clear()
            return
        per_zone = max(3, int(round(3 + self.config.density * 5)))
        target_total = per_zone * len(zones)
        if len(self._fish) == target_total:
            return

        self._fish.clear()
        for zi, z in enumerate(zones):
            for i in range(per_zone):
                angle = random.uniform(0, math.tau)
                spd = random.uniform(22.0, 42.0)
                self._fish.append(
                    _Fish(
                        zone_idx=zi,
                        x=z.x + random.uniform(0.15, 0.85) * z.w,
                        y=z.y + random.uniform(0.15, 0.85) * z.h,
                        vx=math.cos(angle) * spd,
                        vy=math.sin(angle) * spd,
                        scale=random.uniform(0.75, 1.25) * self.config.size,
                        phase=random.uniform(0, math.tau),
                        color_idx=i % 3,
                    )
                )

    def update(
        self,
        dt: float,
        ctx: WindowContext,
        zones: list[ZoneRect],
        time_shift: TimeShift,
    ) -> None:
        self._zones = zones
        self._ensure_school(zones)
        if not self._fish:
            return

        dt = min(dt, 0.05)
        self._time += dt
        speed_mult = self.config.speed * time_shift.speed
        max_speed = 54.0 * speed_mult
        min_speed = 18.0 * speed_mult
        cx, cy = ctx.cursor_pos

        for f in self._fish:
            z = zones[f.zone_idx % len(zones)]
            sep_x = sep_y = 0.0
            ali_x = ali_y = 0.0
            coh_x = coh_y = 0.0
            neighbors = 0

            for other in self._fish:
                if other is f or other.zone_idx != f.zone_idx:
                    continue
                dx = other.x - f.x
                dy = other.y - f.y
                dist = math.hypot(dx, dy)
                if 0.01 < dist < 75.0:
                    neighbors += 1
                    ali_x += other.vx
                    ali_y += other.vy
                    coh_x += other.x
                    coh_y += other.y
                    if dist < 30.0:
                        sep_x -= dx / dist
                        sep_y -= dy / dist

            ax = ay = 0.0
            if neighbors > 0:
                ali_x /= neighbors
                ali_y /= neighbors
                coh_x = coh_x / neighbors - f.x
                coh_y = coh_y / neighbors - f.y
                ax += sep_x * 42.0 + (ali_x - f.vx) * 0.45 + coh_x * 0.35
                ay += sep_y * 42.0 + (ali_y - f.vy) * 0.45 + coh_y * 0.35

            # Soft gentle wander
            ax += math.cos(self._time * 0.7 + f.phase) * 9.0
            ay += math.sin(self._time * 0.6 + f.phase * 1.3) * 9.0

            # Soft boundary steering to keep fish comfortably inside the margin zone
            pad = 22.0
            turn = 85.0
            if f.x < z.x + pad:
                ax += turn * (1.0 - max(0.0, f.x - z.x) / pad)
            elif f.x > z.right - pad:
                ax -= turn * (1.0 - max(0.0, z.right - f.x) / pad)
            if f.y < z.y + pad:
                ay += turn * (1.0 - max(0.0, f.y - z.y) / pad)
            elif f.y > z.bottom - pad:
                ay -= turn * (1.0 - max(0.0, z.bottom - f.y) / pad)

            # Gentle cursor avoidance
            if self.config.avoid_cursor:
                cdx = f.x - cx
                cdy = f.y - cy
                cdist = math.hypot(cdx, cdy)
                if 0.1 < cdist < 110.0:
                    repel = (1.0 - cdist / 110.0) * 140.0
                    ax += (cdx / cdist) * repel
                    ay += (cdy / cdist) * repel

            f.vx += ax * dt
            f.vy += ay * dt
            spd = math.hypot(f.vx, f.vy)
            if spd > max_speed:
                f.vx = f.vx / spd * max_speed
                f.vy = f.vy / spd * max_speed
            elif spd < min_speed and spd > 0.01:
                f.vx = f.vx / spd * min_speed
                f.vy = f.vy / spd * min_speed

            f.x += f.vx * dt
            f.y += f.vy * dt

            # Hard clamp if window resized abruptly
            f.x = max(z.x + 4.0, min(z.right - 4.0, f.x))
            f.y = max(z.y + 4.0, min(z.bottom - 4.0, f.y))

    def paint(self, painter: QPainter, global_opacity: float) -> None:
        if not self._fish or global_opacity <= 0.01:
            return

        style = self.config.style.lower()
        colors = [self.primary, self.secondary, self.accent]

        for f in self._fish:
            angle = math.degrees(math.atan2(f.vy, f.vx))
            wiggle = math.sin(self._time * 6.0 + f.phase) * 3.8 * f.scale
            length = 16.0 * f.scale
            width = 5.2 * f.scale

            base_color = QColor(colors[f.color_idx])
            if style == "ink":
                base_color = base_color.darker(150)

            painter.save()
            painter.translate(f.x, f.y)
            painter.rotate(angle)

            # Optional soft glow for neon style
            if style == "neon":
                glow = QColor(base_color)
                glow.setAlphaF(min(1.0, global_opacity * 0.25))
                painter.setPen(QColor(0, 0, 0, 0))
                painter.setBrush(glow)
                painter.drawEllipse(QPointF(0.0, 0.0), length * 0.9, width * 1.6)

            # Smooth organic fish body + wiggling tail
            body = QPainterPath()
            body.moveTo(length * 0.65, 0.0)
            body.quadTo(length * 0.1, -width, -length * 0.55, wiggle * 0.4)
            body.lineTo(-length * 0.95, wiggle - width * 0.7)
            body.quadTo(-length * 0.72, wiggle * 0.5, -length * 0.95, wiggle + width * 0.7)
            body.lineTo(-length * 0.55, wiggle * 0.4)
            body.quadTo(length * 0.1, width, length * 0.65, 0.0)
            body.closeSubpath()

            fill = QColor(base_color)
            fill.setAlphaF(min(1.0, global_opacity * 0.92))
            painter.setPen(QColor(0, 0, 0, 0))
            painter.setBrush(fill)
            painter.drawPath(body)

            # Koi accent spot
            if style == "koi":
                spot = QColor(self.accent if f.color_idx != 2 else self.primary)
                spot.setAlphaF(min(1.0, global_opacity * 0.85))
                painter.setBrush(spot)
                painter.drawEllipse(QPointF(length * 0.12, 0.0), length * 0.22, width * 0.45)

            painter.restore()
