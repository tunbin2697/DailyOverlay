from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QRadialGradient

from daily_overlay.ai.schema import ComponentConfig, Palette, TimeShift
from daily_overlay.context.win32_watcher import WindowContext
from daily_overlay.context.zones import ZoneRect
from daily_overlay.overlay.components.base import OverlayComponent


@dataclass
class _Cloud:
    zone_idx: int
    x: float
    y: float
    vx: float
    radius_x: float
    radius_y: float
    puffs: list[tuple[float, float, float]]
    color_idx: int
    phase: float


class AmbientCloudsComponent(OverlayComponent):
    """Soft translucent drifting clouds, mist, sunset glow, or aurora haze."""

    def __init__(self, config: ComponentConfig, palette: Palette) -> None:
        super().__init__(config, palette)
        self._clouds: list[_Cloud] = []
        self._time = 0.0

    def _ensure_clouds(self, zones: list[ZoneRect]) -> None:
        if not zones:
            self._clouds.clear()
            return
        per_zone = max(3, int(round(3 + self.config.density * 4)))
        target = per_zone * len(zones)
        if len(self._clouds) == target:
            return

        rng = random.Random(101 + int(self.config.density * 50))
        self._clouds = []
        for zi, z in enumerate(zones):
            for i in range(per_zone):
                rx = rng.uniform(55.0, 115.0) * self.config.size
                ry = rng.uniform(22.0, 44.0) * self.config.size
                puffs = [
                    (rng.uniform(-0.65, 0.65) * rx, rng.uniform(-0.35, 0.35) * ry, rng.uniform(0.55, 1.0))
                    for _ in range(4)
                ]
                self._clouds.append(
                    _Cloud(
                        zone_idx=zi,
                        x=z.x + rng.uniform(0.0, z.w),
                        y=z.y + rng.uniform(0.22, 0.78) * z.h,
                        vx=rng.uniform(8.0, 22.0) * (1 if i % 2 == 0 else -0.65),
                        radius_x=rx,
                        radius_y=ry,
                        puffs=puffs,
                        color_idx=i % 3,
                        phase=rng.uniform(0.0, math.tau),
                    )
                )

    def update(
        self,
        dt: float,
        ctx: WindowContext,
        zones: list[ZoneRect],
        time_shift: TimeShift,
    ) -> None:
        dt = min(dt, 0.05)
        self._time += dt
        self._ensure_clouds(zones)
        spd = self.config.speed * time_shift.speed

        for cl in self._clouds:
            z = zones[cl.zone_idx % len(zones)]
            cl.x += cl.vx * dt * spd
            cl.y += math.sin(self._time * 0.5 + cl.phase) * 3.0 * dt
            if cl.x > z.right + cl.radius_x * 1.4:
                cl.x = z.x - cl.radius_x * 1.2
            elif cl.x < z.x - cl.radius_x * 1.4:
                cl.x = z.right + cl.radius_x * 1.2

    def paint(self, painter: QPainter, global_opacity: float) -> None:
        if not self._clouds or global_opacity <= 0.01:
            return

        style = self.config.style.lower()
        colors = [self.primary, self.secondary, self.accent]
        painter.setPen(Qt.PenStyle.NoPen)

        for cl in self._clouds:
            base = QColor(colors[cl.color_idx])
            if style == "cumulus":
                base = base.lighter(135)
            elif style == "mist":
                base = base.lighter(120)

            alpha = min(0.62, global_opacity * (0.34 if style == "mist" else 0.44))

            for dx, dy, scale in cl.puffs:
                px = cl.x + dx
                py = cl.y + dy
                r = max(cl.radius_x, cl.radius_y) * 0.65 * scale
                grad = QRadialGradient(px, py, r)
                c_in = QColor(base)
                c_in.setAlphaF(alpha)
                c_out = QColor(base)
                c_out.setAlphaF(0.0)
                grad.setColorAt(0.0, c_in)
                grad.setColorAt(1.0, c_out)
                painter.setBrush(grad)
                painter.drawEllipse(QPointF(px, py), r, r * 0.62)
