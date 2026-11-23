from __future__ import annotations

import math
import random
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader, QPainter, QPixmap, QTransform

from daily_overlay.ai.schema import ComponentConfig, Palette, TimeShift
from daily_overlay.assets.manager import ensure_spotlight_asset_async
from daily_overlay.config import (
    MAX_AMBIENT_SPEED_PX_S,
    MAX_TRANSLATE_SPEED_PX_S,
    MOVIE_PLAYBACK_SPEED_PCT,
)
from daily_overlay.context.win32_watcher import WindowContext
from daily_overlay.context.zones import ZoneRect
from daily_overlay.overlay.components.base import (
    OverlayComponent,
    ZoneTransition,
    move_towards_capped,
    path_crosses_center_or_far,
)


@dataclass
class _FlockItem:
    zone_idx: int
    x: float
    y: float
    vx: float
    vy: float
    scale: float
    phase: float
    anim_offset: float = 0.0
    anim_rate: float = 1.0
    roam_timer: float = 30.0
    spawn_alpha: float = 0.0
    transit_alpha: float = 1.0
    retiring: bool = False
    long_jumping: bool = False


@dataclass
class _DriftItem:
    zone_idx: int
    x: float
    y: float
    vx: float
    base_y_ratio: float
    scale: float
    phase: float
    anim_offset: float = 0.0
    anim_rate: float = 1.0
    rest_timer: float = 0.0


@dataclass
class _FallItem:
    zone_idx: int
    x: float
    y: float
    vx: float
    vy: float
    rot: float
    vrot: float
    scale: float
    phase: float
    anim_offset: float = 0.0
    anim_rate: float = 1.0
    rest_timer: float = 0.0


@dataclass
class _PeekItem:
    zone_idx: int
    anchor_rx: float
    anchor_ry: float
    scale: float
    phase: float
    anim_offset: float = 0.0
    anim_rate: float = 1.0
    life_phase: str = "fade_in"
    life_alpha: float = 0.0
    life_timer: float = 18.0
    alpha_mult: float = 1.0


def _decode_asset_frames(
    path: Path, max_h: int = 144
) -> tuple[list[QPixmap], list[float], float]:
    """Decode animated WebP/GIF into per-frame pixmaps + cumulative timestamps so sprites can play asynchronously."""
    reader = QImageReader(str(path))
    if reader.supportsAnimation() and reader.imageCount() > 1:
        frames: list[QPixmap] = []
        cum_times: list[float] = []
        total = 0.0
        limit = min(reader.imageCount(), 180)
        for _ in range(limit):
            img = reader.read()
            if img.isNull():
                break
            if img.height() > max_h:
                img = img.scaledToHeight(max_h, Qt.TransformationMode.SmoothTransformation)
            delay_ms = reader.nextImageDelay()
            dur_s = max(16, delay_ms if delay_ms > 0 else 40) / 1000.0
            total += dur_s
            frames.append(QPixmap.fromImage(img))
            cum_times.append(total)
        if frames:
            return frames, cum_times, total

    pix = QPixmap(str(path))
    if not pix.isNull():
        if pix.height() > max_h:
            pix = pix.scaledToHeight(max_h, Qt.TransformationMode.SmoothTransformation)
        return [pix], [1.0], 1.0
    return [], [], 0.0


