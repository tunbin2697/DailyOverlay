from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from PySide6.QtGui import QImage

EmptySpaceStatus = Literal["ok", "empty_none", "unavailable"]

PW_RENDERFULLCONTENT = 0x00000002
HALFTONE = 4
SRCCOPY = 0x00CC0020

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.PrintWindow.restype = wintypes.BOOL
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = ctypes.c_int

_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
_gdi32.SetStretchBltMode.restype = ctypes.c_int
_gdi32.StretchBlt.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]
_gdi32.StretchBlt.restype = wintypes.BOOL


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


_gdi32.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(_BITMAPINFOHEADER),
    wintypes.UINT,
]
_gdi32.GetDIBits.restype = ctypes.c_int


@dataclass
class EmptySpaceSnapshot:
    """Zero-PII snapshot describing real peripheral empty pockets inside an active window."""

    hwnd: int
    status: EmptySpaceStatus
    norm_rects: list[list[float]] = field(default_factory=list)
    occluded_flags: list[bool] = field(default_factory=list)
    updated_at: float = 0.0


def capture_window_grayscale(
    hwnd: int,
    target_w: int = 320,
    target_h: int = 184,
) -> tuple[bytes, int, int, int] | None:
    """Capture target window off-screen via Win32 PrintWindow and return tiny grayscale bytes in RAM."""
    if not hwnd:
        return None
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    win_w = rect.right - rect.left
    win_h = rect.bottom - rect.top
    if win_w < 120 or win_h < 120:
        return None

    hdc_src = _user32.GetDC(0)
    if not hdc_src:
        return None

    hdc_full = None
    hbmp_full = None
    old_full = None
    hdc_small = None
    hbmp_small = None
    old_small = None
    try:
        hdc_full = _gdi32.CreateCompatibleDC(hdc_src)
        hbmp_full = _gdi32.CreateCompatibleBitmap(hdc_src, win_w, win_h)
        if not hdc_full or not hbmp_full:
            return None
        old_full = _gdi32.SelectObject(hdc_full, hbmp_full)

        if not _user32.PrintWindow(hwnd, hdc_full, PW_RENDERFULLCONTENT):
            return None

        hdc_small = _gdi32.CreateCompatibleDC(hdc_src)
        hbmp_small = _gdi32.CreateCompatibleBitmap(hdc_src, target_w, target_h)
        if not hdc_small or not hbmp_small:
            return None
        old_small = _gdi32.SelectObject(hdc_small, hbmp_small)

        _gdi32.SetStretchBltMode(hdc_small, HALFTONE)
        _gdi32.StretchBlt(
            hdc_small,
            0,
            0,
            target_w,
            target_h,
            hdc_full,
            0,
            0,
            win_w,
            win_h,
            SRCCOPY,
        )

        bmi = _BITMAPINFOHEADER(
            biSize=ctypes.sizeof(_BITMAPINFOHEADER),
            biWidth=target_w,
            biHeight=-target_h,
            biPlanes=1,
            biBitCount=32,
        )
        buf = (ctypes.c_ubyte * (target_w * target_h * 4))()
        lines = _gdi32.GetDIBits(
            hdc_small,
            hbmp_small,
            0,
            target_h,
            buf,
            ctypes.byref(bmi),
            0,
        )
        if lines <= 0:
            return None

        raw_bgra = bytes(buf)
        qimg = QImage(raw_bgra, target_w, target_h, target_w * 4, QImage.Format.Format_RGB32)
        gray = qimg.convertToFormat(QImage.Format.Format_Grayscale8)
        gray_bytes = bytes(gray.constBits())
        return gray_bytes, target_w, target_h, gray.bytesPerLine()
    finally:
        if hdc_small and old_small:
            _gdi32.SelectObject(hdc_small, old_small)
        if hbmp_small:
            _gdi32.DeleteObject(hbmp_small)
        if hdc_small:
            _gdi32.DeleteDC(hdc_small)
        if hdc_full and old_full:
            _gdi32.SelectObject(hdc_full, old_full)
        if hbmp_full:
            _gdi32.DeleteObject(hbmp_full)
        if hdc_full:
            _gdi32.DeleteDC(hdc_full)
        _user32.ReleaseDC(0, hdc_src)


