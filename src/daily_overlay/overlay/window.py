from __future__ import annotations

import ctypes
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from daily_overlay.config import IDLE_POLL_MS, TARGET_FPS
from daily_overlay.context.browser_bridge import BrowserBridgeServer
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
    ) -> None:
        super().__init__(None)
        self.engine = engine
        self.browser_bridge = browser_bridge
        self.watcher = Win32ContextWatcher()

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
        self.watcher.set_own_hwnd(hwnd)
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
        else:
            self._last_ctx.cursor_pos = cursor

        browser_ctx = self.browser_bridge.get_context() if self.browser_bridge else None

        needs_paint = self.engine.update(
            dt_sec=dt_sec,
            ctx=self._last_ctx,
            screen_w=float(geo.width()),
            screen_h=float(geo.height()),
            avail_h=float(avail.height()),
            browser_ctx=browser_ctx,
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
