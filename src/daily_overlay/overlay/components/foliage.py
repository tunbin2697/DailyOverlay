from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen

from daily_overlay.ai.schema import ComponentConfig, Palette, TimeShift
from daily_overlay.context.win32_watcher import WindowContext
from daily_overlay.context.zones import ZoneRect
from daily_overlay.overlay.components.base import OverlayComponent


@dataclass
class _Stalk:
    zone_idx: int
    layer: int  # 0 = far background, 1 = foreground
    anchor_ratio: float
    height: float
    thickness: float
    lean: float
    phase: float
    segments: int


@dataclass
class _DriftingPetal:
    zone_idx: int
    x: float
    y: float
    vx: float
    vy: float
    rot: float
    size: float
    phase: float


class SwayingFoliageComponent(OverlayComponent):
    """Lush 2-layer parallax bamboo grove, sakura branches, fern, or willow."""

    def __init__(self, config: ComponentConfig, palette: Palette) -> None:
        super().__init__(config, palette)
        self._time = 0.0
        self._zones: list[ZoneRect] = []
        self._cursor = (0.0, 0.0)
        self._stalks: list[_Stalk] = []
        self._petals: list[_DriftingPetal] = []

    def _ensure_foliage(self, zones: list[ZoneRect]) -> None:
        if not zones:
            self._stalks.clear()
            self._petals.clear()
            return

        per_zone = max(6, int(round(6 + self.config.density * 8)))
        target = per_zone * len(zones)
        if len(self._stalks) == target:
            return

        rng = random.Random(42 + int(self.config.density * 100))
        self._stalks = []
        self._petals = []
        for zi, z in enumerate(zones):
            # Spread clusters naturally near outer edges if bottom_edge, or evenly in corners/margins
            for i in range(per_zone):
                layer = 0 if i < per_zone // 2 else 1
                if z.w > 600:
                    # Group into lush left & right edge groves so center stays clean
                    side = 0 if i % 2 == 0 else 1
                    local_r = rng.uniform(0.01, 0.22)
                    ratio = local_r if side == 0 else (1.0 - local_r)
                else:
                    ratio = (i + rng.uniform(0.15, 0.85)) / per_zone

                base_h = min(z.h * 0.92, rng.uniform(110.0, 210.0)) * self.config.size
                if layer == 0:
                    base_h *= 1.18

                self._stalks.append(
                    _Stalk(
                        zone_idx=zi,
                        layer=layer,
                        anchor_ratio=ratio,
                        height=max(85.0, base_h),
                        thickness=(4.2 if layer == 1 else 2.8) * self.config.size,
                        lean=rng.uniform(-22.0, 22.0),
                        phase=rng.uniform(0.0, math.tau),
                        segments=rng.randint(4, 7),
                    )
                )

            # Drifting petals/leaves for extra atmosphere
            for _ in range(5):
                self._petals.append(
                    _DriftingPetal(
                        zone_idx=zi,
                        x=z.x + rng.uniform(0.0, z.w),
                        y=z.y + rng.uniform(0.0, z.h),
                        vx=rng.uniform(-12.0, 18.0),
                        vy=rng.uniform(8.0, 20.0),
                        rot=rng.uniform(0.0, 360.0),
                        size=rng.uniform(3.5, 6.5) * self.config.size,
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
        self._zones = zones
        self._cursor = ctx.cursor_pos
        self._ensure_foliage(zones)
        spd = self.config.speed * time_shift.speed
        self._time += dt * spd

        for pt in self._petals:
            z = zones[pt.zone_idx % len(zones)]
            pt.x += (pt.vx + math.sin(self._time * 1.4 + pt.phase) * 14.0) * dt * spd
            pt.y += pt.vy * dt * spd
            pt.rot += 35.0 * dt
            if pt.y > z.bottom:
                pt.y = z.y
                pt.x = z.x + random.uniform(0.0, z.w)
            if pt.x < z.x:
                pt.x = z.right
            elif pt.x > z.right:
                pt.x = z.x

    @staticmethod
    def _draw_tapered_leaf(painter: QPainter, length: float, width: float) -> None:
        path = QPainterPath()
        path.moveTo(0.0, 0.0)
        path.quadTo(length * 0.45, -width, length, 0.0)
        path.quadTo(length * 0.45, width, 0.0, 0.0)
        path.closeSubpath()
        painter.drawPath(path)

    @staticmethod
    def _draw_sakura_blossom(painter: QPainter, radius: float, petal_col: QColor, center_col: QColor) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(petal_col)
        for k in range(5):
            painter.save()
            painter.rotate(k * 72.0)
            painter.drawEllipse(QRectF(radius * 0.15, -radius * 0.42, radius * 0.95, radius * 0.84))
            painter.restore()
        painter.setBrush(center_col)
        painter.drawEllipse(QPointF(0.0, 0.0), radius * 0.26, radius * 0.26)

    def paint(self, painter: QPainter, global_opacity: float) -> None:
        if not self._zones or not self._stalks or global_opacity <= 0.01:
            return

        style = self.config.style.lower()
        cx, cy = self._cursor

        for layer in (0, 1):
            layer_alpha = min(1.0, global_opacity * (0.42 if layer == 0 else 0.88))

            if style == "bamboo":
                stalk_base = QColor(self.primary).darker(135 if layer == 0 else 110)
                node_col = QColor(self.secondary).lighter(115)
                leaf_col = QColor(self.primary).lighter(115 if layer == 1 else 95)
            elif style == "sakura":
                stalk_base = QColor("#5D4037" if layer == 1 else "#795548")
                node_col = stalk_base
                leaf_col = QColor(self.secondary)
            else:
                stalk_base = QColor(self.primary).darker(120)
                node_col = QColor(self.secondary)
                leaf_col = QColor(self.primary).lighter(110)

            stalk_base.setAlphaF(layer_alpha)
            node_col.setAlphaF(min(1.0, layer_alpha * 0.95))
            leaf_col.setAlphaF(min(1.0, layer_alpha * 0.95))
            accent_col = QColor(self.accent)
            accent_col.setAlphaF(min(1.0, layer_alpha * 0.95))

            for st in self._stalks:
                if st.layer != layer:
                    continue
                z = self._zones[st.zone_idx % len(self._zones)]
                base_x = z.x + z.w * st.anchor_ratio
                base_y = z.bottom

                breeze = (
                    math.sin(self._time * 1.15 + st.phase) * 18.0
                    + math.cos(self._time * 0.65 + st.phase * 1.4) * 7.0
                )
                if layer == 0:
                    breeze *= 0.65

                if self.config.avoid_cursor:
                    dx = base_x - cx
                    dy = (base_y - st.height * 0.5) - cy
                    dist = math.hypot(dx, dy)
                    if 1.0 < dist < 140.0:
                        breeze += (dx / dist) * (1.0 - dist / 140.0) * 32.0

                tip_x = base_x + st.lean + breeze
                tip_y = base_y - st.height
                ctrl_x = base_x + (st.lean + breeze) * 0.42
                ctrl_y = base_y - st.height * 0.56

                path = QPainterPath()
                path.moveTo(base_x, base_y)
                path.quadTo(ctrl_x, ctrl_y, tip_x, tip_y)

                # Soft vertical gradient on stalk
                grad = QLinearGradient(base_x, base_y, tip_x, tip_y)
                c_bot = QColor(stalk_base)
                c_bot.setAlphaF(layer_alpha * 0.95)
                c_top = QColor(stalk_base).lighter(120)
                c_top.setAlphaF(layer_alpha * 0.7)
                grad.setColorAt(0.0, c_bot)
                grad.setColorAt(1.0, c_top)

                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(
                    QPen(grad, st.thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                )
                painter.drawPath(path)

                # Nodes + leaf clusters along each segment
                for seg in range(1, st.segments + 1):
                    t = seg / (st.segments + 0.15)
                    pt = path.pointAtPercent(t)
                    tangent_angle = -path.angleAtPercent(t)

                    if style == "bamboo":
                        # Draw crisp horizontal bamboo joint ring (đốt trúc)
                        painter.save()
                        painter.translate(pt)
                        painter.rotate(tangent_angle + 90.0)
                        painter.setPen(QPen(node_col, 1.5))
                        half_w = st.thickness * 0.85
                        painter.drawLine(QPointF(-half_w, 0.0), QPointF(half_w, 0.0))
                        painter.restore()

                        # Draw lush 3-leaf bamboo sprays on upper 65% of stalk
                        if t >= 0.35:
                            side = 1 if seg % 2 == 0 else -1
                            for k in (-1, 0, 1):
                                painter.save()
                                painter.translate(pt)
                                leaf_rot = side * (34.0 + k * 19.0) + math.sin(self._time * 2.1 + seg + k) * 6.0
                                if side < 0:
                                    leaf_rot += 180.0
                                painter.rotate(leaf_rot)
                                painter.setPen(Qt.PenStyle.NoPen)
                                painter.setBrush(leaf_col)
                                ll = (22.0 - abs(k) * 4.5) * self.config.size * (0.85 if layer == 0 else 1.0)
                                lw = 3.8 * self.config.size
                                self._draw_tapered_leaf(painter, ll, lw)
                                painter.restore()
                    elif style == "sakura":
                        if t >= 0.3:
                            side = 1 if seg % 2 == 0 else -1
                            painter.save()
                            painter.translate(pt.x() + side * 6.0, pt.y())
                            r = (7.5 if layer == 1 else 5.5) * self.config.size
                            self._draw_sakura_blossom(painter, r, leaf_col, accent_col)
                            painter.restore()
                    else:
                        # Willow / Fern pairs of tapered fronds
                        for side in (-1, 1):
                            painter.save()
                            painter.translate(pt)
                            ang = (28.0 if side > 0 else 152.0) + math.sin(self._time * 1.8 + seg) * 7.0
                            painter.rotate(ang)
                            painter.setPen(Qt.PenStyle.NoPen)
                            painter.setBrush(leaf_col)
                            self._draw_tapered_leaf(
                                painter,
                                18.0 * self.config.size * (1.15 - t * 0.35),
                                3.4 * self.config.size,
                            )
                            painter.restore()

        # Draw drifting petals/leaves
        petal_col = QColor(self.secondary if style == "sakura" else self.accent)
        petal_col.setAlphaF(min(1.0, global_opacity * 0.72))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(petal_col)
        for pt in self._petals:
            painter.save()
            painter.translate(pt.x, pt.y)
            painter.rotate(pt.rot)
            self._draw_tapered_leaf(painter, pt.size * 2.0, pt.size * 0.65)
            painter.restore()
