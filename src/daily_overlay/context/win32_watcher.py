from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass

import psutil

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT),
        ("dwTime", wt.DWORD),
    ]


@dataclass
class WindowContext:
    hwnd: int
    process_name: str
    title: str
    rect: tuple[int, int, int, int]  # (left, top, right, bottom) in logical px
    is_fullscreen: bool
    is_desktop: bool
    cursor_pos: tuple[float, float]
    idle_seconds: float


class Win32ContextWatcher:
    """Lightweight Win32 foreground-window, fullscreen, and idle detector."""

    def __init__(self, own_hwnd: int = 0) -> None:
        self._own_hwnd = own_hwnd
        self._pid_cache: dict[int, str] = {}

    def set_own_hwnd(self, hwnd: int) -> None:
        self._own_hwnd = hwnd

    def _get_process_name(self, pid: int) -> str:
        if pid <= 0:
            return ""
        cached = self._pid_cache.get(pid)
        if cached is not None:
            return cached
        try:
            name = psutil.Process(pid).name().lower()
        except Exception:
            name = ""
        if len(self._pid_cache) > 128:
            self._pid_cache.clear()
        self._pid_cache[pid] = name
        return name

    @staticmethod
    def _get_window_title(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    @staticmethod
    def _get_class_name(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, buf, 128)
        return buf.value

    @staticmethod
    def _get_idle_seconds() -> float:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        tick_now = kernel32.GetTickCount()
        elapsed_ms = (tick_now - lii.dwTime) & 0xFFFFFFFF
        return elapsed_ms / 1000.0

    def poll(
        self,
        screen_w: int,
        screen_h: int,
        dpr: float = 1.0,
        cursor_pos: tuple[float, float] = (0.0, 0.0),
    ) -> WindowContext:
        hwnd = int(user32.GetForegroundWindow() or 0)
        idle_sec = self._get_idle_seconds()

        if hwnd == 0 or hwnd == self._own_hwnd:
            return WindowContext(
                hwnd=hwnd,
                process_name="",
                title="",
                rect=(0, 0, screen_w, screen_h),
                is_fullscreen=False,
                is_desktop=True,
                cursor_pos=cursor_pos,
                idle_seconds=idle_sec,
            )

        cls_name = self._get_class_name(hwnd)
        is_desktop = cls_name in SHELL_CLASSES

        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = self._get_process_name(pid.value)
        title = self._get_window_title(hwnd)

        raw_rect = wt.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(raw_rect)):
            scale = dpr if dpr > 0 else 1.0
            left = int(raw_rect.left / scale)
            top = int(raw_rect.top / scale)
            right = int(raw_rect.right / scale)
            bottom = int(raw_rect.bottom / scale)
        else:
            left, top, right, bottom = 0, 0, screen_w, screen_h

        # Detect true fullscreen (covers the entire screen, excluding desktop shell)
        tol = 4
        covers_screen = (
            left <= tol
            and top <= tol
            and right >= screen_w - tol
            and bottom >= screen_h - tol
        )
        is_fullscreen = covers_screen and not is_desktop and proc_name != "explorer.exe"

        return WindowContext(
            hwnd=hwnd,
            process_name=proc_name,
            title=title,
            rect=(left, top, right, bottom),
            is_fullscreen=is_fullscreen,
            is_desktop=is_desktop,
            cursor_pos=cursor_pos,
            idle_seconds=idle_sec,
        )
