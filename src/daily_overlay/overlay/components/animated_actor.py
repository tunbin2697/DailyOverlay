from __future__ import annotations

import math
import random
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QMovie, QPainter, QPixmap

from daily_overlay.ai.schema import ComponentConfig, Palette, TimeShift
from daily_overlay.assets.manager import ensure_spotlight_asset_async
from daily_overlay.config import (
    MAX_AMBIENT_SPEED_PX_S,
    MAX_TRANSLATE_SPEED_PX_S,
    MOVIE_PLAYBACK_SPEED_PCT,
)
from daily_overlay.context.browser_bridge import BrowserContext
from daily_overlay.context.win32_watcher import WindowContext
from daily_overlay.context.zones import ZoneRect
from daily_overlay.overlay.cameo import OrganicCameoScheduler
from daily_overlay.overlay.components.base import (
    OverlayComponent,
    move_point_capped,
    move_towards_capped,
    path_crosses_center_or_far,
)


def _ease_in_out_cubic(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2


class AnimatedActorComponent(OverlayComponent):
    """Animates cached or Spotlight-fetched .webp/.gif/.png assets with 4 choreographies and Organic Cameo timing."""

    def __init__(
        self,
        config: ComponentConfig,
        palette: Palette,
        movie_speed_pct: int = MOVIE_PLAYBACK_SPEED_PCT,
        cameo_rest_sec: float | None = None,
    ) -> None:
        super().__init__(config, palette, movie_speed_pct=movie_speed_pct)
        self._x = 260.0
        self._y = 600.0
        self._facing = 1
        self._state = "sit"  # walk | sit | glide
        self._state_timer = 4.5
        self._time = 0.0
        self._initialized = False
        self._base_behavior = config.behavior or "float_drift"
        self._active_behavior = self._base_behavior

        # Smooth glide interpolation
        self._glide_start = (0.0, 0.0)
        self._glide_end = (0.0, 0.0)
        self._glide_t = 0.0
        self._glide_dur = 0.9
        self._perch: ZoneRect | None = None

        # Float drift / parade / relocation state
        self._drift_phase = random.uniform(0.0, math.tau)
        self._parade_dir = random.choice([-1, 1])
        self._parade_progress = 0.0
        self._parade_y: float | None = None
        self._perch_is_thumb = False
        self._active_zone: ZoneRect | None = None
        self._drift_relocating = False

        # Directional slide-fade & occlusion dimming
        self._slide_phase: str = "steady"  # steady | out | in
        self._slide_alpha: float = 1.0
        self._slide_dir: tuple[float, float] = (1.0, 0.0)
        self._occluded_alpha: float = 1.0

        # Organic Cameo Scheduler
        self.scheduler = OrganicCameoScheduler(
            config.presence,
            min_cooldown_override=cameo_rest_sec,
        )
        self._scroll_alpha = 1.0
        self._transit_alpha = 1.0
        self._zone_alpha = 1.0
        self._idle_relocate_timer = random.uniform(28.0, 52.0)
        self._preferred_zone_slot: int = 0
        self._companion_rx: float = random.uniform(0.22, 0.78)

        # Async Public Noto Animated WebP / local GIF loader
        self._movie: QMovie | None = None
        self._static_pix: QPixmap | None = None
        self._pending_asset_path: Path | None = None
        self._has_primary = False

        cached = ensure_spotlight_asset_async(config.style, on_ready=self._on_asset_downloaded)
        if cached is not None:
            self._has_primary = True
            self._load_asset(cached)
        else:
            fallback = ensure_spotlight_asset_async("sparkles", on_ready=self._on_fallback_downloaded)
            if fallback is not None:
                self._load_asset(fallback)

    def _on_asset_downloaded(self, path: Path) -> None:
        self._has_primary = True
        self._pending_asset_path = path

    def _on_fallback_downloaded(self, path: Path) -> None:
        if not self._has_primary:
            self._pending_asset_path = path

    def _load_asset(self, path: Path) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie = None
        self._static_pix = None

        if path.suffix.lower() in {".gif", ".webp"}:
            movie = QMovie(str(path))
            if movie.isValid():
                speed_pct = max(35, int(self._movie_speed_pct * random.uniform(0.9, 1.12)))
                movie.setSpeed(speed_pct)
                movie.start()
                fc = movie.frameCount()
                movie.jumpToFrame(random.randrange(fc) if fc > 1 else 0)
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
            return zones[self.scheduler.variation.corner_index % len(zones)]
        return None

    def _start_glide(self, target_x: float, target_y: float) -> None:
        self._glide_start = (self._x, self._y)
        self._glide_end = (target_x, target_y)
        self._glide_t = 0.0
        dist = math.hypot(target_x - self._x, target_y - self._y)
        self._glide_dur = max(1.1, (dist * 1.6) / MAX_TRANSLATE_SPEED_PX_S)
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
        behavior_override: str = "none",
        freq_mult: float = 1.0,
        zone_alphas: list[float] | None = None,
        container_rect: tuple[float, float, float, float] | None = None,
    ) -> None:
        dt = min(dt, 0.05)
        self._time += dt
        self._active_behavior = (
            behavior_override
            if behavior_override and behavior_override != "none"
            else self._base_behavior
        )

        if self._pending_asset_path is not None:
            path = self._pending_asset_path
            self._pending_asset_path = None
            self._load_asset(path)

        cursor_near = False
        if self.config.avoid_cursor and zones:
            z0 = zones[self.scheduler.variation.corner_index % len(zones)]
            cursor_near = z0.contains(ctx.cursor_pos[0], ctx.cursor_pos[1], padding=80.0)

        just_started = self.scheduler.update(
            dt=dt,
            idle_seconds=ctx.idle_seconds,
            cursor_near=cursor_near,
            freq_mult=freq_mult,
        )
        if just_started:
            self._initialized = False
            self._parade_progress = 0.0
            self._preferred_zone_slot = (
                self.scheduler.variation.corner_index % len(zones) if zones else 0
            )
            self._companion_rx = random.uniform(0.22, 0.78)
            self._idle_relocate_timer = random.uniform(28.0, 52.0)

        is_scrolling = bool(browser_ctx and browser_ctx.is_scrolling)
        target_scroll_alpha = 0.08 if (is_scrolling and self._active_behavior == "perch_hop") else 1.0
        self._scroll_alpha += (target_scroll_alpha - self._scroll_alpha) * min(1.0, dt * 10.0)

        if not zones:
            self._zone_alpha = max(0.0, self._zone_alpha - dt * 3.5)
            return

        self._zone_alpha = min(1.0, self._zone_alpha + dt * 2.5)
        if not self.scheduler.is_visible:
            return

        var = self.scheduler.variation
        speed_mult = self.config.speed * time_shift.speed * var.speed_jitter
        prev_zone = self._active_zone
        if self._slide_phase == "steady" and self._state != "glide":
            self._idle_relocate_timer -= dt
            if self._idle_relocate_timer <= 0.0:
                self._idle_relocate_timer = random.uniform(28.0, 54.0)
                self._companion_rx = random.uniform(0.22, 0.78)
                if len(zones) > 1 and self._active_behavior in (
                    "corner_companion",
                    "perch_hop",
                    "float_drift",
                ):
                    self._preferred_zone_slot = (
                        self._preferred_zone_slot + random.randint(1, len(zones) - 1)
                    ) % len(zones)
        zone_idx = self._preferred_zone_slot if self._preferred_zone_slot < len(zones) else 0
        zone_choice = zones[zone_idx]

        target_occ = (
            zone_alphas[zone_idx]
            if (zone_alphas and zone_idx < len(zone_alphas))
            else 1.0
        )
        self._occluded_alpha += (target_occ - self._occluded_alpha) * min(1.0, dt * 4.5)

        c_rect = container_rect if container_rect is not None else (0.0, 0.0, 1536.0, 864.0)
        dest_cx = zone_choice.x + zone_choice.w * 0.5
        dest_cy = zone_choice.y + zone_choice.h * 0.5

        if self._active_behavior == "corner_companion":
            inset = min(36.0, zone_choice.w * 0.25)
            raw_tx = zone_choice.x + zone_choice.w * self._companion_rx
            anchor_tx = (
                max(zone_choice.x + inset, min(zone_choice.right - inset, raw_tx))
                if zone_choice.w > inset * 2.0
                else dest_cx
            )
            anchor_ty = zone_choice.bottom - 14.0
        elif self._active_behavior == "perch_hop":
            anchor_tx = dest_cx
            anchor_ty = zone_choice.bottom - 4.0
        else:
            anchor_tx = dest_cx
            anchor_ty = dest_cy

        if (
            self._initialized
            and self._slide_phase == "steady"
            and not (self._active_behavior == "perch_hop" and self._perch_is_thumb)
            and path_crosses_center_or_far(
                self._x, self._y, anchor_tx, anchor_ty, c_rect, self.config.relocate_feel
            )
        ):
            cl, ct, cw, ch = c_rect
            dx_out = self._x - (cl + cw * 0.5)
            dy_out = self._y - (ct + ch * 0.5)
            d_out = math.hypot(dx_out, dy_out)
            self._slide_dir = (dx_out / d_out, dy_out / d_out) if d_out >= 1.0 else (1.0, 0.0)
            self._slide_phase = "out"

        if self._slide_phase == "out":
            self._x += self._slide_dir[0] * 75.0 * dt
            self._y += self._slide_dir[1] * 75.0 * dt
            self._slide_alpha = max(0.0, self._slide_alpha - dt * 2.2)
            if self._slide_alpha <= 0.01:
                cl, ct, cw, ch = c_rect
                dx_in = anchor_tx - (cl + cw * 0.5)
                dy_in = anchor_ty - (ct + ch * 0.5)
                d_in = math.hypot(dx_in, dy_in)
                ux, uy = (dx_in / d_in, dy_in / d_in) if d_in >= 1.0 else (1.0, 0.0)
                self._x = anchor_tx + ux * 42.0
                self._y = anchor_ty + uy * 42.0
                if self._active_behavior == "parade_pass":
                    self._parade_y = anchor_ty
                self._state = "sit"
                self._active_zone = zone_choice
                self._slide_phase = "in"
            return

        if self._slide_phase == "in":
            self._active_zone = zone_choice
            if abs(anchor_tx - self._x) > 1.0 and self._active_behavior != "corner_companion":
                self._facing = 1 if anchor_tx >= self._x else -1
            self._x, self._y = move_point_capped(
                self._x,
                self._y,
                anchor_tx,
                anchor_ty,
                dt,
                gain=2.4,
                max_speed=min(100.0, MAX_TRANSLATE_SPEED_PX_S),
            )
            self._slide_alpha = min(1.0, self._slide_alpha + dt * 1.8)
            if self._slide_alpha >= 0.995 and math.hypot(anchor_tx - self._x, anchor_ty - self._y) <= 2.0:
                self._slide_alpha = 1.0
                self._slide_phase = "steady"
            return

        self._active_zone = zone_choice
        target_transit = 1.0

        if self._active_behavior == "corner_companion":
            tx, ty = anchor_tx, anchor_ty
            if not self._initialized:
                self._x, self._y = tx, ty
                self._initialized = True
            else:
                if math.hypot(tx - self._x, ty - self._y) > 140.0:
                    target_transit = 0.72
                self._x, self._y = move_point_capped(
                    self._x, self._y, tx, ty, dt, gain=2.0, max_speed=MAX_TRANSLATE_SPEED_PX_S
                )
            self._transit_alpha += (target_transit - self._transit_alpha) * min(1.0, dt * 4.0)
            self._state = "sit"
            self._facing = 1
            return

        if self._active_behavior == "float_drift":
            cx_z = zone_choice.x + zone_choice.w * 0.5
            cy_z = zone_choice.y + zone_choice.h * 0.5
            rx = max(24.0, zone_choice.w * 0.36)
            ry = max(20.0, zone_choice.h * 0.34)
            omega = min(0.16 * speed_mult, (MAX_AMBIENT_SPEED_PX_S * 0.8) / max(32.0, rx + ry * 0.7))
            t = self._time * omega + self._drift_phase
            nx = cx_z + math.sin(t) * rx
            ny = cy_z + math.sin(t * 1.37 + 0.8) * ry
            if abs(nx - self._x) > 0.2:
                self._facing = 1 if nx >= self._x else -1
            if not self._initialized:
                self._x, self._y = nx, ny
                self._drift_relocating = False
                self._initialized = True
            else:
                if prev_zone is not None:
                    z_shift = math.hypot(
                        (zone_choice.x + zone_choice.w * 0.5) - (prev_zone.x + prev_zone.w * 0.5),
                        (zone_choice.y + zone_choice.h * 0.5) - (prev_zone.y + prev_zone.h * 0.5),
                    )
                    if z_shift > 80.0:
                        self._drift_relocating = True
                dist = math.hypot(nx - self._x, ny - self._y)
                if dist <= 12.0:
                    self._drift_relocating = False
                if self._drift_relocating and dist > 140.0:
                    target_transit = 0.72
                cap_spd = MAX_TRANSLATE_SPEED_PX_S if self._drift_relocating else MAX_AMBIENT_SPEED_PX_S
                self._x, self._y = move_point_capped(
                    self._x, self._y, nx, ny, dt, gain=1.8, max_speed=cap_spd
                )
            self._transit_alpha += (target_transit - self._transit_alpha) * min(1.0, dt * 4.0)
            self._state = "walk"
            return

        if self._active_behavior == "parade_pass":
            target_y = zone_choice.y + zone_choice.h * var.y_ratio
            if not self._initialized or self._parade_y is None:
                self._parade_dir = 1 if var.corner_index % 2 == 0 else -1
                self._facing = self._parade_dir
                self._parade_progress = 0.0
                self._parade_y = target_y
                self._y = target_y
                self._initialized = True
            else:
                if abs(target_y - self._parade_y) > 80.0:
                    target_transit = 0.72
                self._parade_y = move_towards_capped(
                    self._parade_y, target_y, dt, gain=2.0, max_speed=MAX_TRANSLATE_SPEED_PX_S
                )

            parade_speed = min(MAX_AMBIENT_SPEED_PX_S * 0.88, 15.0 * max(0.4, speed_mult))
            travel_dur = max(12.0, (zone_choice.w + 100.0) / max(6.0, parade_speed))
            self._parade_progress = min(1.0, self._parade_progress + dt / travel_dur)
            start_x = zone_choice.x - 50.0 if self._parade_dir > 0 else zone_choice.right + 50.0
            end_x = zone_choice.right + 50.0 if self._parade_dir > 0 else zone_choice.x - 50.0
            self._x = start_x + (end_x - start_x) * self._parade_progress
            self._y = self._parade_y + math.sin(self._time * 0.7) * 4.5
            self._transit_alpha += (target_transit - self._transit_alpha) * min(1.0, dt * 4.0)
            self._state = "walk"
            if self._parade_progress >= 1.0 and self.config.presence != "continuous":
                self.scheduler.force_cooldown()
                self._initialized = False
            elif self._parade_progress >= 1.0:
                self._initialized = False
            return

        # Behavior 4: perch_hop — anchors smoothly to web cards/thumbnails or safe zone edges
        has_thumbnails = bool(browser_ctx and browser_ctx.elements)
        if not self._initialized:
            self._perch = self._pick_perch(zones, browser_ctx)
            self._perch_is_thumb = has_thumbnails and self._perch is not None
            if self._perch:
                self._x = self._perch.x + self._perch.w * 0.5
                self._y = self._perch.y if has_thumbnails else self._perch.bottom - 4.0
            self._initialized = True

        if has_thumbnails and browser_ctx:
            if self._state != "glide":
                if not self._perch_is_thumb:
                    new_p = self._pick_perch(zones, browser_ctx)
                    if new_p is not None:
                        self._perch = new_p
                        self._perch_is_thumb = True
                        tx = new_p.x + new_p.w * random.uniform(0.25, 0.75)
                        if math.hypot(tx - self._x, new_p.y - self._y) > 120.0:
                            self._start_glide(tx, new_p.y)
                        else:
                            self._x, self._y = tx, new_p.y
                elif self._perch is not None:
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
            self._perch = zone_choice
            self._perch_is_thumb = False
            if self._state != "glide":
                target_y = zone_choice.bottom - 4.0
                outside_x = self._x < zone_choice.x + 18.0 or self._x > zone_choice.right - 18.0
                far_y = abs(target_y - self._y) > 75.0
                if outside_x or far_y:
                    if zone_choice.w < 80.0:
                        tx = zone_choice.x + zone_choice.w * 0.5
                    else:
                        tx = max(zone_choice.x + 36.0, min(zone_choice.right - 36.0, self._x))
                    self._start_glide(tx, target_y)
                else:
                    self._y = move_towards_capped(
                        self._y, target_y, dt, gain=2.2, max_speed=MAX_TRANSLATE_SPEED_PX_S
                    )

        if self._state == "glide":
            glide_dist = math.hypot(
                self._glide_end[0] - self._glide_start[0],
                self._glide_end[1] - self._glide_start[1],
            )
            if glide_dist > 140.0:
                target_transit = 0.72
            self._transit_alpha += (target_transit - self._transit_alpha) * min(1.0, dt * 4.0)
            self._glide_t += dt / self._glide_dur
            if self._glide_t >= 1.0:
                self._x, self._y = self._glide_end
                self._state = "sit"
                self._state_timer = random.uniform(7.0, 14.0)
            else:
                e = _ease_in_out_cubic(self._glide_t)
                arc = math.sin(e * math.pi) * min(22.0, glide_dist * 0.08)
                self._x = self._glide_start[0] + (self._glide_end[0] - self._glide_start[0]) * e
                self._y = self._glide_start[1] + (self._glide_end[1] - self._glide_start[1]) * e - arc
            return

        self._transit_alpha += (target_transit - self._transit_alpha) * min(1.0, dt * 4.0)

        self._state_timer -= dt
        if self._state_timer <= 0:
            if has_thumbnails and not is_scrolling and random.random() < 0.35:
                new_p = self._pick_perch(zones, browser_ctx)
                if new_p:
                    self._perch = new_p
                    tx = new_p.x + new_p.w * random.uniform(0.2, 0.8)
                    self._start_glide(tx, new_p.y)
                    return
            self._state = random.choices(["sit", "walk"], weights=[0.65, 0.35])[0]
            self._state_timer = random.uniform(7.0, 14.0)
            if self._state == "walk":
                self._facing = random.choice([-1, 1])

        if self._state == "walk" and self._perch is not None:
            walk_speed = min(MAX_AMBIENT_SPEED_PX_S, 14.0 * speed_mult)
            self._x += self._facing * walk_speed * dt
            left_bound = self._perch.x + 30.0
            right_bound = self._perch.right - 30.0
            if self._x <= left_bound:
                self._x = left_bound
                self._facing = 1
            elif self._x >= right_bound:
                self._x = right_bound
                self._facing = -1

    def paint(self, painter: QPainter, global_opacity: float) -> None:
        eff_opacity = (
            global_opacity
            * self.scheduler.alpha
            * self._scroll_alpha
            * self._transit_alpha
            * self._zone_alpha
            * self._slide_alpha
            * self._occluded_alpha
        )
        if eff_opacity <= 0.01:
            return

        pix = self._movie.currentPixmap() if self._movie else self._static_pix
        if pix is None or pix.isNull():
            return

        size_mult = self.config.size * self.scheduler.variation.size_jitter
        target_h = int(68 * size_mult)
        if self._active_zone is not None:
            max_fit = max(28, int(min(self._active_zone.w, self._active_zone.h) * 0.78))
            target_h = min(target_h, max_fit)
        scaled = pix.scaledToHeight(target_h, Qt.TransformationMode.SmoothTransformation)
        bob = (
            math.sin(self._time * 1.1) * 1.5
            if self._state == "walk"
            else math.sin(self._time * 0.7) * 1.0
        )
        painter.save()
        painter.setOpacity(min(1.0, eff_opacity))
        anchor_y = (
            self._y
            - scaled.height() * (0.46 if self._active_behavior != "float_drift" else 0.0)
            - bob
        )
        if not self._perch_is_thumb and self._active_zone is not None:
            z = self._active_zone
            half_sh = scaled.height() * 0.5
            if z.h >= scaled.height():
                anchor_y = max(z.y + half_sh, min(z.bottom - half_sh, anchor_y))
            else:
                anchor_y = z.y + z.h * 0.5
        painter.translate(self._x, anchor_y)
        if self._facing < 0 and self._active_behavior != "corner_companion":
            painter.scale(-1.0, 1.0)
        painter.drawPixmap(-scaled.width() // 2, -scaled.height() // 2, scaled)
        painter.restore()
