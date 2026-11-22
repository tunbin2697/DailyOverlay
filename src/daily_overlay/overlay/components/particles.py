from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QRadialGradient

from daily_overlay.ai.schema import ComponentConfig, Palette, TimeShift
from daily_overlay.context.win32_watcher import WindowContext
from daily_overlay.context.zones import ZoneRect
from daily_overlay.overlay.components.base import OverlayComponent


@dataclass
class _Particle:
    zone_idx: int
    x: float
    y: float
    vx: float
    vy: float
    radius: float
    phase: float
    color_idx: int


@dataclass
class _Ripple:
    x: float
    y: float
    age: float
    max_age: float


@dataclass
class _ShootingStar:
    x: float
    y: float
    vx: float
    vy: float
    age: float
    max_age: float


class FloatingParticlesComponent(OverlayComponent):
    """Ambient particles: fireflies, bubbles, petals, snow, dust, rain (with ripples), or stars (with shooting stars)."""

    def __init__(self, config: ComponentConfig, palette: Palette) -> None:
        super().__init__(config, palette)
        self._particles: list[_Particle] = []
        self._ripples: list[_Ripple] = []
        self._shooting_star: _ShootingStar | None = None
        self._next_meteor = random.uniform(4.0, 9.0)
        self._time = 0.0

    def _spawn_in_zone(self, zi: int, z: ZoneRect, color_idx: int) -> _Particle:
        style = self.config.style.lower()
        if style == "bubbles":
            vx = random.uniform(-6.0, 6.0)
            vy = random.uniform(-24.0, -10.0)
            r = random.uniform(3.2, 8.5) * self.config.size
        elif style == "rain":
            vx = random.uniform(-26.0, -16.0)
            vy = random.uniform(210.0, 320.0)
            r = random.uniform(10.0, 18.0) * self.config.size
        elif style == "stars":
            vx = random.uniform(-1.5, 1.5)
            vy = random.uniform(-1.5, 1.5)
            r = random.uniform(2.0, 5.0) * self.config.size
        elif style in ("petals", "snow"):
            vx = random.uniform(-10.0, 14.0)
            vy = random.uniform(10.0, 24.0)
            r = random.uniform(2.8, 5.8) * self.config.size
        else:
            vx = random.uniform(-8.0, 8.0)
            vy = random.uniform(-8.0, 8.0)
            r = random.uniform(2.4, 5.0) * self.config.size

        return _Particle(
            zone_idx=zi,
            x=z.x + random.uniform(0.05, 0.95) * z.w,
            y=z.y + random.uniform(0.05, 0.95) * z.h,
            vx=vx,
            vy=vy,
            radius=r,
            phase=random.uniform(0.0, math.tau),
            color_idx=color_idx,
        )

    def _ensure_particles(self, zones: list[ZoneRect]) -> None:
        if not zones:
            self._particles.clear()
            return
        style = self.config.style.lower()
        base_mult = 22 if style == "rain" else 16
        per_zone = max(8, int(round(7 + self.config.density * base_mult)))
        target = per_zone * len(zones)
        if len(self._particles) == target:
            return

        self._particles = [
            self._spawn_in_zone(zi, z, i % 3)
            for zi, z in enumerate(zones)
            for i in range(per_zone)
        ]

    def update(
        self,
        dt: float,
        ctx: WindowContext,
        zones: list[ZoneRect],
        time_shift: TimeShift,
    ) -> None:
        self._ensure_particles(zones)
        if not self._particles:
            return

        dt = min(dt, 0.05)
        self._time += dt
        speed_mult = self.config.speed * time_shift.speed
        style = self.config.style.lower()
        cx, cy = ctx.cursor_pos

        for p in self._particles:
            z = zones[p.zone_idx % len(zones)]
            if style == "rain":
                vx = p.vx * speed_mult
                vy = p.vy * speed_mult
            elif style == "stars":
                vx = p.vx * speed_mult
                vy = p.vy * speed_mult
            else:
                drift_x = math.sin(self._time * 0.9 + p.phase) * 7.0
                drift_y = math.cos(self._time * 0.7 + p.phase) * 5.0
                vx = (p.vx + drift_x) * speed_mult
                vy = (p.vy + drift_y) * speed_mult

            if self.config.avoid_cursor and style not in ("rain", "stars"):
                dx = p.x - cx
                dy = p.y - cy
                dist = math.hypot(dx, dy)
                if 0.1 < dist < 95.0:
                    push = (1.0 - dist / 95.0) * 65.0
                    vx += (dx / dist) * push
                    vy += (dy / dist) * push

            p.x += vx * dt
            p.y += vy * dt

            if p.x < z.x:
                p.x = z.right
            elif p.x > z.right:
                p.x = z.x

            if p.y < z.y:
                p.y = z.bottom
            elif p.y > z.bottom:
                if style == "rain" and len(self._ripples) < 16 and random.random() < 0.28:
                    self._ripples.append(_Ripple(x=p.x, y=z.bottom - 3.0, age=0.0, max_age=0.65))
                p.y = z.y
                if style == "rain":
                    p.x = z.x + random.uniform(0.0, z.w)

        # Update rain splash ripples
        if self._ripples:
            for rp in self._ripples:
                rp.age += dt
            self._ripples = [rp for rp in self._ripples if rp.age < rp.max_age]

        # Occasional shooting star for "stars" style
        if style == "stars" and zones:
            if self._shooting_star is not None:
                self._shooting_star.age += dt
                self._shooting_star.x += self._shooting_star.vx * dt
                self._shooting_star.y += self._shooting_star.vy * dt
                if self._shooting_star.age >= self._shooting_star.max_age:
                    self._shooting_star = None
                    self._next_meteor = random.uniform(6.0, 14.0)
            else:
                self._next_meteor -= dt
                if self._next_meteor <= 0.0:
                    z = random.choice(zones)
                    self._shooting_star = _ShootingStar(
                        x=z.x + random.uniform(0.2, 0.9) * z.w,
                        y=z.y + random.uniform(0.05, 0.35) * z.h,
                        vx=-random.uniform(220.0, 340.0),
                        vy=random.uniform(90.0, 160.0),
                        age=0.0,
                        max_age=0.75,
                    )

    def paint(self, painter: QPainter, global_opacity: float) -> None:
        if not self._particles or global_opacity <= 0.01:
            return

        style = self.config.style.lower()
        colors = [self.primary, self.secondary, self.accent]

        if style == "rain":
            for p in self._particles:
                col = QColor(colors[p.color_idx])
                col.setAlphaF(min(1.0, global_opacity * 0.58))
                painter.setPen(QPen(col, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(QPointF(p.x, p.y), QPointF(p.x + p.vx * 0.05, p.y + p.radius))

            for rp in self._ripples:
                t = rp.age / rp.max_age
                col = QColor(self.secondary)
                col.setAlphaF(min(1.0, global_opacity * (1.0 - t) * 0.65))
                painter.setPen(QPen(col, 1.1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(rp.x, rp.y), 4.0 + t * 14.0, 1.5 + t * 4.5)
            return

        for p in self._particles:
            base = QColor(colors[p.color_idx])
            pulse = 0.62 + 0.38 * math.sin(self._time * (2.6 if style == "stars" else 1.8) + p.phase)
            alpha = min(1.0, global_opacity * pulse * 0.96)

            if style == "bubbles":
                stroke = QColor(base)
                stroke.setAlphaF(alpha)
                fill = QColor(base)
                fill.setAlphaF(alpha * 0.2)
                painter.setPen(QPen(stroke, 1.3))
                painter.setBrush(fill)
                painter.drawEllipse(QPointF(p.x, p.y), p.radius, p.radius)
            elif style == "stars":
                c = QColor(base).lighter(125)
                c.setAlphaF(alpha)
                painter.setPen(QPen(c, 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                r = p.radius * (0.8 + 0.45 * pulse)
                painter.drawLine(QPointF(p.x - r, p.y), QPointF(p.x + r, p.y))
                painter.drawLine(QPointF(p.x, p.y - r), QPointF(p.x, p.y + r))
            elif style == "petals":
                painter.save()
                painter.translate(p.x, p.y)
                painter.rotate(math.degrees(self._time * 0.8 + p.phase))
                fill = QColor(base)
                fill.setAlphaF(alpha * 0.9)
                painter.setPen(QColor(0, 0, 0, 0))
                painter.setBrush(fill)
                painter.drawEllipse(QRectF(-p.radius, -p.radius * 0.55, p.radius * 2, p.radius * 1.1))
                painter.restore()
            else:
                glow_r = p.radius * (2.6 if style == "fireflies" else 1.7)
                grad = QRadialGradient(p.x, p.y, glow_r)
                c_core = QColor(base)
                c_core.setAlphaF(alpha)
                c_outer = QColor(base)
                c_outer.setAlphaF(0.0)
                grad.setColorAt(0.0, c_core)
                grad.setColorAt(1.0, c_outer)
                painter.setPen(QColor(0, 0, 0, 0))
                painter.setBrush(grad)
                painter.drawEllipse(QPointF(p.x, p.y), glow_r, glow_r)

        if self._shooting_star is not None:
            st = self._shooting_star
            fade = math.sin((st.age / st.max_age) * math.pi)
            tail_x = st.x - st.vx * 0.18
            tail_y = st.y - st.vy * 0.18
            grad = QLinearGradient(st.x, st.y, tail_x, tail_y)
            c_head = QColor(self.accent).lighter(145)
            c_head.setAlphaF(min(1.0, global_opacity * fade))
            c_tail = QColor(self.accent)
            c_tail.setAlphaF(0.0)
            grad.setColorAt(0.0, c_head)
            grad.setColorAt(1.0, c_tail)
            painter.setPen(QPen(grad, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(st.x, st.y), QPointF(tail_x, tail_y))
