from __future__ import annotations

import datetime as dt
import time

from PySide6.QtGui import QPainter

from daily_overlay.ai.schema import AppRule, DailyScenario, TimeShift
from daily_overlay.config import COMMON_BROWSERS
from daily_overlay.context.browser_bridge import BrowserContext
from daily_overlay.context.win32_watcher import WindowContext
from daily_overlay.context.zones import compute_zones
from daily_overlay.overlay.components import (
    CatPetComponent,
    OverlayComponent,
    create_component,
)


class OverlayEngine:
    """Coordinates active scenario, app-context rule matching, Browser Bridge DOM boxes, and auto-fade."""

    def __init__(self, scenario: DailyScenario) -> None:
        self.scenario = scenario
        self.paused: bool = False
        self.zen_until: float = 0.0
        self.user_opacity_mult: float = 1.0  # 0.65 (Nhẹ), 1.0 (Vừa), 1.25 (Rõ)
        self.current_opacity: float = 0.0
        self.active_rule_name: str = "Global"
        self.last_process_name: str = ""
        self.last_window_title: str = ""
        self.last_browser_host: str = ""
        self._active_key: str = ""
        self._components: list[OverlayComponent] = []
        self._rebuild_components("global", scenario.global_components)

    def set_scenario(self, scenario: DailyScenario) -> None:
        self.scenario = scenario
        self._active_key = ""
        self.active_rule_name = "Global"
        self._rebuild_components("global", scenario.global_components)

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
        if 5 <= hour < 12:
            return self.scenario.time_shifts.morning
        if 12 <= hour < 18:
            return self.scenario.time_shifts.afternoon
        return self.scenario.time_shifts.night

    def _match_rule(
        self,
        ctx: WindowContext,
        browser_ctx: BrowserContext | None,
    ) -> AppRule | None:
        if ctx.is_desktop or not ctx.process_name:
            return None

        proc = ctx.process_name.lower()
        is_browser = proc in COMMON_BROWSERS
        title_lower = ctx.title.lower()
        if is_browser and browser_ctx:
            title_lower = f"{title_lower} {browser_ctx.host.lower()} {browser_ctx.title.lower()}"

        # 1. Prefer rules matching process AND title/host keyword
        for rule in self.scenario.app_rules:
            procs = [p.lower() for p in rule.match_process]
            titles = [t.lower() for t in rule.match_title_contains if t.strip()]
            proc_ok = (not procs) or (proc in procs)
            if proc_ok and titles and any(t in title_lower for t in titles):
                return rule

        # 2. Next, rules matching process without title constraints
        for rule in self.scenario.app_rules:
            procs = [p.lower() for p in rule.match_process]
            titles = [t.lower() for t in rule.match_title_contains if t.strip()]
            if procs and proc in procs and not titles:
                return rule

        return None

    def _rebuild_components(self, key: str, configs) -> None:
        if key == self._active_key:
            return
        self._active_key = key
        self._components = [create_component(cfg, self.scenario.palette) for cfg in configs]

    def should_hide(self, ctx: WindowContext) -> bool:
        return self.paused or self.is_zen_active or ctx.is_fullscreen

    def update(
        self,
        dt_sec: float,
        ctx: WindowContext,
        screen_w: float,
        screen_h: float,
        avail_h: float,
        browser_ctx: BrowserContext | None = None,
    ) -> bool:
        """Advance engine state. Returns True if overlay needs repainting."""
        time_shift = self._current_time_shift()
        self.last_process_name = ctx.process_name or "Desktop"
        self.last_window_title = ctx.title

        # Only apply Browser Extension DOM boxes when a browser is actually focused
        active_browser_ctx = (
            browser_ctx
            if (browser_ctx and ctx.process_name.lower() in COMMON_BROWSERS)
            else None
        )
        self.last_browser_host = active_browser_ctx.host if active_browser_ctx else ""

        rule = self._match_rule(ctx, active_browser_ctx)
        if rule and rule.components:
            self.active_rule_name = rule.rule_name
            self._rebuild_components(f"rule:{rule.rule_name}", rule.components)
        else:
            self.active_rule_name = "Global"
            self._rebuild_components("global", self.scenario.global_components)

        if self.should_hide(ctx):
            target_opacity = 0.0
        else:
            base = (
                max(0.70, self.scenario.palette.opacity_scale)
                * time_shift.brightness
                * self.user_opacity_mult
            )
            if not ctx.is_desktop and ctx.idle_seconds < 1.2:
                base *= 0.88
            target_opacity = max(0.0, min(0.98, base))

        fade_rate = 4.0 if target_opacity < self.current_opacity else 2.4
        self.current_opacity += (target_opacity - self.current_opacity) * min(1.0, dt_sec * fade_rate)
        if abs(target_opacity - self.current_opacity) < 0.004:
            self.current_opacity = target_opacity

        if self.current_opacity <= 0.005:
            return False

        for comp in self._components:
            # Prefer exact DOM margins from Browser Extension when available (e.g. Google Docs)
            if (
                comp.config.zone == "left_right_margins"
                and active_browser_ctx
                and active_browser_ctx.margins
            ):
                zones = active_browser_ctx.margins
            else:
                zones = compute_zones(
                    comp.config.zone,
                    screen_w=screen_w,
                    screen_h=screen_h,
                    avail_h=avail_h,
                    window_rect=None if ctx.is_desktop else ctx.rect,
                )

            if isinstance(comp, CatPetComponent):
                comp.update(dt_sec, ctx, zones, time_shift, browser_ctx=active_browser_ctx)
            else:
                comp.update(dt_sec, ctx, zones, time_shift)

        return True

    def paint(self, painter: QPainter) -> None:
        if self.current_opacity <= 0.005:
            return
        for comp in self._components:
            comp.paint(painter, self.current_opacity)
