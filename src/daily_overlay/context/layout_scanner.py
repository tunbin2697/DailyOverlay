from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import uuid
from ctypes import POINTER, byref, c_int, c_long, c_void_p

ole32 = ctypes.windll.ole32
user32 = ctypes.windll.user32
user32.GetWindowRect.argtypes = [wt.HWND, POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, text: str) -> "_GUID":
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


_CLSID_CUIAutomation = _GUID.from_str("{FF48DBA4-60EF-4201-AA87-54103EEF594E}")
_IID_IUIAutomation = _GUID.from_str("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")
_CLSCTX_ALL = 23

# UIA ControlType IDs (strictly structural — NEVER read Name or Value)
_UIA_EDIT = 50004
_UIA_LIST = 50008
_UIA_MENUBAR = 50010
_UIA_TAB = 50018
_UIA_TOOLBAR = 50021
_UIA_STATUSBAR = 50022
_UIA_TREE = 50023
_UIA_DOCUMENT = 50030
_UIA_PANE = 50033
_UIA_TITLEBAR = 50037

_MAX_UIA_NODES = 120
_MAX_UIA_DEPTH = 4


def _release_com(ptr: c_void_p | None) -> None:
    if ptr and ptr.value:
        try:
            vtbl = ctypes.cast(ctypes.cast(ptr, POINTER(c_void_p))[0], POINTER(c_void_p))
            ctypes.WINFUNCTYPE(wt.ULONG, c_void_p)(vtbl[2])(ptr)
        except Exception:
            pass


def _classify_uia_block(
    ctrl_type: int,
    rx: int,
    ry: int,
    rw: int,
    rh: int,
    win_w: int,
    win_h: int,
) -> str:
    if ctrl_type in (_UIA_MENUBAR, _UIA_TOOLBAR, _UIA_TITLEBAR, _UIA_TAB) and ry <= 120 and rh <= 150:
        return "top_bar"
    if ry <= 12 and rh <= 120 and rw >= win_w * 0.45:
        return "top_bar"
    if ctrl_type == _UIA_STATUSBAR or (ry >= win_h * 0.85 and rh <= 80 and rw >= win_w * 0.45):
        return "bottom_bar"
    if (
        (ctrl_type in (_UIA_TREE, _UIA_LIST, _UIA_PANE) or True)
        and rx <= 24
        and rw <= win_w * 0.34
        and rh >= win_h * 0.4
    ):
        return "left_sidebar"
    if rx >= win_w * 0.66 and rw <= win_w * 0.34 and rh >= win_h * 0.4:
        return "right_sidebar"
    if ctrl_type in (_UIA_DOCUMENT, _UIA_EDIT) or (rw >= win_w * 0.42 and rh >= win_h * 0.42):
        return "main_content"
    return "pane"


