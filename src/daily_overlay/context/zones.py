from __future__ import annotations

from dataclasses import dataclass

from daily_overlay.config import ZoneType


@dataclass(frozen=True)
class ZoneRect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def contains(self, px: float, py: float, padding: float = 0.0) -> bool:
        return (
            self.x - padding <= px <= self.right + padding
            and self.y - padding <= py <= self.bottom + padding
        )


def compute_zones(
    zone: ZoneType,
    screen_w: float,
    screen_h: float,
    avail_h: float,
    window_rect: tuple[int, int, int, int] | None = None,
) -> list[ZoneRect]:
    """Compute safe drawing rectangles for a named zone."""
    taskbar_y = min(avail_h, screen_h)

    if zone == "bottom_edge":
        h = max(72.0, min(120.0, screen_h * 0.11))
        return [ZoneRect(0.0, max(0.0, taskbar_y - h), screen_w, h)]

    if zone == "top_edge":
        h = max(56.0, min(90.0, screen_h * 0.08))
        return [ZoneRect(0.0, 0.0, screen_w, h)]

    if zone == "right_edge":
        w = max(110.0, min(190.0, screen_w * 0.11))
        top_pad = 48.0
        return [ZoneRect(screen_w - w, top_pad, w, max(120.0, taskbar_y - top_pad))]

    if zone == "corners":
        cw = max(180.0, min(280.0, screen_w * 0.16))
        ch = max(140.0, min(220.0, screen_h * 0.18))
        return [
            ZoneRect(0.0, max(0.0, taskbar_y - ch), cw, ch),
            ZoneRect(screen_w - cw, max(0.0, taskbar_y - ch), cw, ch),
        ]

    if zone == "left_right_margins":
        # Ideal for Google Docs / Notion / reading pages with empty side margins
        if window_rect is not None:
            wl, wt, wr, wb = window_rect
            wl = max(0, wl)
            wt = max(0, wt)
            wr = min(int(screen_w), wr)
            wb = min(int(taskbar_y), wb)
            win_w = wr - wl
            win_h = wb - wt
        else:
            wl, wt, wr, wb = 0, 0, int(screen_w), int(taskbar_y)
            win_w, win_h = int(screen_w), int(taskbar_y)

        if win_w >= 720 and win_h >= 360:
            margin_w = max(95.0, min(220.0, win_w * 0.15))
            top_chrome = 120.0  # Stay clear of browser tabs & Docs toolbar
            usable_h = max(160.0, win_h - top_chrome - 24.0)
            y0 = wt + top_chrome
            return [
                ZoneRect(float(wl + 6), y0, margin_w, usable_h),
                ZoneRect(float(wr - margin_w - 6), y0, margin_w, usable_h),
            ]

        # Fallback if window is small
        margin_w = max(90.0, screen_w * 0.12)
        return [
            ZoneRect(0.0, 90.0, margin_w, max(160.0, taskbar_y - 120.0)),
            ZoneRect(screen_w - margin_w, 90.0, margin_w, max(160.0, taskbar_y - 120.0)),
        ]

    return [ZoneRect(0.0, max(0.0, taskbar_y - 100.0), screen_w, 100.0)]
