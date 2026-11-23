from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, replace
from typing import Literal

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter, QPainterPath

from daily_overlay.ai.schema import (
    ComponentConfig,
    DailyDeck,
    DailyScenario,
    StateModifier,
    TimeShift,
)
from daily_overlay.config import (
    COMMON_BROWSERS,
    DAY_ROLLOVER_HOUR,
    EVENING_ROLLOVER_HOUR,
    AppProfile,
    current_cycle_slot,
    load_app_profiles,
    match_profile_for_context,
)
from daily_overlay.context.browser_bridge import BrowserContext
from daily_overlay.context.empty_space import EmptySpaceSnapshot
from daily_overlay.context.win32_watcher import WindowContext
from daily_overlay.context.zones import ZoneRect, compute_zones
from daily_overlay.overlay.cameo import OrganicCameoScheduler
from daily_overlay.overlay.components import (
    AnimatedActorComponent,
    AssetSwarmComponent,
    OverlayComponent,
    create_component,
)

_IGNORED_REAL_PROCS = {
    "explorer.exe",
    "shellexperiencehost.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "applicationframehost.exe",
}

ActiveSource = Literal["global", "daily_adapted", "pinned"]
ContextState = Literal["normal", "music", "deep_focus", "idle_break"]


def _window_bounds(ctx: WindowContext, screen_w: float, avail_h: float) -> tuple[float, float, float, float]:
    if not ctx.is_desktop:
        wl, wt, wr, wb = ctx.rect
        return float(wl), float(wt), max(100.0, float(wr - wl)), max(100.0, float(wb - wt))
    return 0.0, 0.0, float(screen_w), float(avail_h)


def _map_relative_boxes(
    rel_boxes: list[list[float]],
    ctx: WindowContext,
    screen_w: float,
    avail_h: float,
) -> list[ZoneRect]:
    wl, wt, win_w, win_h = _window_bounds(ctx, screen_w, avail_h)
    mapped: list[ZoneRect] = []
    for box in rel_boxes:
        if len(box) != 4:
            continue
        rx, ry, rw, rh = box
        zw = rw * win_w
        zh = rh * win_h
        if zw >= 24.0 and zh >= 24.0:
            mapped.append(ZoneRect(x=wl + rx * win_w, y=wt + ry * win_h, w=zw, h=zh))
    return mapped


def _subtract_forbidden_from_zones(
    zones: list[ZoneRect],
    forbidden: list[ZoneRect],
) -> list[ZoneRect]:
    if not forbidden or not zones:
        return zones

    current = list(zones)
    for f in forbidden:
        next_zones: list[ZoneRect] = []
        for z in current:
            ix0 = max(z.x, f.x)
            iy0 = max(z.y, f.y)
            ix1 = min(z.right, f.right)
            iy1 = min(z.bottom, f.bottom)
            if ix1 <= ix0 or iy1 <= iy0:
                next_zones.append(z)
                continue

            inter_area = (ix1 - ix0) * (iy1 - iy0)
            z_area = max(1.0, z.w * z.h)
            if inter_area / z_area >= 0.72:
                # Largely covered by forbidden zone -> drop
                continue

            # Keep largest remaining sub-strip (>= 40x40) outside forbidden box
            candidates = [
                ZoneRect(z.x, z.y, ix0 - z.x, z.h),
                ZoneRect(ix1, z.y, z.right - ix1, z.h),
                ZoneRect(z.x, z.y, z.w, iy0 - z.y),
                ZoneRect(z.x, iy1, z.w, z.bottom - iy1),
            ]
            valid = [c for c in candidates if c.w >= 40.0 and c.h >= 40.0]
            if valid:
                next_zones.append(max(valid, key=lambda c: c.w * c.h))
        current = next_zones
    return current


@dataclass
class _FadingLayer:
    components: list[OverlayComponent]
    non_actor_schedulers: dict[int, OrganicCameoScheduler]
    opacity: float