def scan_win32_window_wireframe(hwnd: int, dpr: float = 1.0) -> dict | None:
    """Capture a strictly Zero-PII geometric wireframe using Windows UI Automation (NO text read)."""
    if not hwnd:
        return None
    parent_rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, byref(parent_rect)):
        return None

    scale = dpr if dpr > 0 else 1.0
    pl = parent_rect.left / scale
    pt = parent_rect.top / scale
    pr = parent_rect.right / scale
    pb = parent_rect.bottom / scale
    win_w = int(round(pr - pl))
    win_h = int(round(pb - pt))
    if win_w < 240 or win_h < 180:
        return None

    blocks: list[dict] = []
    seen: set[tuple[str, int, int, int, int]] = set()
    visited = 0

    ole32.CoInitialize(None)
    uia = c_void_p()
    walker = c_void_p()
    root = c_void_p()
    try:
        hr = ole32.CoCreateInstance(
            byref(_CLSID_CUIAutomation),
            None,
            _CLSCTX_ALL,
            byref(_IID_IUIAutomation),
            byref(uia),
        )
        if hr != 0 or not uia.value:
            return {"source": "uia_none", "viewport": [win_w, win_h], "blocks": []}

        vtbl = ctypes.cast(ctypes.cast(uia, POINTER(c_void_p))[0], POINTER(c_void_p))
        element_from_handle = ctypes.WINFUNCTYPE(c_long, c_void_p, wt.HWND, POINTER(c_void_p))(vtbl[6])
        get_control_walker = ctypes.WINFUNCTYPE(c_long, c_void_p, POINTER(c_void_p))(vtbl[14])

        if element_from_handle(uia, hwnd, byref(root)) != 0 or not root.value:
            return {"source": "uia_none", "viewport": [win_w, win_h], "blocks": []}
        if get_control_walker(uia, byref(walker)) != 0 or not walker.value:
            return {"source": "uia_none", "viewport": [win_w, win_h], "blocks": []}

        wvtbl = ctypes.cast(ctypes.cast(walker, POINTER(c_void_p))[0], POINTER(c_void_p))
        get_first_child = ctypes.WINFUNCTYPE(c_long, c_void_p, c_void_p, POINTER(c_void_p))(wvtbl[4])
        get_next_sibling = ctypes.WINFUNCTYPE(c_long, c_void_p, c_void_p, POINTER(c_void_p))(wvtbl[6])

        def _inspect(el: c_void_p) -> tuple[int, bool, int, int, int, int]:
            evtbl = ctypes.cast(ctypes.cast(el, POINTER(c_void_p))[0], POINTER(c_void_p))
            ct = c_int(0)
            off = c_int(0)
            rc = wt.RECT()
            ctypes.WINFUNCTYPE(c_long, c_void_p, POINTER(c_int))(evtbl[21])(el, byref(ct))
            ctypes.WINFUNCTYPE(c_long, c_void_p, POINTER(c_int))(evtbl[38])(el, byref(off))
            ctypes.WINFUNCTYPE(c_long, c_void_p, POINTER(wt.RECT))(evtbl[43])(el, byref(rc))
            rx = int(round(rc.left / scale - pl))
            ry = int(round(rc.top / scale - pt))
            rw = int(round((rc.right - rc.left) / scale))
            rh = int(round((rc.bottom - rc.top) / scale))
            return ct.value, bool(off.value), rx, ry, rw, rh

        def _walk(parent: c_void_p, depth: int) -> None:
            nonlocal visited
            if depth > _MAX_UIA_DEPTH or visited >= _MAX_UIA_NODES or len(blocks) >= 14:
                return
            ch = c_void_p()
            if get_first_child(walker, parent, byref(ch)) != 0:
                return
            while ch.value and visited < _MAX_UIA_NODES and len(blocks) < 14:
                visited += 1
                ct, is_offscreen, rx, ry, rw, rh = _inspect(ch)
                nxt = c_void_p()
                get_next_sibling(walker, ch, byref(nxt))
                try:
                    if not is_offscreen and rw >= 80 and rh >= 32 and rw <= win_w + 10 and rh <= win_h + 10:
                        is_full_wrapper = rw >= win_w - 18 and rh >= win_h - 18
                        if not is_full_wrapper:
                            role = _classify_uia_block(ct, rx, ry, rw, rh, win_w, win_h)
                            key = (role, rx // 20, ry // 20, rw // 20, rh // 20)
                            if key not in seen:
                                seen.add(key)
                                blocks.append({"role": role, "box": [max(0, rx), max(0, ry), rw, rh]})
                        if depth < _MAX_UIA_DEPTH:
                            _walk(ch, depth + 1)
                finally:
                    _release_com(ch)
                ch = nxt

        _walk(root, 1)
    except Exception:
        return {"source": "uia_none", "viewport": [win_w, win_h], "blocks": []}
    finally:
        _release_com(root)
        _release_com(walker)
        _release_com(uia)

    if not blocks:
        return {"source": "uia_none", "viewport": [win_w, win_h], "blocks": []}

    # Infer real geometric empty margins if a centered main_content exists without sidebars
    has_left_sb = any(b["role"] == "left_sidebar" for b in blocks)
    has_right_sb = any(b["role"] == "right_sidebar" for b in blocks)
    main_blks = [b for b in blocks if b["role"] == "main_content"]
    if main_blks:
        mx, my, mw, mh = max(main_blks, key=lambda b: b["box"][2] * b["box"][3])["box"]
        if not has_left_sb and mx >= 70:
            blocks.append({"role": "empty_space", "box": [4, my, mx - 8, mh]})
        right_gap = win_w - (mx + mw)
        if not has_right_sb and right_gap >= 70:
            blocks.append({"role": "empty_space", "box": [mx + mw + 4, my, right_gap - 8, mh]})

    return {
        "source": "uia",
        "viewport": [win_w, win_h],
        "blocks": blocks[:14],
    }


def _format_rel_boxes(label: str, rel_boxes: list[list[float]] | None) -> str:
    if not rel_boxes:
        return ""
    items = []
    for box in rel_boxes[:6]:
        if len(box) != 4:
            continue
        rx, ry, rw, rh = box
        x0 = max(0, min(100, int(round(rx * 100))))
        x1 = max(0, min(100, int(round((rx + rw) * 100))))
        y0 = max(0, min(100, int(round(ry * 100))))
        y1 = max(0, min(100, int(round((ry + rh) * 100))))
        items.append(f"{label}(x:{x0}-{x1}%,y:{y0}-{y1}%)")
    return ", ".join(items)


def summarize_wireframe_for_prompt(
    wireframe: dict | None,
    allowed_zones: list[list[float]] | None = None,
    forbidden_zones: list[list[float]] | None = None,
) -> str:
    """Summarize Zero-PII geometric wireframe + user-drawn allowed/forbidden zones for Gemini."""
    parts: list[str] = []

    if wireframe:
        vp = wireframe.get("viewport") or [1920, 1080]
        vw = max(1, int(vp[0]))
        vh = max(1, int(vp[1]))
        src = str(wireframe.get("source", "ui"))
        blocks = wireframe.get("blocks") or []
        if not blocks or src == "uia_none":
            parts.append(f"{src} {vw}x{vh}: no internal structure detected")
        else:
            blk_parts = []
            for blk in blocks[:10]:
                role = str(blk.get("role", "pane"))
                box = blk.get("box") or [0, 0, 0, 0]
                if len(box) != 4:
                    continue
                x, y, w, h = box
                x0 = max(0, min(100, int(round(x * 100 / vw))))
                x1 = max(0, min(100, int(round((x + w) * 100 / vw))))
                y0 = max(0, min(100, int(round(y * 100 / vh))))
                y1 = max(0, min(100, int(round((y + h) * 100 / vh))))
                blk_parts.append(f"{role}(x:{x0}-{x1}%,y:{y0}-{y1}%)")
            parts.append(f"{src} {vw}x{vh}: " + ", ".join(blk_parts))

    allowed_txt = _format_rel_boxes("user_allowed", allowed_zones)
    if allowed_txt:
        parts.append(allowed_txt)
    forbid_txt = _format_rel_boxes("user_forbidden", forbidden_zones)
    if forbid_txt:
        parts.append(forbid_txt)

    return " | ".join(parts) if parts else "not scanned yet"