class AssetSwarmComponent(OverlayComponent):
    """Unified 100% asset-driven swarm component supporting flock, drift, fall, and edge_peek motions."""

    def __init__(
        self,
        config: ComponentConfig,
        palette: Palette,
        movie_speed_pct: int = MOVIE_PLAYBACK_SPEED_PCT,
    ) -> None:
        super().__init__(config, palette, movie_speed_pct=movie_speed_pct)
        self._time = 0.0
        self._behavior = (
            config.behavior
            if config.behavior in ("flock", "drift", "fall", "edge_peek")
            else "drift"
        )
        self._zone_tx = ZoneTransition(relocate_feel=config.relocate_feel)
        self._cursor = (0.0, 0.0)
        self._render_zones: list[ZoneRect] = []
        self._zone_dim_alphas: list[float] = []
        self._seeded_counts: list[int] = []

        self._flock_items: list[_FlockItem] = []
        self._drift_items: list[_DriftItem] = []
        self._fall_items: list[_FallItem] = []
        self._peek_items: list[_PeekItem] = []

        self._instance_salt = random.randrange(1, 1_000_000)
        self._frames: list[QPixmap] = []
        self._frame_cum_times: list[float] = []
        self._total_anim_dur: float = 0.0
        self._anim_clock: float = 0.0
        self._pending_asset_path: Path | None = None
        self._has_primary = False
        self._scaled_cache: dict[tuple[int, int], QPixmap] = {}

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
        self._scaled_cache.clear()
        frames, cum_times, total = _decode_asset_frames(path)
        self._frames = frames
        self._frame_cum_times = cum_times
        self._total_anim_dur = total

    def _style_seed(self) -> int:
        key = f"{self.config.style}:{self._behavior}"
        return (sum(ord(ch) * (i + 1) for i, ch in enumerate(key)) + self._instance_salt) & 0xFFFF

    def _zone_item_count(self, z: ZoneRect, behavior: str) -> int:
        area = max(1.0, z.w * z.h)
        if behavior in ("drift", "edge_peek"):
            return max(1, min(3, int(round((area / 42000.0) * (0.7 + self.config.density * 0.9)))))
        if behavior == "flock":
            return max(1, min(3, int(round((area / 38000.0) * (0.7 + self.config.density * 0.9)))))
        return max(2, min(5, int(round((area / 24000.0) * (0.8 + self.config.density * 1.1)))))

    def _update_zone_dim_alphas(self, dt: float, zone_alphas: list[float] | None) -> None:
        n = len(self._render_zones)
        if len(self._zone_dim_alphas) < n:
            self._zone_dim_alphas.extend([1.0] * (n - len(self._zone_dim_alphas)))
        elif len(self._zone_dim_alphas) > n:
            self._zone_dim_alphas = self._zone_dim_alphas[:n]

        rate = min(1.0, dt * 4.5)
        for zi in range(n):
            target = zone_alphas[zi] if (zone_alphas and zi < len(zone_alphas)) else 1.0
            self._zone_dim_alphas[zi] += (target - self._zone_dim_alphas[zi]) * rate

    def _zone_dim(self, zi: int) -> float:
        if 0 <= zi < len(self._zone_dim_alphas):
            return self._zone_dim_alphas[zi]
        return 1.0

    @staticmethod
    def _fit_height(desired: float, z: ZoneRect, ratio: float = 0.72) -> int:
        cap = int(min(z.w, z.h) * ratio)
        return max(18, min(int(desired), cap))

    def _resolve_frame_idx(self, anim_offset: float = 0.0, anim_rate: float = 1.0) -> int:
        if len(self._frames) <= 1 or self._total_anim_dur <= 0.0:
            return 0
        t = (self._anim_clock * anim_rate + anim_offset * self._total_anim_dur) % self._total_anim_dur
        idx = bisect_right(self._frame_cum_times, t)
        return min(idx, len(self._frames) - 1)

    def _get_scaled_pixmap(
        self,
        target_h: int,
        anim_offset: float = 0.0,
        anim_rate: float = 1.0,
    ) -> QPixmap | None:
        if target_h < 8:
            target_h = 8
        if not self._frames:
            return None

        frame_id = self._resolve_frame_idx(anim_offset=anim_offset, anim_rate=anim_rate)
        raw = self._frames[frame_id]
        if raw.isNull():
            return None

        bucket_h = max(8, (target_h // 2) * 2)
        key = (frame_id, bucket_h)
        cached = self._scaled_cache.get(key)
        if cached is not None:
            return cached

        scaled = raw.scaledToHeight(bucket_h, Qt.TransformationMode.SmoothTransformation)
        if len(self._scaled_cache) > 256:
            self._scaled_cache.clear()
        self._scaled_cache[key] = scaled
        return scaled

    # --- Flock (smooth migrating Boids with slide-fade for cross-center jumps) ---

    def _update_flock(
        self,
        dt: float,
        ctx: WindowContext,
        zones: list[ZoneRect],
        spd_mult: float,
        container_rect: tuple[float, float, float, float] | None,
    ) -> None:
        self._render_zones = zones
        if not zones:
            for item in self._flock_items:
                item.retiring = True
                item.spawn_alpha = max(0.0, item.spawn_alpha - dt * 2.5)
            self._flock_items = [it for it in self._flock_items if it.spawn_alpha > 0.01]
            return

        c_rect = container_rect if container_rect is not None else (0.0, 0.0, 1536.0, 864.0)
        counts = [self._zone_item_count(z, "flock") for z in zones]
        target_slots: list[int] = []
        for zi, cnt in enumerate(counts):
            target_slots.extend([zi] * cnt)
        target_total = len(target_slots)

        active = [it for it in self._flock_items if not it.retiring]
        slots_dirty = (
            counts != self._seeded_counts
            or len(active) != target_total
            or any(it.zone_idx >= len(zones) for it in active)
        )
        if slots_dirty:
            for idx, it in enumerate(active):
                if idx < target_total:
                    it.zone_idx = target_slots[idx]
            self._seeded_counts = list(counts)

        rng = random.Random(self._style_seed() + len(active))
        while len(active) < target_total:
            zi = target_slots[len(active)]
            z = zones[zi]
            ang = rng.uniform(0.0, math.tau)
            spd = rng.uniform(6.0, 12.0)
            new_it = _FlockItem(
                zone_idx=zi,
                x=z.x + rng.uniform(0.18, 0.82) * z.w,
                y=z.y + rng.uniform(0.18, 0.82) * z.h,
                vx=math.cos(ang) * spd,
                vy=math.sin(ang) * spd,
                scale=rng.uniform(0.8, 1.2),
                phase=rng.uniform(0.0, math.tau),
                anim_offset=rng.uniform(0.0, 1.0),
                anim_rate=rng.uniform(0.84, 1.16),
                roam_timer=rng.uniform(22.0, 44.0),
                spawn_alpha=0.0,
                transit_alpha=1.0,
                retiring=False,
            )
            self._flock_items.append(new_it)
            active.append(new_it)

        if len(active) > target_total:
            for it in active[target_total:]:
                it.retiring = True

        max_speed = min(MAX_AMBIENT_SPEED_PX_S, 15.0 * spd_mult)
        min_speed = min(6.0 * spd_mult, max_speed * 0.5)
        cx, cy = ctx.cursor_pos

        for it in self._flock_items:
            if it.retiring:
                it.spawn_alpha = max(0.0, it.spawn_alpha - dt * 2.4)
            else:
                it.spawn_alpha = min(1.0, it.spawn_alpha + dt * 2.2)

            z = zones[it.zone_idx % len(zones)]
            inside = z.contains(it.x, it.y, padding=-10.0)
            ax = ay = 0.0

            if not it.retiring and not inside:
                zcx, zcy = z.x + z.w * 0.5, z.y + z.h * 0.5
                if it.long_jumping or path_crosses_center_or_far(
                    it.x, it.y, zcx, zcy, c_rect, self.config.relocate_feel
                ):
                    it.long_jumping = True
                    it.transit_alpha = max(0.0, it.transit_alpha - dt * 2.2)
                    cap_speed = 55.0
                    if it.transit_alpha <= 0.02:
                        it.x = z.x + random.uniform(0.2, 0.8) * z.w
                        it.y = z.y + random.uniform(0.2, 0.8) * z.h
                        ang = random.uniform(0.0, math.tau)
                        spd_in = random.uniform(6.0, 12.0)
                        it.vx = math.cos(ang) * spd_in
                        it.vy = math.sin(ang) * spd_in
                        it.long_jumping = False
                else:
                    pad_x = min(18.0, z.w * 0.4)
                    pad_y = min(18.0, z.h * 0.4)
                    tx = max(z.x + pad_x, min(z.right - pad_x, it.x))
                    ty = max(z.y + pad_y, min(z.bottom - pad_y, it.y))
                    dx_h = tx - it.x
                    dy_h = ty - it.y
                    d_h = max(1.0, math.hypot(dx_h, dy_h))
                    ax += (dx_h / d_h) * 85.0
                    ay += (dy_h / d_h) * 85.0
                    it.transit_alpha += (0.72 - it.transit_alpha) * min(1.0, dt * 4.0)
                    cap_speed = min(MAX_TRANSLATE_SPEED_PX_S * 0.7, 95.0)
            else:
                it.long_jumping = False
                it.transit_alpha = min(1.0, it.transit_alpha + dt * 1.8)
                cap_speed = max_speed

                if not it.retiring and len(zones) > 1:
                    it.roam_timer -= dt
                    if it.roam_timer <= 0.0:
                        it.roam_timer = random.uniform(24.0, 48.0)
                        other_zones = [zi for zi in range(len(zones)) if zi != it.zone_idx]
                        if other_zones:
                            it.zone_idx = random.choice(other_zones)
                            z = zones[it.zone_idx % len(zones)]
                            inside = z.contains(it.x, it.y, padding=-10.0)

                sep_x = sep_y = ali_x = ali_y = coh_x = coh_y = 0.0
                neighbors = 0
                for other in self._flock_items:
                    if other is it or other.retiring or other.zone_idx != it.zone_idx:
                        continue
                    dx = other.x - it.x
                    dy = other.y - it.y
                    dist = math.hypot(dx, dy)
                    if 0.1 < dist < 80.0:
                        neighbors += 1
                        ali_x += other.vx
                        ali_y += other.vy
                        coh_x += other.x
                        coh_y += other.y
                        if dist < 36.0:
                            sep_x -= dx / dist
                            sep_y -= dy / dist

                if neighbors > 0:
                    ali_x /= neighbors
                    ali_y /= neighbors
                    coh_x = coh_x / neighbors - it.x
                    coh_y = coh_y / neighbors - it.y
                    ax += sep_x * 24.0 + (ali_x - it.vx) * 0.35 + coh_x * 0.25
                    ay += sep_y * 24.0 + (ali_y - it.vy) * 0.35 + coh_y * 0.25

                ax += math.cos(self._time * 0.75 + it.phase) * 4.0
                ay += math.sin(self._time * 0.65 + it.phase * 1.3) * 4.0

                pad_x = min(24.0, z.w * 0.2)
                pad_y = min(24.0, z.h * 0.2)
                turn = 45.0
                if it.x < z.x + pad_x:
                    ax += turn * (1.0 - max(0.0, it.x - z.x) / max(1.0, pad_x))
                elif it.x > z.right - pad_x:
                    ax -= turn * (1.0 - max(0.0, z.right - it.x) / max(1.0, pad_x))
                if it.y < z.y + pad_y:
                    ay += turn * (1.0 - max(0.0, it.y - z.y) / max(1.0, pad_y))
                elif it.y > z.bottom - pad_y:
                    ay -= turn * (1.0 - max(0.0, z.bottom - it.y) / max(1.0, pad_y))

            if self.config.avoid_cursor and not it.long_jumping:
                cdx = it.x - cx
                cdy = it.y - cy
                cdist = math.hypot(cdx, cdy)
                if 0.1 < cdist < 110.0:
                    repel = (1.0 - cdist / 110.0) * 55.0
                    ax += (cdx / cdist) * repel
                    ay += (cdy / cdist) * repel

            it.vx += ax * dt
            it.vy += ay * dt
            spd = math.hypot(it.vx, it.vy)
            if spd > cap_speed:
                it.vx = it.vx / spd * cap_speed
                it.vy = it.vy / spd * cap_speed
            elif inside and 0.01 < spd < min_speed:
                it.vx = it.vx / spd * min_speed
                it.vy = it.vy / spd * min_speed

            it.x += it.vx * dt
            it.y += it.vy * dt

        self._flock_items = [it for it in self._flock_items if not (it.retiring and it.spawn_alpha <= 0.01)]

    # --- Drift (horizontal drift + geometry-aware ZoneTransition) ---

    def _seed_drift(self, zones: list[ZoneRect]) -> None:
        if not zones:
            self._drift_items.clear()
            self._seeded_counts = []
            return
        rng = random.Random(self._style_seed())
        self._drift_items = []
        self._seeded_counts = []
        for zi, z in enumerate(zones):
            cnt = self._zone_item_count(z, "drift")
            self._seeded_counts.append(cnt)
            for i in range(cnt):
                ratio_x = (i + rng.uniform(0.18, 0.82)) / cnt
                ratio_y = rng.uniform(0.22, 0.78)
                dir_sign = 1 if (i + self._style_seed()) % 2 == 0 else -1
                scale = rng.uniform(0.75, 1.2)
                half_h = self._fit_height(54.0 * self.config.size * scale, z) * 0.5
                y_min = z.y + half_h
                y_max = z.bottom - half_h
                init_y = (
                    max(y_min, min(y_max, z.y + ratio_y * z.h))
                    if y_min < y_max
                    else (z.y + z.h * 0.5)
                )
                self._drift_items.append(
                    _DriftItem(
                        zone_idx=zi,
                        x=z.x + ratio_x * z.w,
                        y=init_y,
                        vx=dir_sign * rng.uniform(4.0, 9.5),
                        base_y_ratio=ratio_y,
                        scale=scale,
                        phase=rng.uniform(0.0, math.tau),
                        anim_offset=rng.uniform(0.0, 1.0),
                        anim_rate=rng.uniform(0.84, 1.16),
                        rest_timer=0.0,
                    )
                )

    def _update_drift(
        self,
        dt: float,
        zones: list[ZoneRect],
        spd_mult: float,
        container_rect: tuple[float, float, float, float] | None,
    ) -> None:
        active = self._zone_tx.update(dt, zones, container_rect=container_rect)
        self._render_zones = active
        if not active:
            self._drift_items.clear()
            self._seeded_counts = []
            return
        counts = [self._zone_item_count(z, "drift") for z in active]
        if (
            self._zone_tx.just_initialized
            or self._zone_tx.just_jumped
            or counts != self._seeded_counts
            or len(self._drift_items) != sum(counts)
        ):
            self._seed_drift(active)

        for it in self._drift_items:
            zi = it.zone_idx % len(active)
            z = active[zi]
            if it.rest_timer > 0.0:
                it.rest_timer -= dt
                if it.rest_timer <= 0.0:
                    it.rest_timer = 0.0
                    if len(active) > 1 and random.random() < 0.45:
                        others = [k for k in range(len(active)) if k != zi]
                        if others:
                            zi = random.choice(others)
                            it.zone_idx = zi
                            z = active[zi]
                    it.base_y_ratio = random.uniform(0.18, 0.82)
                    it.scale = random.uniform(0.75, 1.2)
                    dir_sign = 1 if it.vx >= 0 else -1
                    it.vx = dir_sign * random.uniform(4.0, 9.5)
                    it.anim_offset = random.uniform(0.0, 1.0)
                    it.anim_rate = random.uniform(0.84, 1.16)
                    h_re = self._fit_height(54.0 * self.config.size * it.scale, z)
                    pad_re = max(12.0, h_re * 0.4)
                    it.x = (z.x - pad_re) if dir_sign > 0 else (z.right + pad_re)
                    it.y = z.y + it.base_y_ratio * z.h
                continue

            dx_z, dy_z = self._zone_tx.last_deltas[zi] if zi < len(self._zone_tx.last_deltas) else (0.0, 0.0)
            h = self._fit_height(54.0 * self.config.size * it.scale, z)
            half_h = h * 0.5
            eff_vx = max(
                -MAX_AMBIENT_SPEED_PX_S * 0.65,
                min(MAX_AMBIENT_SPEED_PX_S * 0.65, it.vx * spd_mult),
            )
            it.x += dx_z + eff_vx * dt
            it.y += dy_z

            y_min = z.y + half_h
            y_max = z.bottom - half_h
            if y_min >= y_max:
                target_y = z.y + z.h * 0.5
            else:
                bob_amp = min(5.0, max(0.0, (y_max - y_min) * 0.3))
                base_y = z.y + it.base_y_ratio * z.h
                target_y = max(
                    y_min,
                    min(y_max, base_y + math.sin(self._time * 0.32 + it.phase) * bob_amp),
                )
            it.y = move_towards_capped(it.y, target_y, dt, gain=3.0, max_speed=MAX_TRANSLATE_SPEED_PX_S)

            pad = max(12.0, half_h * 0.8)
            if it.x > z.right + pad or it.x < z.x - pad:
                it.rest_timer = random.uniform(0.6, 3.6)

    # --- Fall (drifting petals, leaves, snow, droplets) ---

    def _seed_fall(self, zones: list[ZoneRect]) -> None:
        if not zones:
            self._fall_items.clear()
            self._seeded_counts = []
            return
        rng = random.Random(self._style_seed())
        self._fall_items = []
        self._seeded_counts = []
        for zi, z in enumerate(zones):
            cnt = self._zone_item_count(z, "fall")
            self._seeded_counts.append(cnt)
            for _ in range(cnt):
                self._fall_items.append(
                    _FallItem(
                        zone_idx=zi,
                        x=z.x + rng.uniform(0.08, 0.92) * z.w,
                        y=z.y + rng.uniform(0.0, 1.0) * z.h,
                        vx=rng.uniform(-4.0, 4.5),
                        vy=rng.uniform(7.0, 14.0),
                        rot=rng.uniform(0.0, 360.0),
                        vrot=rng.uniform(-14.0, 14.0),
                        scale=rng.uniform(0.72, 1.2),
                        phase=rng.uniform(0.0, math.tau),
                        anim_offset=rng.uniform(0.0, 1.0),
                        anim_rate=rng.uniform(0.84, 1.18),
                        rest_timer=0.0,
                    )
                )

    def _update_fall(
        self,
        dt: float,
        ctx: WindowContext,
        zones: list[ZoneRect],
        spd_mult: float,
        container_rect: tuple[float, float, float, float] | None,
    ) -> None:
        active = self._zone_tx.update(dt, zones, container_rect=container_rect)
        self._render_zones = active
        if not active:
            self._fall_items.clear()
            self._seeded_counts = []
            return
        counts = [self._zone_item_count(z, "fall") for z in active]
        if (
            self._zone_tx.just_initialized
            or self._zone_tx.just_jumped
            or counts != self._seeded_counts
            or len(self._fall_items) != sum(counts)
        ):
            self._seed_fall(active)

        cx, cy = ctx.cursor_pos
        for it in self._fall_items:
            zi = it.zone_idx % len(active)
            z = active[zi]
            if it.rest_timer > 0.0:
                it.rest_timer -= dt
                if it.rest_timer <= 0.0:
                    it.rest_timer = 0.0
                    if len(active) > 1 and random.random() < 0.35:
                        others = [k for k in range(len(active)) if k != zi]
                        if others:
                            zi = random.choice(others)
                            it.zone_idx = zi
                            z = active[zi]
                    it.scale = random.uniform(0.72, 1.2)
                    half_h_re = self._fit_height(34.0 * self.config.size * it.scale, z) * 0.5
                    x_min_re = z.x + half_h_re
                    x_max_re = z.right - half_h_re
                    it.y = z.y
                    it.x = (
                        z.x + half_h_re + random.uniform(0.0, 1.0) * max(1.0, z.w - 2.0 * half_h_re)
                        if x_min_re < x_max_re
                        else z.x + z.w * 0.5
                    )
                    it.vx = random.uniform(-4.0, 4.5)
                    it.vrot = random.uniform(-14.0, 14.0)
                    it.anim_offset = random.uniform(0.0, 1.0)
                    it.anim_rate = random.uniform(0.84, 1.18)
                continue

            dx_z, dy_z = self._zone_tx.last_deltas[zi] if zi < len(self._zone_tx.last_deltas) else (0.0, 0.0)
            vx = (it.vx + math.sin(self._time * 0.65 + it.phase) * 4.5) * spd_mult
            vy = it.vy * spd_mult

            if self.config.avoid_cursor:
                dx = it.x - cx
                dy = it.y - cy
                dist = math.hypot(dx, dy)
                if 0.1 < dist < 95.0:
                    push = (1.0 - dist / 95.0) * 30.0
                    vx += (dx / dist) * push
                    vy += (dy / dist) * push

            vmag = math.hypot(vx, vy)
            if vmag > MAX_AMBIENT_SPEED_PX_S:
                vx = vx / vmag * MAX_AMBIENT_SPEED_PX_S
                vy = vy / vmag * MAX_AMBIENT_SPEED_PX_S

            it.x += dx_z + vx * dt
            it.y += dy_z + vy * dt
            it.rot = (it.rot + it.vrot * dt) % 360.0

            half_h = self._fit_height(34.0 * self.config.size * it.scale, z) * 0.5
            x_min = z.x + half_h
            x_max = z.right - half_h
            if x_min >= x_max:
                it.x = z.x + z.w * 0.5
            else:
                it.x = max(x_min, min(x_max, it.x))

            if it.y > z.bottom:
                it.rest_timer = random.uniform(0.2, 2.2)
            elif it.y < z.y:
                it.y = z.bottom

    # --- Edge Peek (botanical / lantern edge clusters) ---

    def _seed_edge_peek(self, zones: list[ZoneRect]) -> None:
        if not zones:
            self._peek_items.clear()
            self._seeded_counts = []
            return
        rng = random.Random(self._style_seed())
        self._peek_items = []
        self._seeded_counts = []
        for zi, z in enumerate(zones):
            cnt = self._zone_item_count(z, "edge_peek")
            self._seeded_counts.append(cnt)
            for i in range(cnt):
                if z.w > 520.0:
                    side = 0 if i % 2 == 0 else 1
                    rx = rng.uniform(0.04, 0.2) if side == 0 else rng.uniform(0.8, 0.96)
                else:
                    rx = (i + rng.uniform(0.25, 0.75)) / cnt
                ry = rng.uniform(0.26, 0.78)
                start_resting = cnt >= 2 and rng.random() < 0.30
                self._peek_items.append(
                    _PeekItem(
                        zone_idx=zi,
                        anchor_rx=max(0.08, min(0.92, rx)),
                        anchor_ry=max(0.15, min(0.85, ry)),
                        scale=rng.uniform(0.8, 1.18),
                        phase=rng.uniform(0.0, math.tau),
                        anim_offset=rng.uniform(0.0, 1.0),
                        anim_rate=rng.uniform(0.82, 1.18),
                        life_phase="rest" if start_resting else "fade_in",
                        life_alpha=0.0,
                        life_timer=rng.uniform(1.5, 6.5) if start_resting else rng.uniform(9.0, 26.0),
                    )
                )

    def _update_edge_peek(
        self,
        dt: float,
        ctx: WindowContext,
        zones: list[ZoneRect],
        container_rect: tuple[float, float, float, float] | None,
    ) -> None:
        active = self._zone_tx.update(dt, zones, container_rect=container_rect)
        self._render_zones = active
        if not active:
            self._peek_items.clear()
            self._seeded_counts = []
            return
        counts = [self._zone_item_count(z, "edge_peek") for z in active]
        if (
            self._zone_tx.just_initialized
            or self._zone_tx.just_jumped
            or counts != self._seeded_counts
            or len(self._peek_items) != sum(counts)
        ):
            self._seed_edge_peek(active)

        cx, cy = ctx.cursor_pos
        for it in self._peek_items:
            if it.life_phase == "fade_in":
                it.life_alpha = min(1.0, it.life_alpha + dt * 0.55)
                if it.life_alpha >= 1.0:
                    it.life_phase = "dwell"
            elif it.life_phase == "dwell":
                it.life_timer -= dt
                if it.life_timer <= 0.0:
                    it.life_phase = "fade_out"
            elif it.life_phase == "fade_out":
                it.life_alpha = max(0.0, it.life_alpha - dt * 0.55)
                if it.life_alpha <= 0.0:
                    it.life_phase = "rest"
                    it.life_timer = random.uniform(2.5, 8.0)
            else:  # rest
                it.life_timer -= dt
                if it.life_timer <= 0.0:
                    old_zi = it.zone_idx % len(active)
                    if len(active) > 1 and random.random() < 0.55:
                        others = [k for k in range(len(active)) if k != old_zi]
                        occupancy = {
                            k: sum(
                                1
                                for o in self._peek_items
                                if o is not it and (o.zone_idx % len(active)) == k
                            )
                            for k in others
                        }
                        min_occ = min(occupancy.values())
                        least_busy = [k for k, occ in occupancy.items() if occ == min_occ]
                        it.zone_idx = random.choice(least_busy)
                    else:
                        it.zone_idx = old_zi
                    new_rx = random.uniform(0.10, 0.90)
                    new_ry = random.uniform(0.18, 0.82)
                    if it.zone_idx == old_zi and abs(new_rx - it.anchor_rx) < 0.18:
                        new_rx = 0.85 if it.anchor_rx < 0.5 else 0.15
                    it.anchor_rx = new_rx
                    it.anchor_ry = new_ry
                    it.scale = random.uniform(0.8, 1.18)
                    it.phase = random.uniform(0.0, math.tau)
                    it.anim_offset = random.uniform(0.0, 1.0)
                    it.anim_rate = random.uniform(0.82, 1.18)
                    it.life_timer = random.uniform(14.0, 30.0)
                    it.life_phase = "fade_in"

            z = active[it.zone_idx % len(active)]
            px = z.x + it.anchor_rx * z.w
            py = z.y + it.anchor_ry * z.h
            target_dim = 1.0
            if self.config.avoid_cursor:
                dist = math.hypot(px - cx, py - cy)
                if dist < 110.0:
                    target_dim = 0.35 + 0.65 * (dist / 110.0)
            it.alpha_mult += (target_dim - it.alpha_mult) * min(1.0, dt * 8.0)

    def update(
        self,
        dt: float,
        ctx: WindowContext,
        zones: list[ZoneRect],
        time_shift: TimeShift,
        zone_alphas: list[float] | None = None,
        container_rect: tuple[float, float, float, float] | None = None,
    ) -> None:
        dt = min(max(dt, 0.0), 0.05)
        self._cursor = ctx.cursor_pos
        spd_mult = self.config.speed * time_shift.speed
        self._time += dt * spd_mult
        self._anim_clock += dt * (self._movie_speed_pct / 100.0)

        if self._pending_asset_path is not None:
            path = self._pending_asset_path
            self._pending_asset_path = None
            self._load_asset(path)

        if self._behavior == "flock":
            self._update_flock(dt, ctx, zones, spd_mult, container_rect)
        elif self._behavior == "drift":
            self._update_drift(dt, zones, spd_mult, container_rect)
        elif self._behavior == "fall":
            self._update_fall(dt, ctx, zones, spd_mult, container_rect)
        else:
            self._update_edge_peek(dt, ctx, zones, container_rect)

        self._update_zone_dim_alphas(dt, zone_alphas)

    def _draw_sprite(
        self,
        painter: QPainter,
        x: float,
        y: float,
        target_h: int,
        alpha: float,
        angle: float = 0.0,
        flip_x: bool = False,
        anim_offset: float = 0.0,
        anim_rate: float = 1.0,
    ) -> None:
        if alpha <= 0.01:
            return
        scaled = self._get_scaled_pixmap(
            target_h,
            anim_offset=anim_offset,
            anim_rate=anim_rate,
        )
        if scaled is None or scaled.isNull():
            return

        if flip_x:
            scaled = scaled.transformed(QTransform().scale(-1.0, 1.0))

        painter.save()
        painter.setOpacity(min(1.0, max(0.0, alpha)))
        painter.translate(x, y)
        if abs(angle) > 0.2:
            painter.rotate(angle)
        painter.drawPixmap(-scaled.width() // 2, -scaled.height() // 2, scaled)
        painter.restore()

    def paint(self, painter: QPainter, global_opacity: float) -> None:
        if global_opacity <= 0.01:
            return

        if self._behavior == "flock":
            for it in self._flock_items:
                dim = self._zone_dim(it.zone_idx)
                alpha = global_opacity * it.spawn_alpha * it.transit_alpha * dim * 0.92
                pitch = math.degrees(math.atan2(it.vy, max(12.0, abs(it.vx)))) * 0.55
                pitch = max(-22.0, min(22.0, pitch))
                if it.vx < 0:
                    pitch = -pitch
                if self._render_zones:
                    z_ref = self._render_zones[it.zone_idx % len(self._render_zones)]
                    h = self._fit_height(40.0 * self.config.size * it.scale, z_ref, ratio=0.68)
                else:
                    h = max(18, int(40.0 * self.config.size * it.scale))
                self._draw_sprite(
                    painter,
                    it.x,
                    it.y,
                    h,
                    alpha,
                    angle=pitch,
                    flip_x=(it.vx < 0),
                    anim_offset=it.anim_offset,
                    anim_rate=it.anim_rate,
                )
            return

        zone_alpha = global_opacity * self._zone_tx.alpha
        if zone_alpha <= 0.01 or not self._render_zones:
            return

        if self._behavior == "drift":
            for it in self._drift_items:
                if it.rest_timer > 0.0:
                    continue
                zi = it.zone_idx % len(self._render_zones)
                z = self._render_zones[zi]
                h = self._fit_height(54.0 * self.config.size * it.scale, z)
                edge_pad = max(18.0, min(z.w * 0.25, h * 1.1))
                fade_l = min(1.0, max(0.0, (it.x - z.x) / max(1.0, edge_pad)))
                fade_r = min(1.0, max(0.0, (z.right - it.x) / max(1.0, edge_pad)))
                alpha = zone_alpha * min(fade_l, fade_r) * self._zone_dim(zi) * 0.78
                self._draw_sprite(
                    painter,
                    it.x,
                    it.y,
                    h,
                    alpha,
                    anim_offset=it.anim_offset,
                    anim_rate=it.anim_rate,
                )
            return

        if self._behavior == "fall":
            for it in self._fall_items:
                if it.rest_timer > 0.0:
                    continue
                zi = it.zone_idx % len(self._render_zones)
                z = self._render_zones[zi]
                rel_y = (it.y - z.y) / max(1.0, z.h)
                v_fade = min(1.0, rel_y / 0.15) * min(1.0, (1.0 - rel_y) / 0.15)
                alpha = zone_alpha * max(0.0, v_fade) * self._zone_dim(zi) * 0.88
                h = self._fit_height(34.0 * self.config.size * it.scale, z)
                self._draw_sprite(
                    painter,
                    it.x,
                    it.y,
                    h,
                    alpha,
                    angle=it.rot,
                    anim_offset=it.anim_offset,
                    anim_rate=it.anim_rate,
                )
            return

        # edge_peek
        for it in self._peek_items:
            if it.life_alpha <= 0.01:
                continue
            zi = it.zone_idx % len(self._render_zones)
            z = self._render_zones[zi]
            sway = math.sin(self._time * 0.45 + it.phase) * 4.5
            breath = 1.0 + 0.025 * math.sin(self._time * 0.45 + it.phase)
            h = self._fit_height(54.0 * self.config.size * it.scale * breath, z)
            half_h = h * 0.5
            dx_drift = math.sin(self._time * 0.25 + it.phase) * min(8.0, z.w * 0.04)
            dy_drift = math.cos(self._time * 0.21 + it.phase * 1.2) * min(6.0, z.h * 0.04)
            raw_px = z.x + it.anchor_rx * z.w + dx_drift
            raw_py = z.y + it.anchor_ry * z.h + dy_drift
            px = max(z.x + half_h, min(z.right - half_h, raw_px)) if z.w >= h else (z.x + z.w * 0.5)
            py = max(z.y + half_h, min(z.bottom - half_h, raw_py)) if z.h >= h else (z.y + z.h * 0.5)
            alpha = zone_alpha * it.alpha_mult * it.life_alpha * self._zone_dim(zi) * 0.85
            self._draw_sprite(
                painter,
                px,
                py,
                h,
                alpha,
                angle=sway,
                anim_offset=it.anim_offset,
                anim_rate=it.anim_rate,
            )