class OverlayEngine:
    """Unified cast OverlayEngine with smooth zone relocation, crossfade on style change & Alt-Tab debounce."""

    def __init__(
        self,
        deck_or_scenario: DailyDeck | DailyScenario,
        profiles: list[AppProfile] | None = None,
    ) -> None:
        if isinstance(deck_or_scenario, DailyScenario):
            slot_day, slot_period = current_cycle_slot()
            self.deck = DailyDeck(
                date=deck_or_scenario.date or slot_day.isoformat(),
                period=slot_period,
                active_index=0,
                scenarios=[deck_or_scenario],
            )
        else:
            self.deck = deck_or_scenario

        self.profiles: list[AppProfile] = profiles if profiles is not None else load_app_profiles()
        self.paused: bool = False
        self.zen_until: float = 0.0
        self.user_opacity_mult: float = 1.0
        self.current_opacity: float = 0.0

        self.active_rule_name: str = "Chung"
        self.active_profile: AppProfile | None = None
        self.active_source: ActiveSource = "global"
        self.active_context_state: ContextState = "normal"
        self.active_empty_status: str = "desktop"
        self.active_empty_count: int = 0
        self._comp_sticky_zone: dict[int, ZoneRect] = {}
        self._ordered_realtime_pockets: list[ZoneRect] = []
        self._last_realtime_hwnd: int = 0

        self.last_process_name: str = "Desktop"
        self.last_window_title: str = ""
        self.last_browser_host: str = ""
        self.last_real_app_ctx: WindowContext | None = None
        self.last_real_browser_ctx: BrowserContext | None = None

        self._outgoing_layers: list[_FadingLayer] = []
        self._layer_fade_in: float = 0.0

        self._app_switch_debounce_sec: float = 0.25
        self._committed_ctx_sig: tuple[str, str, str] | None = None
        self._pending_ctx_sig: tuple[str, str, str] | None = None
        self._pending_ctx_elapsed: float = 0.0
        self._committed_ctx: WindowContext | None = None
        self._committed_browser_ctx: BrowserContext | None = None
        self._committed_profile: AppProfile | None = None
        self._force_commit_next: bool = True

        self._active_key: str = ""
        self._components: list[OverlayComponent] = []
        self._non_actor_schedulers: dict[int, OrganicCameoScheduler] = {}
        self._current_forbidden_rects: list[ZoneRect] = []
        self._switch_layer(self._daily_layer_key(), self.scenario.global_components, crossfade=False)

    @property
    def scenario(self) -> DailyScenario:
        return self.deck.active_scenario

    def _daily_layer_key(self) -> str:
        return f"daily:{self.deck.cycle_key}:{self.deck.active_index}:{self.scenario.theme_name}"

    def set_deck(self, deck: DailyDeck) -> None:
        self.deck = deck
        self._force_commit_next = True
        self._switch_layer(self._daily_layer_key(), self.scenario.global_components, crossfade=True)

    def set_scenario(self, scenario: DailyScenario) -> None:
        if self.deck.scenarios:
            idx = max(0, min(self.deck.active_index, len(self.deck.scenarios) - 1))
            self.deck.scenarios[idx] = scenario
        else:
            self.deck.scenarios = [scenario]
            self.deck.active_index = 0
        self._force_commit_next = True
        self._switch_layer(self._daily_layer_key(), scenario.global_components, crossfade=True)

    def reload_profiles(self, profiles: list[AppProfile] | None = None) -> None:
        self.profiles = profiles if profiles is not None else load_app_profiles()
        self._force_commit_next = True

    def set_zen_minutes(self, minutes: int) -> None:
        if minutes <= 0:
            self.zen_until = 0.0
        else:
            self.zen_until = time.monotonic() + minutes * 60.0

    @property
    def is_zen_active(self) -> bool:
        return time.monotonic() < self.zen_until

    def _current_time_shift(self) -> TimeShift:
        hour = dt.datetime.now().hour
        if DAY_ROLLOVER_HOUR <= hour < 12:
            return self.scenario.time_shifts.morning
        if 12 <= hour < EVENING_ROLLOVER_HOUR:
            return self.scenario.time_shifts.afternoon
        return self.scenario.time_shifts.night

    def _resolve_context_modifier(
        self,
        ctx: WindowContext,
        is_audio_playing: bool,
    ) -> tuple[ContextState, StateModifier]:
        mods = self.scenario.modifiers
        if is_audio_playing:
            return "music", mods.on_music_playing
        if ctx.idle_seconds >= 60.0:
            return "idle_break", mods.on_idle_break
        if not ctx.is_desktop and ctx.idle_seconds < 1.5:
            return "deep_focus", mods.on_deep_focus
        return "normal", StateModifier()

    def _switch_layer(
        self,
        key: str,
        configs: list[ComponentConfig],
        crossfade: bool = True,
    ) -> None:
        if key == self._active_key:
            return
        if crossfade and self._components and self.current_opacity > 0.02:
            self._outgoing_layers.append(
                _FadingLayer(
                    components=list(self._components),
                    non_actor_schedulers=dict(self._non_actor_schedulers),
                    opacity=max(0.05, self._layer_fade_in),
                )
            )
            if len(self._outgoing_layers) > 2:
                self._outgoing_layers = self._outgoing_layers[-2:]
            self._layer_fade_in = 0.0
        elif not self._components:
            self._layer_fade_in = 0.0

        self._active_key = key
        self._comp_sticky_zone.clear()
        self._ordered_realtime_pockets.clear()
        speed_pct = self.scenario.movie_speed_pct
        rest_sec = float(self.scenario.cameo_rest_sec)
        self._components = [
            create_component(
                cfg,
                self.scenario.palette,
                movie_speed_pct=speed_pct,
                cameo_rest_sec=rest_sec,
            )
            for cfg in configs
        ]
        self._non_actor_schedulers = {}
        for idx, comp in enumerate(self._components):
            if not isinstance(comp, AnimatedActorComponent) and comp.config.presence in (
                "cameo",
                "peek_fade",
            ):
                self._non_actor_schedulers[idx] = OrganicCameoScheduler(
                    comp.config.presence,
                    min_cooldown_override=rest_sec,
                )

    def should_hide(self, ctx: WindowContext) -> bool:
        return self.paused or self.is_zen_active or ctx.is_fullscreen

    def _zones_from_wireframe(
        self,
        zone_type: str,
        wf: dict,
        ctx: WindowContext,
        screen_w: float,
        avail_h: float,
    ) -> list[ZoneRect]:
        blocks = wf.get("blocks") or []
        if not blocks or wf.get("source") == "uia_none":
            return []

        vp = wf.get("viewport") or [screen_w, avail_h]
        vw = max(1.0, float(vp[0]))
        vh = max(1.0, float(vp[1]))
        wl, wt, win_w, win_h = _window_bounds(ctx, screen_w, avail_h)

        def _scale(box: list) -> ZoneRect:
            bx, by, bw, bh = box
            return ZoneRect(
                x=wl + (bx / vw) * win_w,
                y=wt + (by / vh) * win_h,
                w=(bw / vw) * win_w,
                h=(bh / vh) * win_h,
            )

        if zone_type == "left_right_margins":
            out = [_scale(b["box"]) for b in blocks if b.get("role") == "empty_space" and len(b.get("box", [])) == 4]
            return [z for z in out if z.w >= 48.0 and z.h >= 80.0]

        if zone_type == "right_edge":
            right_spaces = [
                _scale(b["box"])
                for b in blocks
                if b.get("role") in ("empty_space", "right_sidebar")
                and len(b.get("box", [])) == 4
                and b["box"][0] >= vw * 0.55
            ]
            valid = [z for z in right_spaces if z.w >= 50.0 and z.h >= 100.0]
            if valid:
                return valid

        if zone_type == "top_edge":
            top_bars = [
                _scale(b["box"])
                for b in blocks
                if b.get("role") in ("top_bar", "header") and len(b.get("box", [])) == 4
            ]
            if top_bars:
                tb = top_bars[0]
                return [ZoneRect(wl, wt, win_w, max(48.0, min(140.0, tb.h)))]

        if zone_type == "bottom_edge":
            bot_bars = [
                _scale(b["box"])
                for b in blocks
                if b.get("role") == "bottom_bar" and len(b.get("box", [])) == 4
            ]
            if bot_bars:
                bb = bot_bars[0]
                h = max(68.0, min(115.0, win_h * 0.11))
                return [ZoneRect(wl, max(wt, bb.y - h), win_w, h)]

        return []

    @staticmethod
    def _zones_close_or_overlap(a: ZoneRect, b: ZoneRect) -> bool:
        acx, acy = a.x + a.w * 0.5, a.y + a.h * 0.5
        bcx, bcy = b.x + b.w * 0.5, b.y + b.h * 0.5
        if ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5 <= 130.0:
            return True
        return max(a.x, b.x) < min(a.right, b.right) and max(a.y, b.y) < min(a.bottom, b.bottom)

    def _order_pockets_stably(
        self,
        raw_pairs: list[tuple[ZoneRect, bool]],
    ) -> tuple[list[ZoneRect], list[bool]]:
        if not raw_pairs:
            self._ordered_realtime_pockets = []
            return [], []

        if not self._ordered_realtime_pockets:
            ordered = [p[0] for p in raw_pairs]
            occs = [p[1] for p in raw_pairs]
            self._ordered_realtime_pockets = list(ordered)
            return ordered, occs

        ordered: list[ZoneRect] = []
        occs: list[bool] = []
        used: set[int] = set()

        for prev in self._ordered_realtime_pockets:
            pcx, pcy = prev.x + prev.w * 0.5, prev.y + prev.h * 0.5
            best_i = -1
            best_d = 145.0
            for idx, (cand, _occ) in enumerate(raw_pairs):
                if idx in used:
                    continue
                d = ((cand.x + cand.w * 0.5 - pcx) ** 2 + (cand.y + cand.h * 0.5 - pcy) ** 2) ** 0.5
                if d <= best_d:
                    best_d = d
                    best_i = idx
            if best_i >= 0:
                used.add(best_i)
                ordered.append(raw_pairs[best_i][0])
                occs.append(raw_pairs[best_i][1])

        for idx, (cand, occ) in enumerate(raw_pairs):
            if idx not in used:
                ordered.append(cand)
                occs.append(occ)

        self._ordered_realtime_pockets = list(ordered)
        return ordered, occs

    def _assign_realtime_zones(
        self,
        mapped_zones: list[ZoneRect],
        zone_alphas: list[float] | None = None,
    ) -> tuple[dict[int, list[ZoneRect]], dict[int, list[float]]]:
        """Share ALL valid pockets across AssetSwarmComponents (anti-clumping) and give actors 1 sticky pocket."""
        valid = [z for z in mapped_zones if z.w >= 76.0 and z.h >= 52.0]
        if not valid:
            return {}, {}

        alphas = (
            list(zone_alphas[: len(valid)])
            if zone_alphas and len(zone_alphas) >= len(valid)
            else [1.0] * len(valid)
        )
        assignments: dict[int, list[ZoneRect]] = {}
        alpha_assignments: dict[int, list[float]] = {}
        claimed_actor_idx: set[int] = set()

        for idx, comp in enumerate(self._components):
            if isinstance(comp, AssetSwarmComponent):
                assignments[idx] = list(valid)
                alpha_assignments[idx] = list(alphas)
                continue

            prev = self._comp_sticky_zone.get(idx)
            chosen_zi: int | None = None
            if prev is not None:
                for zi, cand in enumerate(valid):
                    if zi not in claimed_actor_idx and self._zones_close_or_overlap(prev, cand):
                        chosen_zi = zi
                        break
            if chosen_zi is None:
                for zi in range(len(valid)):
                    if zi not in claimed_actor_idx:
                        chosen_zi = zi
                        break
            if chosen_zi is None:
                chosen_zi = idx % len(valid)

            claimed_actor_idx.add(chosen_zi)
            chosen = valid[chosen_zi]
            other_valid = [z for zi, z in enumerate(valid) if zi != chosen_zi]
            other_alphas = [alphas[zi] for zi in range(len(valid)) if zi != chosen_zi]
            assignments[idx] = [chosen] + other_valid
            alpha_assignments[idx] = [alphas[chosen_zi]] + other_alphas
            self._comp_sticky_zone[idx] = chosen

        return assignments, alpha_assignments

    def _zones_for_component(
        self,
        comp: OverlayComponent,
        ctx: WindowContext,
        active_browser_ctx: BrowserContext | None,
        screen_w: float,
        screen_h: float,
        avail_h: float,
        comp_idx: int = 0,
        realtime_assignments: dict[int, list[ZoneRect]] | None = None,
        empty_space: EmptySpaceSnapshot | None = None,
    ) -> list[ZoneRect]:
        forbidden = self._current_forbidden_rects

        # Priority 1: Explicit user-drawn Allowed Zones for this AppProfile
        if self.active_profile and self.active_profile.custom_allowed_zones and not ctx.is_desktop:
            custom = _map_relative_boxes(
                self.active_profile.custom_allowed_zones, ctx, screen_w, avail_h
            )
            if custom:
                return _subtract_forbidden_from_zones(custom, forbidden)

        # Priority 2: Desktop freely uses the scenario's authored component zone
        if ctx.is_desktop:
            zones = compute_zones(
                comp.config.zone,
                screen_w=screen_w,
                screen_h=screen_h,
                avail_h=avail_h,
                window_rect=None,
            )
            return _subtract_forbidden_from_zones(zones, forbidden)

        # Priority 3: Universal Realtime Peripheral Empty-Space Scanner
        if realtime_assignments is not None:
            return realtime_assignments.get(comp_idx, [])
        if empty_space is not None and empty_space.hwnd == ctx.hwnd:
            if empty_space.status == "empty_none":
                return []
            if empty_space.status == "ok":
                mapped = _map_relative_boxes(empty_space.norm_rects, ctx, screen_w, avail_h)
                sub = _subtract_forbidden_from_zones(mapped, forbidden)
                valid = [z for z in sub if z.w >= 76.0 and z.h >= 52.0]
                if not valid:
                    return []
                return [valid[comp_idx % len(valid)]]

        # Priority 4: Safe Fallback when scanner is pending or unavailable
        if active_browser_ctx and active_browser_ctx.margins:
            wide_margins = [m for m in active_browser_ctx.margins if m.w >= 90.0 and m.h >= 80.0]
            if wide_margins:
                return _subtract_forbidden_from_zones(wide_margins, forbidden)

        if self.active_profile and self.active_profile.layout_wireframe:
            target_zone_type = (
                self.active_profile.preferred_zone
                if self.active_source != "pinned"
                else comp.config.zone
            )
            wf_zones = self._zones_from_wireframe(
                target_zone_type,
                self.active_profile.layout_wireframe,
                ctx,
                screen_w,
                avail_h,
            )
            if not wf_zones:
                wf_zones = self._zones_from_wireframe(
                    "left_right_margins",
                    self.active_profile.layout_wireframe,
                    ctx,
                    screen_w,
                    avail_h,
                )
            if wf_zones:
                return _subtract_forbidden_from_zones(wf_zones, forbidden)

        # Compact corners safe default (never blind wide left_right_margins on unknown apps)
        zones = compute_zones(
            "corners",
            screen_w=screen_w,
            screen_h=screen_h,
            avail_h=avail_h,
            window_rect=ctx.rect,
        )
        return _subtract_forbidden_from_zones(zones, forbidden)

    @staticmethod
    def _filter_browser_ctx(
        bctx: BrowserContext | None,
        forbidden: list[ZoneRect],
    ) -> BrowserContext | None:
        if bctx is None or not forbidden or not bctx.elements:
            return bctx
        filtered_els = [
            el
            for el in bctx.elements
            if not any(
                f.contains(el.x + el.w * 0.5, el.y + el.h * 0.5) for f in forbidden
            )
        ]
        return BrowserContext(
            host=bctx.host,
            url=bctx.url,
            title=bctx.title,
            is_scrolling=bctx.is_scrolling,
            elements=filtered_els,
            margins=bctx.margins,
            wireframe=bctx.wireframe,
            updated_at=bctx.updated_at,
        )

    def update(
        self,
        dt_sec: float,
        ctx: WindowContext,
        screen_w: float,
        screen_h: float,
        avail_h: float,
        browser_ctx: BrowserContext | None = None,
        is_audio_playing: bool = False,
        empty_space: EmptySpaceSnapshot | None = None,
    ) -> bool:
        """Advance engine state. Returns True if overlay needs repainting."""
        time_shift = self._current_time_shift()
        raw_ctx = ctx
        raw_browser_ctx = (
            browser_ctx
            if (browser_ctx and raw_ctx.process_name.lower() in COMMON_BROWSERS)
            else None
        )

        if (
            not raw_ctx.is_desktop
            and raw_ctx.process_name
            and raw_ctx.process_name.lower() not in _IGNORED_REAL_PROCS
        ):
            self.last_real_app_ctx = raw_ctx
            if raw_browser_ctx is not None:
                self.last_real_browser_ctx = raw_browser_ctx
            elif raw_ctx.process_name.lower() not in COMMON_BROWSERS:
                self.last_real_browser_ctx = None

        raw_profile = (
            match_profile_for_context(
                self.profiles,
                raw_ctx.process_name,
                raw_ctx.title,
                raw_browser_ctx.host if raw_browser_ctx else "",
            )
            if not raw_ctx.is_desktop
            else None
        )

        # Debounce rapid Alt-Tab hopping (0.25s settle window); immediate on first/forced/same-app updates
        sig = (
            "desktop" if raw_ctx.is_desktop else raw_ctx.process_name.lower(),
            raw_browser_ctx.host.lower() if raw_browser_ctx else "",
            raw_profile.id if raw_profile else "",
        )
        if self._force_commit_next or self._committed_ctx_sig is None or sig == self._committed_ctx_sig:
            self._committed_ctx_sig = sig
            self._committed_ctx = raw_ctx
            self._committed_browser_ctx = raw_browser_ctx
            self._committed_profile = raw_profile
            self._pending_ctx_sig = None
            self._pending_ctx_elapsed = 0.0
            self._force_commit_next = False
        else:
            if sig != self._pending_ctx_sig:
                self._pending_ctx_sig = sig
                self._pending_ctx_elapsed = dt_sec
            else:
                self._pending_ctx_elapsed += dt_sec
            if self._pending_ctx_elapsed >= self._app_switch_debounce_sec:
                self._committed_ctx_sig = sig
                self._committed_ctx = raw_ctx
                self._committed_browser_ctx = raw_browser_ctx
                self._committed_profile = raw_profile
                self._pending_ctx_sig = None
                self._pending_ctx_elapsed = 0.0

        eff_ctx = replace(
            self._committed_ctx or raw_ctx,
            cursor_pos=raw_ctx.cursor_pos,
            idle_seconds=raw_ctx.idle_seconds,
            is_fullscreen=raw_ctx.is_fullscreen,
        )
        eff_browser_ctx = self._committed_browser_ctx
        eff_profile = self._committed_profile

        self.active_profile = eff_profile
        self.last_process_name = eff_ctx.process_name or "Desktop"
        self.last_window_title = eff_ctx.title
        self.last_browser_host = eff_browser_ctx.host if eff_browser_ctx else ""

        if eff_profile and eff_profile.custom_forbidden_zones and not eff_ctx.is_desktop:
            self._current_forbidden_rects = _map_relative_boxes(
                eff_profile.custom_forbidden_zones, eff_ctx, screen_w, avail_h
            )
        else:
            self._current_forbidden_rects = []

        used_pinned = False
        if (
            eff_profile is not None
            and eff_profile.mode == "pinned"
            and eff_profile.pinned_components
        ):
            try:
                pinned_cfgs = [
                    ComponentConfig.model_validate(item)
                    for item in eff_profile.pinned_components
                ]
            except Exception:
                pinned_cfgs = []
            if pinned_cfgs:
                used_pinned = True
                self.active_source = "pinned"
                self.active_rule_name = eff_profile.pinned_theme_name or eff_profile.name
                self._switch_layer(
                    f"pinned:{eff_profile.id}:{self.active_rule_name}",
                    pinned_cfgs,
                    crossfade=True,
                )

        if not used_pinned:
            if eff_profile is not None and not eff_ctx.is_desktop:
                self.active_source = "daily_adapted"
                self.active_rule_name = eff_profile.name
            else:
                self.active_source = "global"
                self.active_rule_name = "Chung"
            self._switch_layer(
                self._daily_layer_key(),
                self.scenario.global_components,
                crossfade=True,
            )

        ctx_state, mod = self._resolve_context_modifier(raw_ctx, is_audio_playing)
        self.active_context_state = ctx_state

        eff_time_shift = TimeShift(
            brightness=max(0.65, min(1.2, time_shift.brightness)),
            speed=max(0.4, min(1.4, time_shift.speed * mod.speed_mult)),
        )

        # Fullscreen / Pause / Zen take effect immediately with 0ms delay
        if self.should_hide(raw_ctx):
            target_opacity = 0.0
        else:
            base = (
                max(0.70, self.scenario.palette.opacity_scale)
                * time_shift.brightness
                * mod.opacity_mult
                * self.user_opacity_mult
            )
            target_opacity = max(0.0, min(0.98, base))

        fade_rate = 4.0 if target_opacity < self.current_opacity else 2.4
        self.current_opacity += (target_opacity - self.current_opacity) * min(1.0, dt_sec * fade_rate)
        if abs(target_opacity - self.current_opacity) < 0.004:
            self.current_opacity = target_opacity

        if self.current_opacity <= 0.005:
            return False

        self._layer_fade_in = min(1.0, self._layer_fade_in + dt_sec * 2.2)
        if self._outgoing_layers:
            for layer in self._outgoing_layers:
                layer.opacity = max(0.0, layer.opacity - dt_sec * 2.6)
            self._outgoing_layers = [lay for lay in self._outgoing_layers if lay.opacity > 0.01]

        filtered_browser_ctx = self._filter_browser_ctx(
            eff_browser_ctx, self._current_forbidden_rects
        )

        realtime_assignments: dict[int, list[ZoneRect]] | None = None
        realtime_alphas: dict[int, list[float]] | None = None
        if eff_ctx.hwnd != self._last_realtime_hwnd:
            self._last_realtime_hwnd = eff_ctx.hwnd
            self._ordered_realtime_pockets.clear()

        if eff_profile and eff_profile.custom_allowed_zones and not eff_ctx.is_desktop:
            self.active_empty_status = "custom"
            self.active_empty_count = len(eff_profile.custom_allowed_zones)
        elif eff_ctx.is_desktop:
            self.active_empty_status = "desktop"
            self.active_empty_count = 0
        elif (
            empty_space is not None
            and empty_space.hwnd == eff_ctx.hwnd
            and empty_space.status in ("ok", "empty_none")
        ):
            if empty_space.status == "empty_none":
                realtime_assignments = {}
                realtime_alphas = {}
                self.active_empty_status = "realtime_packed"
                self.active_empty_count = 0
            else:
                wl, wt, win_w, win_h = _window_bounds(eff_ctx, screen_w, avail_h)
                occ_list = empty_space.occluded_flags
                raw_pairs: list[tuple[ZoneRect, bool]] = []
                for bi, box in enumerate(empty_space.norm_rects):
                    if len(box) != 4:
                        continue
                    rx, ry, rw, rh = box
                    zw, zh = rw * win_w, rh * win_h
                    if zw < 76.0 or zh < 52.0:
                        continue
                    zr = ZoneRect(x=wl + rx * win_w, y=wt + ry * win_h, w=zw, h=zh)
                    sub = _subtract_forbidden_from_zones([zr], self._current_forbidden_rects)
                    is_occ = bool(occ_list[bi]) if bi < len(occ_list) else False
                    for sz in sub:
                        if sz.w >= 76.0 and sz.h >= 52.0:
                            raw_pairs.append((sz, is_occ))

                if raw_pairs:
                    ordered_zones, ordered_occ = self._order_pockets_stably(raw_pairs)
                    pocket_alphas = [0.22 if occ else 1.0 for occ in ordered_occ]
                    realtime_assignments, realtime_alphas = self._assign_realtime_zones(
                        ordered_zones, pocket_alphas
                    )
                    self.active_empty_status = "realtime_ok"
                    self.active_empty_count = len(ordered_zones)
                else:
                    realtime_assignments = {}
                    realtime_alphas = {}
                    self.active_empty_status = "realtime_packed"
                    self.active_empty_count = 0
        else:
            self.active_empty_status = "fallback"
            self.active_empty_count = 0

        container_rect = _window_bounds(eff_ctx, screen_w, avail_h)
        for idx, comp in enumerate(self._components):
            zones = self._zones_for_component(
                comp,
                eff_ctx,
                eff_browser_ctx,
                screen_w,
                screen_h,
                avail_h,
                comp_idx=idx,
                realtime_assignments=realtime_assignments,
                empty_space=empty_space,
            )
            z_alphas = realtime_alphas.get(idx) if realtime_alphas is not None else None
            if isinstance(comp, AnimatedActorComponent):
                comp.update(
                    dt_sec,
                    eff_ctx,
                    zones,
                    eff_time_shift,
                    browser_ctx=filtered_browser_ctx,
                    behavior_override=mod.actor_behavior_override,
                    freq_mult=mod.cameo_freq_mult,
                    zone_alphas=z_alphas,
                    container_rect=container_rect,
                )
            else:
                sched = self._non_actor_schedulers.get(idx)
                if sched is not None:
                    cursor_near = bool(
                        comp.config.avoid_cursor
                        and zones
                        and zones[0].contains(eff_ctx.cursor_pos[0], eff_ctx.cursor_pos[1], padding=80.0)
                    )
                    sched.update(
                        dt=dt_sec,
                        idle_seconds=eff_ctx.idle_seconds,
                        cursor_near=cursor_near,
                        freq_mult=mod.cameo_freq_mult,
                    )
                if isinstance(comp, AssetSwarmComponent):
                    comp.update(
                        dt_sec,
                        eff_ctx,
                        zones,
                        eff_time_shift,
                        zone_alphas=z_alphas,
                        container_rect=container_rect,
                    )
                else:
                    comp.update(dt_sec, eff_ctx, zones, eff_time_shift)

        return True

    def paint(self, painter: QPainter) -> None:
        if self.current_opacity <= 0.005:
            return

        has_forbidden = bool(self._current_forbidden_rects)
        if has_forbidden:
            dev = painter.device()
            dpr = max(1.0, float(dev.devicePixelRatio())) if dev else 1.0
            vw = float(dev.width()) / dpr if dev else 4000.0
            vh = float(dev.height()) / dpr if dev else 4000.0
            clip_path = QPainterPath()
            clip_path.addRect(QRectF(0.0, 0.0, vw, vh))
            for f in self._current_forbidden_rects:
                sub = QPainterPath()
                sub.addRect(QRectF(f.x, f.y, f.w, f.h))
                clip_path = clip_path.subtracted(sub)
            painter.save()
            painter.setClipPath(clip_path)

        for layer in self._outgoing_layers:
            for idx, comp in enumerate(layer.components):
                sched = layer.non_actor_schedulers.get(idx)
                eff_alpha = (
                    self.current_opacity
                    * layer.opacity
                    * (sched.alpha if sched is not None else 1.0)
                )
                if eff_alpha > 0.01:
                    comp.paint(painter, eff_alpha)

        for idx, comp in enumerate(self._components):
            sched = self._non_actor_schedulers.get(idx)
            eff_alpha = (
                self.current_opacity
                * self._layer_fade_in
                * (sched.alpha if sched is not None else 1.0)
            )
            if eff_alpha > 0.01:
                comp.paint(painter, eff_alpha)

        if has_forbidden:
            painter.restore()
