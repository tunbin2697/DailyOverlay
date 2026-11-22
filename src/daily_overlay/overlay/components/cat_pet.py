from __future__ import annotations

import math
import random
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QMovie, QPainter, QPainterPath, QPen, QPixmap

from daily_overlay.ai.schema import ComponentConfig, Palette, TimeShift
from daily_overlay.assets.manager import ensure_spotlight_asset_async
from daily_overlay.context.browser_bridge import BrowserContext
from daily_overlay.context.win32_watcher import WindowContext
from daily_overlay.context.zones import ZoneRect
from daily_overlay.overlay.components.base import OverlayComponent


def _ease_in_out_cubic(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2


class AnimatedActorComponent(OverlayComponent):
    """Versatile 2D animated actor playing any public Noto Animated WebP (or local GIF/WebP, or vector cat fallback)
    with 4 AI-chosen choreographies: perch_hop, float_drift, corner_companion, and parade_pass."""

    def __init__(self, config: ComponentConfig, palette: Palette) -> None:
        super().__init__(config, palette)
        self._x = 260.0
        self._y = 600.0
        self._facing = 1
        self._state = "sit"  # walk | sit | sleep | glide
        self._state_timer = 4.5
        self._time = 0.0
        self._blink_timer = 3.2
        self._is_blinking = False
        self._initialized = False
        self._cursor = (0.0, 0.0)
        self._behavior = config.behavior or "float_drift"

        # Smooth glide interpolation
        self._glide_start = (0.0, 0.0)
        self._glide_end = (0.0, 0.0)
        self._glide_t = 0.0
        self._glide_dur = 0.9
        self._perch: ZoneRect | None = None

        # Float drift / parade state
        self._drift_phase = random.uniform(0.0, math.tau)
        self._parade_dir = random.choice([-1, 1])
        self._parade_progress = 0.0
        self._parade_pause = 0.0

        # Cameo / Peek-in lifecycle
        self._is_cameo_mode = config.presence == "cameo"
        self._cameo_visible = True
        self._cameo_timer = random.uniform(24.0, 38.0) if self._is_cameo_mode else 999999.0
        self._cameo_alpha = 0.0
        self._scroll_alpha = 1.0

        # Async Public Noto Animated WebP / local GIF loader
        self._movie: QMovie | None = None
        self._static_pix: QPixmap | None = None
        self._pending_asset_path: Path | None = None
        cached = ensure_spotlight_asset_async(config.style, on_ready=self._on_asset_downloaded)
        if cached is not None:
            self._load_asset(cached)

    def _on_asset_downloaded(self, path: Path) -> None:
        self._pending_asset_path = path

    def _load_asset(self, path: Path) -> None:
        if path.suffix.lower() in {".gif", ".webp"}:
            movie = QMovie(str(path))
            if movie.isValid():
                movie.start()
                self._movie = movie
                return
        pix = QPixmap(str(path))
        if not pix.isNull():
            self._static_pix = pix

    def _pick_perch(
        self,
        zones: list[ZoneRect],
        browser_ctx: BrowserContext | None,
    ) -> ZoneRect | None:
        if browser_ctx and browser_ctx.elements:
            candidates = browser_ctx.elements
            if len(candidates) > 1 and self._perch is not None:
                others = [
                    c
                    for c in candidates
                    if abs(c.x - self._perch.x) > 40 or abs(c.y - self._perch.y) > 40
                ]
                if others:
                    candidates = others
            return random.choice(candidates)
        if zones:
            return zones[0]
        return None

    def _start_glide(self, target_x: float, target_y: float) -> None:
        self._glide_start = (self._x, self._y)
        self._glide_end = (target_x, target_y)
        self._glide_t = 0.0
        dist = math.hypot(target_x - self._x, target_y - self._y)
        self._glide_dur = max(0.6, min(1.3, dist / 500.0))
        if abs(target_x - self._x) > 4:
            self._facing = 1 if target_x >= self._x else -1
        self._state = "glide"

    def update(
        self,
        dt: float,
        ctx: WindowContext,
        zones: list[ZoneRect],
        time_shift: TimeShift,
        browser_ctx: BrowserContext | None = None,
    ) -> None:
        dt = min(dt, 0.05)
        self._time += dt
        self._cursor = ctx.cursor_pos
        speed_mult = self.config.speed * time_shift.speed

        if self._pending_asset_path is not None:
            path = self._pending_asset_path
            self._pending_asset_path = None
            self._load_asset(path)

        # Cameo intermittent visit / rest cycle
        if self._is_cameo_mode and self._behavior != "parade_pass":
            self._cameo_timer -= dt
            if self._cameo_timer <= 0.0:
                self._cameo_visible = not self._cameo_visible
                if self._cameo_visible:
                    self._cameo_timer = random.uniform(22.0, 38.0)
                    self._initialized = False
                else:
                    self._cameo_timer = random.uniform(45.0, 80.0)

        target_cameo = 1.0 if self._cameo_visible else 0.0
        self._cameo_alpha += (target_cameo - self._cameo_alpha) * min(1.0, dt * 2.2)

        is_scrolling = bool(browser_ctx and browser_ctx.is_scrolling)
        target_scroll_alpha = 0.08 if (is_scrolling and self._behavior == "perch_hop") else 1.0
        self._scroll_alpha += (target_scroll_alpha - self._scroll_alpha) * min(1.0, dt * 10.0)

        if self._cameo_alpha <= 0.01 or not zones:
            return

        # Behavior 1: corner_companion — rests cozily in a corner with gentle bob
        if self._behavior == "corner_companion":
            z = zones[0]
            tx = z.x + min(72.0, z.w * 0.35)
            ty = z.bottom - 14.0
            if not self._initialized:
                self._x, self._y = tx, ty
                self._initialized = True
            else:
                self._x += (tx - self._x) * min(1.0, dt * 5.0)
                self._y += (ty - self._y) * min(1.0, dt * 5.0)
            self._state = "sit"
            self._facing = 1
            return

        # Behavior 2: float_drift — drifts lazily in a smooth Lissajous curve inside zone
        if self._behavior == "float_drift":
            z = zones[0]
            cx_z = z.x + z.w * 0.5
            cy_z = z.y + z.h * 0.5
            rx = max(24.0, z.w * 0.36)
            ry = max(20.0, z.h * 0.34)
            t = self._time * 0.42 * speed_mult + self._drift_phase
            nx = cx_z + math.sin(t) * rx
            ny = cy_z + math.sin(t * 1.37 + 0.8) * ry
            if abs(nx - self._x) > 0.2:
                self._facing = 1 if nx >= self._x else -1
            if not self._initialized:
                self._x, self._y = nx, ny
                self._initialized = True
            else:
                self._x += (nx - self._x) * min(1.0, dt * 4.5)
                self._y += (ny - self._y) * min(1.0, dt * 4.5)
            self._state = "walk"
            return

        # Behavior 3: parade_pass — cruises smoothly across the zone and rests between passes
        if self._behavior == "parade_pass":
            z = zones[0]
            if self._parade_pause > 0.0:
                self._parade_pause -= dt
                self._cameo_visible = False
                return
            self._cameo_visible = True
            if not self._initialized:
                self._parade_dir = random.choice([-1, 1])
                self._facing = self._parade_dir
                self._parade_progress = 0.0
                self._y = z.y + z.h * random.uniform(0.35, 0.82)
                self._initialized = True

            travel_dur = max(10.0, (z.w / 62.0) / max(0.35, speed_mult))
            self._parade_progress += dt / travel_dur
            if self._parade_progress >= 1.0:
                self._initialized = False
                self._parade_pause = random.uniform(22.0, 50.0) if self._is_cameo_mode else 6.0
                return
            start_x = z.x - 50.0 if self._parade_dir > 0 else z.right + 50.0
            end_x = z.right + 50.0 if self._parade_dir > 0 else z.x - 50.0
            self._x = start_x + (end_x - start_x) * self._parade_progress
            self._y = z.y + z.h * 0.65 + math.sin(self._time * 1.8) * 10.0
            self._state = "walk"
            return

        # Behavior 4: perch_hop — anchors smoothly to YouTube thumbnails or bottom edge
        self._blink_timer -= dt
        if self._blink_timer <= 0:
            self._is_blinking = not self._is_blinking
            self._blink_timer = 0.14 if self._is_blinking else random.uniform(2.8, 6.0)

        has_thumbnails = bool(browser_ctx and browser_ctx.elements)
        if not self._initialized:
            self._perch = self._pick_perch(zones, browser_ctx)
            if self._perch:
                self._x = self._perch.x + self._perch.w * 0.5
                self._y = self._perch.y if has_thumbnails else self._perch.bottom - 4.0
            self._initialized = True

        if has_thumbnails and browser_ctx:
            if self._state != "glide":
                if self._perch is not None:
                    best = min(
                        browser_ctx.elements,
                        key=lambda r: math.hypot(r.x - self._perch.x, r.y - self._perch.y),
                    )
                    if math.hypot(best.x - self._perch.x, best.y - self._perch.y) < 320.0:
                        self._perch = best
                        self._y += (best.y - self._y) * min(1.0, dt * 18.0)
                        self._x = max(best.x + 28.0, min(best.right - 28.0, self._x))
                    elif not is_scrolling:
                        new_p = self._pick_perch(zones, browser_ctx)
                        if new_p:
                            self._perch = new_p
                            self._start_glide(new_p.x + new_p.w * random.uniform(0.25, 0.75), new_p.y)
        else:
            z = zones[0]
            self._perch = z
            if self._state != "glide":
                target_y = z.bottom - 4.0
                self._y += (target_y - self._y) * min(1.0, dt * 8.0)
                self._x = max(z.x + 40.0, min(z.right - 40.0, self._x))

        if self._state == "glide":
            self._glide_t += dt / self._glide_dur
            if self._glide_t >= 1.0:
                self._x, self._y = self._glide_end
                self._state = "sit"
                self._state_timer = random.uniform(5.0, 10.0)
            else:
                e = _ease_in_out_cubic(self._glide_t)
                arc = math.sin(e * math.pi) * 44.0
                self._x = self._glide_start[0] + (self._glide_end[0] - self._glide_start[0]) * e
                self._y = self._glide_start[1] + (self._glide_end[1] - self._glide_start[1]) * e - arc
            return

        self._state_timer -= dt
        if self._state_timer <= 0:
            if has_thumbnails and not is_scrolling and random.random() < 0.4:
                new_p = self._pick_perch(zones, browser_ctx)
                if new_p:
                    self._perch = new_p
                    tx = new_p.x + new_p.w * random.uniform(0.2, 0.8)
                    self._start_glide(tx, new_p.y)
                    return
            self._state = random.choices(["sit", "walk", "sleep"], weights=[0.48, 0.30, 0.22])[0]
            self._state_timer = random.uniform(4.5, 9.0)
            if self._state == "walk":
                self._facing = random.choice([-1, 1])

        if self._state == "walk" and self._perch is not None:
            walk_speed = 42.0 * speed_mult
            self._x += self._facing * walk_speed * dt
            left_bound = self._perch.x + 30.0
            right_bound = self._perch.right - 30.0
            if self._x <= left_bound:
                self._x = left_bound
                self._facing = 1
            elif self._x >= right_bound:
                self._x = right_bound
                self._facing = -1

    def _cat_colors(self) -> tuple[QColor, QColor, QColor]:
        st = self.config.style.lower()
        if st == "tuxedo":
            return QColor("#2C313A"), QColor("#F4F6F7"), QColor("#F5B041")
        if st == "ginger":
            return QColor("#E67E22"), QColor("#FAD7A0"), QColor("#27AE60")
        if st == "shadow":
            return QColor("#1F2430"), QColor("#3B4252"), QColor("#F7DC6F")
        if st == "calico":
            return QColor("#FDFEFE"), QColor("#E59866"), QColor("#5DADE2")
        return self.primary, self.secondary, self.accent

    def paint(self, painter: QPainter, global_opacity: float) -> None:
        eff_opacity = global_opacity * self._cameo_alpha * self._scroll_alpha
        if eff_opacity <= 0.01:
            return

        if self._movie is not None or self._static_pix is not None:
            pix = self._movie.currentPixmap() if self._movie else self._static_pix
            if pix and not pix.isNull():
                target_h = int(68 * self.config.size)
                scaled = pix.scaledToHeight(target_h, Qt.TransformationMode.SmoothTransformation)
                bob = math.sin(self._time * 2.6) * 3.0 if self._state == "walk" else math.sin(self._time * 1.4) * 1.5
                painter.save()
                painter.setOpacity(min(1.0, eff_opacity))
                anchor_y = self._y - scaled.height() * (0.46 if self._behavior != "float_drift" else 0.0) - bob
                painter.translate(self._x, anchor_y)
                if self._facing < 0 and self._behavior != "corner_companion":
                    painter.scale(-1.0, 1.0)
                painter.drawPixmap(-scaled.width() // 2, -scaled.height() // 2, scaled)
                painter.restore()
                return

        # Built-in vector cat fallback (only used when style is calico/tuxedo/ginger/shadow or while asset downloads)
        body_col, patch_col, eye_col = self._cat_colors()
        s = self.config.size * 1.0
        alpha = min(1.0, eff_opacity * 0.96)
        body_col.setAlphaF(alpha)
        patch_col.setAlphaF(alpha)
        eye_col.setAlphaF(alpha)

        outline = QColor("#1A1D24")
        outline.setAlphaF(alpha * 0.85)
        pen = QPen(outline, 1.8)

        painter.save()
        painter.translate(self._x, self._y)
        painter.scale(self._facing * s, s)

        shadow = QColor(0, 0, 0, int(60 * alpha))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(QPointF(0.0, 0.0), 22.0, 4.5)

        tail_wag = math.sin(self._time * (4.0 if self._state == "walk" else 2.0)) * 8.0
        tail_path = QPainterPath()
        tail_path.moveTo(-16.0, -14.0)
        tail_path.cubicTo(-30.0, -22.0 + tail_wag, -32.0, -38.0, -22.0, -40.0 + tail_wag * 0.6)
        painter.setPen(QPen(body_col, 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(tail_path)

        painter.setPen(pen)
        painter.setBrush(body_col)
        if self._state == "sleep":
            painter.drawEllipse(QRectF(-20.0, -20.0, 40.0, 20.0))
        elif self._state == "sit":
            painter.drawEllipse(QRectF(-15.0, -28.0, 28.0, 28.0))
        else:
            bob = abs(math.sin(self._time * 8.0)) * 2.2 if self._state == "walk" else 0.0
            painter.drawRoundedRect(QRectF(-20.0, -24.0 - bob, 36.0, 18.0), 9.0, 9.0)
            leg_swing = math.sin(self._time * 8.0) * 4.5 if self._state == "walk" else 0.0
            painter.drawRoundedRect(QRectF(-15.0 + leg_swing, -9.0, 6.0, 9.0), 3.0, 3.0)
            painter.drawRoundedRect(QRectF(6.0 - leg_swing, -9.0, 6.0, 9.0), 3.0, 3.0)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(patch_col)
        painter.drawEllipse(QPointF(-2.0, -14.0), 8.0, 5.5)

        hx, hy = (12.0, -28.0) if self._state != "sleep" else (14.0, -13.0)
        if self._state == "sit":
            hx, hy = 3.0, -34.0

        painter.setPen(pen)
        painter.setBrush(body_col)
        left_ear = QPainterPath()
        left_ear.moveTo(hx - 10.0, hy - 6.0)
        left_ear.lineTo(hx - 12.0, hy - 20.0)
        left_ear.lineTo(hx - 1.0, hy - 11.0)
        left_ear.closeSubpath()
        painter.drawPath(left_ear)

        right_ear = QPainterPath()
        right_ear.moveTo(hx + 2.0, hy - 11.0)
        right_ear.lineTo(hx + 11.0, hy - 20.0)
        right_ear.lineTo(hx + 11.0, hy - 5.0)
        right_ear.closeSubpath()
        painter.drawPath(right_ear)

        painter.drawEllipse(QPointF(hx, hy), 13.0, 11.5)

        if self._state == "sleep" or self._is_blinking:
            painter.setPen(QPen(outline, 1.6))
            painter.drawArc(QRectF(hx - 7.0, hy - 2.0, 5.0, 4.0), 0, -180 * 16)
            painter.drawArc(QRectF(hx + 2.0, hy - 2.0, 5.0, 4.0), 0, -180 * 16)
        else:
            dx = (self._cursor[0] - self._x) * self._facing
            dy = self._cursor[1] - (self._y + hy)
            dist = max(1.0, math.hypot(dx, dy))
            look_x = (dx / dist) * 1.4
            look_y = (dy / dist) * 1.1
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(eye_col)
            painter.drawEllipse(QPointF(hx - 4.5, hy - 1.0), 3.1, 3.3)
            painter.drawEllipse(QPointF(hx + 4.5, hy - 1.0), 3.1, 3.3)
            pupil = QColor("#111318")
            pupil.setAlphaF(alpha)
            painter.setBrush(pupil)
            painter.drawEllipse(QPointF(hx - 4.5 + look_x, hy - 1.0 + look_y), 1.5, 2.0)
            painter.drawEllipse(QPointF(hx + 4.5 + look_x, hy - 1.0 + look_y), 1.5, 2.0)

        nose = QColor("#F1948A")
        nose.setAlphaF(alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(nose)
        painter.drawEllipse(QPointF(hx, hy + 3.2), 2.0, 1.4)

        if self._state == "sleep":
            z_off = (self._time * 12.0) % 18.0
            z_col = QColor(self.secondary)
            z_col.setAlphaF(alpha * (1.0 - z_off / 18.0))
            painter.setPen(QPen(z_col, 1.5))
            painter.drawText(QPointF(hx + 10.0, hy - 10.0 - z_off), "z")

        painter.restore()


# Backward-compatible alias
CatPetComponent = AnimatedActorComponent
