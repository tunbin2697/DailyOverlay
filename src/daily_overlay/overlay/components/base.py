from __future__ import annotations

import math
from abc import ABC, abstractmethod

from PySide6.QtGui import QColor, QPainter

from daily_overlay.ai.schema import ComponentConfig, Palette, TimeShift
from daily_overlay.config import MAX_TRANSLATE_SPEED_PX_S, MOVIE_PLAYBACK_SPEED_PCT
from daily_overlay.context.win32_watcher import WindowContext
from daily_overlay.context.zones import ZoneRect


def parse_hex_color(hex_str: str, fallback: str = "#7FB3D5") -> QColor:
    color = QColor(hex_str)
    if not color.isValid():
        color = QColor(fallback)
    return color


def move_towards_capped(
    curr: float,
    target: float,
    dt: float,
    gain: float = 2.2,
    max_speed: float = MAX_TRANSLATE_SPEED_PX_S,
) -> float:
    delta = target - curr
    if abs(delta) <= 0.25:
        return target
    step = delta * min(1.0, dt * gain)
    max_step = max_speed * dt
    if step > max_step:
        step = max_step
    elif step < -max_step:
        step = -max_step
    return curr + step


def move_point_capped(
    x: float,
    y: float,
    tx: float,
    ty: float,
    dt: float,
    gain: float = 2.2,
    max_speed: float = MAX_TRANSLATE_SPEED_PX_S,
) -> tuple[float, float]:
    dx = tx - x
    dy = ty - y
    dist = math.hypot(dx, dy)
    if dist <= 0.35:
        return tx, ty
    step = min(dist * min(1.0, dt * gain), max_speed * dt)
    ratio = step / dist
    return x + dx * ratio, y + dy * ratio


def path_crosses_center_or_far(
    ox: float,
    oy: float,
    nx: float,
    ny: float,
    container_rect: tuple[float, float, float, float],
    relocate_feel: str = "auto",
) -> bool:
    cl, ct, cw, ch = container_rect
    cw = max(100.0, cw)
    ch = max(100.0, ch)
    dist = math.hypot(nx - ox, ny - oy)
    if relocate_feel == "dissolve" and dist > 90.0:
        return True
    if dist > max(260.0, cw * 0.28):
        return True
    cx0, cx1 = cl + cw * 0.22, cl + cw * 0.78
    cy0, cy1 = ct + ch * 0.16, ct + ch * 0.84
    for frac in (0.15, 0.28, 0.40, 0.50, 0.60, 0.72, 0.85):
        px = ox + (nx - ox) * frac
        py = oy + (ny - oy) * frac
        if cx0 <= px <= cx1 and cy0 <= py <= cy1:
            return True
    return False


def _outward_dir(cx: float, cy: float, container_rect: tuple[float, float, float, float]) -> tuple[float, float]:
    cl, ct, cw, ch = container_rect
    cont_cx = cl + cw * 0.5
    cont_cy = ct + ch * 0.5
    dx = cx - cont_cx
    dy = cy - cont_cy
    d = math.hypot(dx, dy)
    if d < 1.0:
        return 1.0, 0.0
    return dx / d, dy / d