def detect_peripheral_empty_rects(
    gray_bytes: bytes | memoryview,
    width: int,
    height: int,
    stride: int | None = None,
    max_rects: int = 4,
) -> list[list[float]] | None:
    """Detect peripheral empty rectangles in normalized 0..1 coordinates.

    Returns:
        - None when capture buffer looks blank/all-black (DRM/protected/failed PrintWindow)
        - [] when valid UI is packed with no peripheral empty space
        - list of [rx, ry, rw, rh] scored by corner/edge priority
    """
    stride = stride or width
    mv = memoryview(gray_bytes)
    if width < 64 or height < 64 or len(mv) < stride * (height - 1) + width:
        return None

    # Blank-capture guard
    sample_step = max(1, len(mv) // 480)
    if max(mv[::sample_step]) <= 2:
        return None

    cell = 8
    cols = width // cell
    rows = height // cell
    if cols < 8 or rows < 8:
        return None

    empty = [[False] * cols for _ in range(rows)]
    for r in range(1, rows - 1):  # exclude title/menu row & status/taskbar row
        y0 = r * cell
        row_slices = [mv[yy * stride : yy * stride + width] for yy in range(y0, y0 + cell)]
        for c in range(cols):
            x0 = c * cell
            x1 = x0 + cell
            mn = 255
            mx = 0
            for rs in row_slices:
                sub = rs[x0:x1]
                lo = min(sub)
                hi = max(sub)
                if lo < mn:
                    mn = lo
                if hi > mx:
                    mx = hi
                if mx - mn > 16:
                    break
            if mx - mn <= 16:
                empty[r][c] = True

    # 4-connected safety erosion + Central Workspace & Top-Center Sanctuary masks
    safe = [[False] * cols for _ in range(rows)]
    for r in range(1, rows - 1):
        cy = (r + 0.5) / rows
        for c in range(cols):
            if not empty[r][c]:
                continue
            cx = (c + 0.5) / cols
            if 0.22 <= cx <= 0.78 and 0.16 <= cy <= 0.84:
                continue
            if 0.25 <= cx <= 0.75 and cy < 0.16:
                continue
            if (
                empty[r - 1][c]
                and empty[r + 1][c]
                and (c == 0 or empty[r][c - 1])
                and (c == cols - 1 or empty[r][c + 1])
            ):
                safe[r][c] = True

    grid = [row[:] for row in safe]
    rects: list[list[float]] = []
    for _ in range(max_rects):
        heights = [0] * cols
        best_score = 0.0
        best_rect: tuple[int, int, int, int] | None = None

        for r in range(rows):
            for c in range(cols):
                heights[c] = heights[c] + 1 if grid[r][c] else 0
            stack: list[tuple[int, int]] = []
            for c in range(cols + 1):
                h = heights[c] if c < cols else 0
                start = c
                while stack and stack[-1][1] > h:
                    idx, hh = stack.pop()
                    ww = c - idx
                    area = ww * hh
                    if ww >= 3 and hh >= 2 and area >= 8:
                        ry0 = r - hh + 1
                        rx_c = (idx + ww * 0.5) / cols
                        ry_c = (ry0 + hh * 0.5) / rows
                        edge_x = abs(rx_c - 0.5) * 2.0
                        edge_y = abs(ry_c - 0.5) * 2.0
                        if edge_x >= 0.48 or ry_c >= 0.80:
                            corner_bonus = 1.55 if (edge_x >= 0.55 and edge_y >= 0.45) else 1.0
                            score = (
                                area
                                * (0.4 + 1.1 * max(edge_x, edge_y) + 0.7 * min(edge_x, edge_y))
                                * corner_bonus
                            )
                            if score > best_score:
                                best_score = score
                                best_rect = (idx, ry0, ww, hh)
                    start = idx
                stack.append((start, h))

        if best_rect is None:
            break
        rx, ry, rw, rh = best_rect
        rects.append(
            [
                round(rx / cols, 4),
                round(ry / rows, 4),
                round(rw / cols, 4),
                round(rh / rows, 4),
            ]
        )
        for rr in range(ry, ry + rh):
            for cc in range(rx, rx + rw):
                grid[rr][cc] = False

    return rects


def _rect_iou(a: list[float], b: list[float]) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax0 + aw, bx0 + bw)
    iy1 = min(ay0 + ah, by0 + bh)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = aw * ah + bw * bh - inter
    return inter / max(1e-6, union)


