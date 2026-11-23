from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
from dataclasses import dataclass

import psutil

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Explicit 64-bit safe Win32 signatures
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

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
        self._own_pid = os.getpid()
        self._own_hwnds: set[int] = set()
        if own_hwnd:
            self._own_hwnds.add(own_hwnd)
        self._pid_cache: dict[int, str] = {}

    def add_own_hwnd(self, hwnd: int) -> None:
        if hwnd:
            self._own_hwnds.add(hwnd)

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

    def _ignored_context(
        self,
        hwnd: int,
        screen_w: int,
        screen_h: int,
        cursor_pos: tuple[float, float],
        idle_sec: float,
    ) -> WindowContext:
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

    def poll(
        self,
        screen_w: int,
        screen_h: int,
        dpr: float = 1.0,
        cursor_pos: tuple[float, float] = (0.0, 0.0),
    ) -> WindowContext:
        hwnd = int(user32.GetForegroundWindow() or 0)
        idle_sec = self._get_idle_seconds()

        if hwnd == 0 or hwnd in self._own_hwnds:
            return self._ignored_context(hwnd, screen_w, screen_h, cursor_pos, idle_sec)

        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        # Never treat DailyOverlay's own dialogs, menus, or zone editor as an external app
        if pid.value == self._own_pid:
            return self._ignored_context(hwnd, screen_w, screen_h, cursor_pos, idle_sec)

        cls_name = self._get_class_name(hwnd)
        is_desktop = cls_name in SHELL_CLASSES

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