class ZoneTransition:
    """Geometry-aware zone transition: pure translation on same-side shifts, directional Slide-Fade on cross-center/far jumps."""

    def __init__(
        self,
        max_speed: float = MAX_TRANSLATE_SPEED_PX_S,
        gain: float = 2.2,
        relocate_feel: str = "auto",
    ) -> None:
        self.max_speed = max_speed
        self.gain = gain
        self.relocate_feel = relocate_feel
        self.active_zones: list[ZoneRect] = []
        self.last_deltas: list[tuple[float, float]] = []
        self.alpha: float = 1.0
        self.just_initialized: bool = False
        self.just_jumped: bool = False
        self._phase: str = "steady"  # steady | slide_out | slide_in
        self._pending_targets: list[ZoneRect] | None = None
        self._exit_dirs: list[tuple[float, float]] = []

    @staticmethod
    def _pair_zones(curr: list[ZoneRect], targets: list[ZoneRect]) -> list[ZoneRect]:
        k = len(curr)
        matched: list[ZoneRect] = [targets[0]] * k
        pairs = sorted(
            (
                math.hypot(
                    (t.x + t.w * 0.5) - (c.x + c.w * 0.5),
                    (t.y + t.h * 0.5) - (c.y + c.h * 0.5),
                ),
                ci,
                ti,
            )
            for ci, c in enumerate(curr)
            for ti, t in enumerate(targets)
        )
        used_c: set[int] = set()
        used_t: set[int] = set()
        for _d, ci, ti in pairs:
            if ci in used_c or ti in used_t:
                continue
            used_c.add(ci)
            used_t.add(ti)
            matched[ci] = targets[ti]
        return matched

    @staticmethod
    def _reconcile_count(curr: list[ZoneRect], targets: list[ZoneRect]) -> list[ZoneRect]:
        res = list(curr)
        if len(res) < len(targets):
            while len(res) < len(targets):
                tz = targets[len(res)]
                tx, ty = tz.x + tz.w * 0.5, tz.y + tz.h * 0.5
                nearest = min(
                    res,
                    key=lambda c: math.hypot((c.x + c.w * 0.5) - tx, (c.y + c.h * 0.5) - ty),
                )
                res.append(nearest)
        elif len(res) > len(targets):
            remaining_c = list(res)
            kept: list[ZoneRect] = []
            for tz in targets:
                tx, ty = tz.x + tz.w * 0.5, tz.y + tz.h * 0.5
                best_idx = min(
                    range(len(remaining_c)),
                    key=lambda i: math.hypot(
                        (remaining_c[i].x + remaining_c[i].w * 0.5) - tx,
                        (remaining_c[i].y + remaining_c[i].h * 0.5) - ty,
                    ),
                )
                kept.append(remaining_c.pop(best_idx))
            res = kept
        return res

    def update(
        self,
        dt: float,
        target_zones: list[ZoneRect],
        container_rect: tuple[float, float, float, float] | None = None,
    ) -> list[ZoneRect]:
        dt = min(max(dt, 0.0), 0.05)
        self.just_initialized = False
        self.just_jumped = False
        c_rect = container_rect if container_rect is not None else (0.0, 0.0, 1536.0, 864.0)

        if not target_zones:
            self._pending_targets = None
            if self.active_zones:
                self._phase = "slide_out"
                self.alpha = max(0.0, self.alpha - dt * 2.2)
                if self.alpha <= 0.01:
                    self.active_zones = []
                    self.last_deltas = []
                    self.alpha = 0.0
                    self._phase = "steady"
                else:
                    self.last_deltas = [(0.0, 0.0)] * len(self.active_zones)
            else:
                self.alpha = 0.0
                self.last_deltas = []
                self._phase = "steady"
            return self.active_zones

        if not self.active_zones:
            self.active_zones = list(target_zones)
            self.last_deltas = [(0.0, 0.0)] * len(target_zones)
            self._pending_targets = None
            self.alpha = min(1.0, dt * 1.8)
            self._phase = "slide_in"
            self.just_initialized = True
            return self.active_zones

        if self._phase == "slide_out":
            self._pending_targets = list(target_zones)
            step = 100.0 * dt
            next_zones: list[ZoneRect] = []
            deltas: list[tuple[float, float]] = []
            for i, z in enumerate(self.active_zones):
                ux, uy = self._exit_dirs[i] if i < len(self._exit_dirs) else (1.0, 0.0)
                dx, dy = ux * step, uy * step
                next_zones.append(ZoneRect(z.x + dx, z.y + dy, z.w, z.h))
                deltas.append((dx, dy))
            self.active_zones = next_zones
            self.last_deltas = deltas
            self.alpha = max(0.0, self.alpha - dt * 2.0)

            if self.alpha <= 0.01:
                targets = self._pending_targets or list(target_zones)
                jumped: list[ZoneRect] = []
                for tz in targets:
                    ux, uy = _outward_dir(tz.x + tz.w * 0.5, tz.y + tz.h * 0.5, c_rect)
                    jumped.append(ZoneRect(tz.x + ux * 45.0, tz.y + uy * 45.0, tz.w, tz.h))
                self.active_zones = jumped
                self.last_deltas = [(0.0, 0.0)] * len(jumped)
                self._pending_targets = None
                self.alpha = 0.0
                self.just_jumped = True
                self._phase = "slide_in"
            return self.active_zones

        if self._phase == "steady":
            curr_check = self._reconcile_count(self.active_zones, target_zones)
            matched_check = self._pair_zones(curr_check, target_zones)
            should_slide_fade = (
                self.relocate_feel == "dissolve" and len(self.active_zones) != len(target_zones)
            ) or any(
                path_crosses_center_or_far(
                    c.x + c.w * 0.5,
                    c.y + c.h * 0.5,
                    t.x + t.w * 0.5,
                    t.y + t.h * 0.5,
                    c_rect,
                    self.relocate_feel,
                )
                for c, t in zip(curr_check, matched_check)
            )
            if should_slide_fade:
                self._exit_dirs = [
                    _outward_dir(c.x + c.w * 0.5, c.y + c.h * 0.5, c_rect)
                    for c in self.active_zones
                ]
                self._pending_targets = list(target_zones)
                self._phase = "slide_out"
                self.last_deltas = [(0.0, 0.0)] * len(self.active_zones)
                return self.active_zones

        # Either steady small translation or active slide_in
        curr = self._reconcile_count(self.active_zones, target_zones)
        matched_targets = self._pair_zones(curr, target_zones)

        if self._phase == "slide_in":
            self.alpha = min(1.0, self.alpha + dt * 1.7)
            step_speed = min(110.0, self.max_speed)
            step_gain = 2.6
        else:
            self.alpha = 1.0
            step_speed = self.max_speed
            step_gain = self.gain

        next_zones = []
        deltas = []
        for c, t in zip(curr, matched_targets):
            old_cx = c.x + c.w * 0.5
            old_cy = c.y + c.h * 0.5
            t_cx = t.x + t.w * 0.5
            t_cy = t.y + t.h * 0.5
            new_cx, new_cy = move_point_capped(
                old_cx, old_cy, t_cx, t_cy, dt, gain=step_gain, max_speed=step_speed
            )
            new_w = move_towards_capped(c.w, t.w, dt, gain=step_gain, max_speed=step_speed)
            new_h = move_towards_capped(c.h, t.h, dt, gain=step_gain, max_speed=step_speed)
            next_zones.append(
                ZoneRect(
                    x=new_cx - new_w * 0.5,
                    y=new_cy - new_h * 0.5,
                    w=new_w,
                    h=new_h,
                )
            )
            deltas.append((new_cx - old_cx, new_cy - old_cy))

        self.active_zones = next_zones
        self.last_deltas = deltas
        if self._phase == "slide_in" and self.alpha >= 0.995:
            self.alpha = 1.0
            self._phase = "steady"
        return self.active_zones


class OverlayComponent(ABC):
    def __init__(
        self,
        config: ComponentConfig,
        palette: Palette,
        movie_speed_pct: int = MOVIE_PLAYBACK_SPEED_PCT,
    ) -> None:
        self.config = config
        self.palette = palette
        self._movie_speed_pct = max(45, min(85, int(movie_speed_pct)))
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