def _center_dist(a: list[float], b: list[float]) -> float:
    ax = a[0] + a[2] * 0.5
    ay = a[1] + a[3] * 0.5
    bx = b[0] + b[2] * 0.5
    by = b[1] + b[3] * 0.5
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _rects_match(a: list[float], b: list[float]) -> bool:
    if _rect_iou(a, b) >= 0.35:
        return True
    area_a = max(1e-6, a[2] * a[3])
    area_b = max(1e-6, b[2] * b[3])
    ratio = area_b / area_a
    return _center_dist(a, b) <= 0.10 and 0.45 <= ratio <= 2.2


@dataclass
class _StablePocket:
    rect: list[float]
    occluded_since: float | None = None


@dataclass
class _PendingPocket:
    rect: list[float]
    first_seen_at: float


@dataclass
class _FilterState:
    initialized: bool = False
    stable_entries: list[_StablePocket] = field(default_factory=list)
    pending_candidates: list[_PendingPocket] = field(default_factory=list)
    last_relocate_at: float = 0.0


class TemporalEmptySpaceFilter:
    """Global delay buffer: keeps active pockets sticky during temporary UI occlusions (1.8s grace),
    dims occluded slots in place, requires 1.5s settle on new pockets, and enforces a 3.0s relocation cooldown.
    """

    def __init__(
        self,
        max_rects: int = 4,
        grace_sec: float = 1.8,
        settle_sec: float = 1.5,
        relocate_cooldown_sec: float = 3.0,
    ) -> None:
        self._max_rects = max_rects
        self._grace_sec = grace_sec
        self._settle_sec = settle_sec
        self._relocate_cooldown_sec = relocate_cooldown_sec
        self._states: dict[int, _FilterState] = {}

    def reset(self, hwnd: int) -> None:
        self._states.pop(hwnd, None)

    def update(
        self,
        hwnd: int,
        new_rects: list[list[float]],
        now: float | None = None,
    ) -> tuple[list[list[float]], list[bool]]:
        ts = time.monotonic() if now is None else now
        st = self._states.get(hwnd)
        if st is None:
            st = _FilterState()
            self._states[hwnd] = st

        if not st.initialized:
            st.initialized = True
            st.stable_entries = [_StablePocket(rect=list(r)) for r in new_rects[: self._max_rects]]
            st.pending_candidates = []
            st.last_relocate_at = ts
            return (
                [list(e.rect) for e in st.stable_entries],
                [False] * len(st.stable_entries),
            )

        next_stable: list[_StablePocket] = []
        used_new: set[int] = set()
        relocated = False

        # 1. Match existing stable pockets; apply 1.8s grace buffer before evicting occluded pockets
        for old in st.stable_entries:
            best_idx = -1
            best_iou = -1.0
            for idx, nr in enumerate(new_rects):
                if idx in used_new or not _rects_match(old.rect, nr):
                    continue
                iou = _rect_iou(old.rect, nr)
                if iou > best_iou:
                    best_iou = iou
                    best_idx = idx
            if best_idx >= 0:
                next_stable.append(_StablePocket(rect=list(new_rects[best_idx]), occluded_since=None))
                used_new.add(best_idx)
            else:
                occ_since = ts if old.occluded_since is None else old.occluded_since
                if (ts - occ_since) < self._grace_sec:
                    next_stable.append(_StablePocket(rect=list(old.rect), occluded_since=occ_since))
                else:
                    relocated = True

        # 2. Track newly appearing pockets with first_seen_at timestamps
        next_pending: list[_PendingPocket] = []
        for idx, nr in enumerate(new_rects):
            if idx in used_new:
                continue
            existing_first: float | None = None
            for pend in st.pending_candidates:
                if _rects_match(nr, pend.rect):
                    existing_first = pend.first_seen_at
                    break
            next_pending.append(
                _PendingPocket(
                    rect=list(nr),
                    first_seen_at=existing_first if existing_first is not None else ts,
                )
            )

        # 3. Promote settled candidates (respecting 1.5s settle & 3.0s relocation cooldown)
        remaining_pending: list[_PendingPocket] = []
        for cand in next_pending:
            settled = (ts - cand.first_seen_at) >= self._settle_sec
            cooldown_ok = (
                (not next_stable)
                or relocated
                or (ts - st.last_relocate_at) >= self._relocate_cooldown_sec
            )
            if len(next_stable) < self._max_rects and settled and cooldown_ok:
                next_stable.append(_StablePocket(rect=list(cand.rect), occluded_since=None))
                relocated = True
            else:
                remaining_pending.append(cand)

        if relocated:
            st.last_relocate_at = ts

        st.stable_entries = next_stable[: self._max_rects]
        st.pending_candidates = remaining_pending
        return (
            [list(e.rect) for e in st.stable_entries],
            [e.occluded_since is not None for e in st.stable_entries],
        )


