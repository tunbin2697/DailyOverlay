from __future__ import annotations

import ctypes
import time
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from daily_overlay.config import IDLE_POLL_MS, TARGET_FPS, current_cycle_slot, cycle_key
from daily_overlay.context.audio_watcher import WindowsAudioWatcher
from daily_overlay.context.browser_bridge import BrowserBridgeServer
from daily_overlay.context.empty_space import RealtimeEmptySpaceWatcher
from daily_overlay.context.win32_watcher import Win32ContextWatcher, WindowContext
from daily_overlay.overlay.engine import OverlayEngine

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


class OverlayWindow(QWidget):
    """Full-screen transparent, click-through, topmost ambient overlay window."""

    def __init__(
        self,
        engine: OverlayEngine,
        browser_bridge: BrowserBridgeServer | None = None,
        on_fullscreen_changed: Callable[[bool], None] | None = None,
        on_new_day: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(None)
        self.engine = engine
        self.browser_bridge = browser_bridge
        self.on_fullscreen_changed = on_fullscreen_changed
        self.on_new_day = on_new_day

        self.watcher = Win32ContextWatcher()
        self.audio_watcher = WindowsAudioWatcher()
        self.empty_watcher = RealtimeEmptySpaceWatcher()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._sync_screen_geometry()
        self._last_frame_time = time.monotonic()
        self._last_ctx_poll = 0.0
        self._last_audio_poll = 0.0
        self._last_day_check = time.monotonic()
        self._is_audio_playing = False
        self._last_fullscreen_state = False
        self._last_ctx: WindowContext | None = None
        self._active_interval_ms = max(16, int(1000 / TARGET_FPS))

        self._timer = QTimer(self)
        self._timer.setInterval(self._active_interval_ms)
        self._timer.timeout.connect(self._on_tick)

    def _sync_screen_geometry(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.geometry()
        self.setGeometry(geo)

    def _apply_win32_click_through(self) -> None:
        hwnd = int(self.winId())
        self.watcher.add_own_hwnd(hwnd)
        user32 = ctypes.windll.user32
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        new_style = (
            ex_style
            | WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOOLWINDOW
            | WS_EX_NOACTIVATE
        )
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)

    def start(self) -> None:
        self.empty_watcher.start()
        self.show()
        self._apply_win32_click_through()
        self._last_frame_time = time.monotonic()
        self._timer.start()

    def _on_tick(self) -> None:
        now = time.monotonic()
        dt_sec = min(0.1, max(0.001, now - self._last_frame_time))
        self._last_frame_time = now

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.geometry()
        avail = screen.availableGeometry()
        dpr = screen.devicePixelRatio()

        pos = QCursor.pos()
        cursor = (float(pos.x() - geo.x()), float(pos.y() - geo.y()))

        if self._last_ctx is None or (now - self._last_ctx_poll) >= 0.25:
            self._last_ctx = self.watcher.poll(
                screen_w=geo.width(),
                screen_h=geo.height(),
                dpr=dpr,
                cursor_pos=cursor,
            )
            self._last_ctx_poll = now
            if self._last_ctx.is_fullscreen != self._last_fullscreen_state:
                self._last_fullscreen_state = self._last_ctx.is_fullscreen
                if self.on_fullscreen_changed:
                    self.on_fullscreen_changed(self._last_fullscreen_state)
            self.empty_watcher.set_target(
                hwnd=self._last_ctx.hwnd,
                rect=self._last_ctx.rect,
                is_desktop=self._last_ctx.is_desktop,
                active=not self.engine.should_hide(self._last_ctx),
                idle_seconds=self._last_ctx.idle_seconds,
            )
        else:
            self._last_ctx.cursor_pos = cursor

        if (now - self._last_audio_poll) >= 0.5:
            self._is_audio_playing, _ = self.audio_watcher.poll(now)
            self._last_audio_poll = now

        if (now - self._last_day_check) >= 30.0:
            self._last_day_check = now
            slot_day, slot_period = current_cycle_slot()
            if cycle_key(slot_day, slot_period) != self.engine.deck.cycle_key and self.on_new_day:
                self.on_new_day()

        browser_ctx = self.browser_bridge.get_context() if self.browser_bridge else None
        empty_space = self.empty_watcher.get_snapshot(self._last_ctx.hwnd) if self._last_ctx else None

        needs_paint = self.engine.update(
            dt_sec=dt_sec,
            ctx=self._last_ctx,
            screen_w=float(geo.width()),
            screen_h=float(geo.height()),
            avail_h=float(avail.height()),
            browser_ctx=browser_ctx,
            is_audio_playing=self._is_audio_playing,
            empty_space=empty_space,
        )

        if not needs_paint:
            if self._timer.interval() != IDLE_POLL_MS:
                self._timer.setInterval(IDLE_POLL_MS)
                self.update()
        else:
            if self._timer.interval() != self._active_interval_ms:
                self._timer.setInterval(self._active_interval_ms)
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.engine.paint(painter)
        painter.end()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        self.empty_watcher.stop()
        super().closeEvent(event)