class RealtimeEmptySpaceWatcher:
    """Low-overhead background worker scanning the foreground window for peripheral empty pockets."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self._thread: threading.Thread | None = None

        self._target_hwnd: int = 0
        self._target_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._is_desktop: bool = True
        self._active: bool = False
        self._idle_seconds: float = 0.0
        self._needs_initial_settle: bool = False
        self._target_changed_at: float = 0.0

        self._filter = TemporalEmptySpaceFilter()
        self._snapshots: dict[int, EmptySpaceSnapshot] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(
            target=self._run,
            name="RealtimeEmptySpaceWatcher",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None

    def set_target(
        self,
        hwnd: int,
        rect: tuple[int, int, int, int],
        is_desktop: bool,
        active: bool,
        idle_seconds: float,
    ) -> None:
        wake_needed = False
        with self._lock:
            old_w = self._target_rect[2] - self._target_rect[0]
            old_h = self._target_rect[3] - self._target_rect[1]
            new_w = rect[2] - rect[0]
            new_h = rect[3] - rect[1]

            hwnd_changed = hwnd != self._target_hwnd or is_desktop != self._is_desktop
            size_changed = abs(new_w - old_w) >= 24 or abs(new_h - old_h) >= 24

            self._target_hwnd = hwnd
            self._target_rect = rect
            self._is_desktop = is_desktop
            self._active = active
            self._idle_seconds = idle_seconds

            if hwnd_changed or size_changed:
                if hwnd_changed and hwnd:
                    self._filter.reset(hwnd)
                self._needs_initial_settle = True
                self._target_changed_at = time.monotonic()
                wake_needed = True

        if wake_needed:
            self._wake.set()

    def get_snapshot(self, hwnd: int) -> EmptySpaceSnapshot | None:
        if not hwnd:
            return None
        with self._lock:
            return self._snapshots.get(hwnd)

    def _run(self) -> None:
        while not self._stop:
            with self._lock:
                hwnd = self._target_hwnd
                is_desktop = self._is_desktop
                active = self._active
                idle_sec = self._idle_seconds
                settle = self._needs_initial_settle
                changed_at = self._target_changed_at

            if not active or is_desktop or not hwnd:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue

            if settle:
                rem = 0.25 - (time.monotonic() - changed_at)
                if rem > 0.01:
                    self._wake.wait(timeout=rem)
                    self._wake.clear()
                    continue
                with self._lock:
                    self._needs_initial_settle = False

            cap = capture_window_grayscale(hwnd)
            now = time.monotonic()
            if cap is None:
                snap = EmptySpaceSnapshot(hwnd=hwnd, status="unavailable", updated_at=now)
            else:
                gray_bytes, gw, gh, stride = cap
                raw = detect_peripheral_empty_rects(gray_bytes, gw, gh, stride)
                if raw is None:
                    snap = EmptySpaceSnapshot(hwnd=hwnd, status="unavailable", updated_at=now)
                else:
                    filtered, occluded_flags = self._filter.update(hwnd, raw, now=now)
                    snap = EmptySpaceSnapshot(
                        hwnd=hwnd,
                        status="ok" if filtered else "empty_none",
                        norm_rects=filtered,
                        occluded_flags=occluded_flags,
                        updated_at=now,
                    )

            with self._lock:
                self._snapshots[hwnd] = snap
                if len(self._snapshots) > 16:
                    oldest_key = min(self._snapshots, key=lambda k: self._snapshots[k].updated_at)
                    self._snapshots.pop(oldest_key, None)

            interval = 0.50 if idle_sec <= 12.0 else 3.0
            self._wake.wait(timeout=interval)
            self._wake.clear()
