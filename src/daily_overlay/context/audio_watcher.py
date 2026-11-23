from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import uuid
from ctypes import POINTER, byref, c_float, c_int, c_long, c_void_p

ole32 = ctypes.windll.ole32


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


_CLSID_MMDeviceEnumerator = _GUID.from_str("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_IID_IMMDeviceEnumerator = _GUID.from_str("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
_IID_IAudioMeterInformation = _GUID.from_str("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")

_CLSCTX_ALL = 23
_PEAK_THRESHOLD = 0.008
_HOLDOVER_SEC = 3.0


class WindowsAudioWatcher:
    """Zero-dependency pure-ctypes Windows system audio activity detector (IAudioMeterInformation)."""

    def __init__(self) -> None:
        self._meter_ptr: c_void_p | None = None
        self._get_peak_fn = None
        self._release_fn = None
        self._last_init_attempt = -999.0
        self._last_sound_time = -999.0

    def _release_meter(self) -> None:
        if self._meter_ptr and self._release_fn:
            try:
                self._release_fn(self._meter_ptr)
            except Exception:
                pass
        self._meter_ptr = None
        self._get_peak_fn = None
        self._release_fn = None

    def _ensure_meter(self, now: float) -> bool:
        if self._meter_ptr is not None and self._get_peak_fn is not None:
            return True
        if now - self._last_init_attempt < 8.0:
            return False
        self._last_init_attempt = now

        try:
            ole32.CoInitialize(None)
            enum_ptr = c_void_p()
            hr = ole32.CoCreateInstance(
                byref(_CLSID_MMDeviceEnumerator),
                None,
                _CLSCTX_ALL,
                byref(_IID_IMMDeviceEnumerator),
                byref(enum_ptr),
            )
            if hr != 0 or not enum_ptr.value:
                return False

            evtbl = ctypes.cast(ctypes.cast(enum_ptr, POINTER(c_void_p))[0], POINTER(c_void_p))
            enum_release = ctypes.WINFUNCTYPE(wt.ULONG, c_void_p)(evtbl[2])
            get_default = ctypes.WINFUNCTYPE(c_long, c_void_p, c_int, c_int, POINTER(c_void_p))(evtbl[4])

            dev_ptr = c_void_p()
            hr2 = get_default(enum_ptr, 0, 0, byref(dev_ptr))
            enum_release(enum_ptr)
            if hr2 != 0 or not dev_ptr.value:
                return False

            dvtbl = ctypes.cast(ctypes.cast(dev_ptr, POINTER(c_void_p))[0], POINTER(c_void_p))
            dev_release = ctypes.WINFUNCTYPE(wt.ULONG, c_void_p)(dvtbl[2])
            activate = ctypes.WINFUNCTYPE(
                c_long, c_void_p, POINTER(_GUID), wt.DWORD, c_void_p, POINTER(c_void_p)
            )(dvtbl[3])

            meter_ptr = c_void_p()
            hr3 = activate(dev_ptr, byref(_IID_IAudioMeterInformation), _CLSCTX_ALL, None, byref(meter_ptr))
            dev_release(dev_ptr)
            if hr3 != 0 or not meter_ptr.value:
                return False

            mvtbl = ctypes.cast(ctypes.cast(meter_ptr, POINTER(c_void_p))[0], POINTER(c_void_p))
            self._meter_ptr = meter_ptr
            self._release_fn = ctypes.WINFUNCTYPE(wt.ULONG, c_void_p)(mvtbl[2])
            self._get_peak_fn = ctypes.WINFUNCTYPE(c_long, c_void_p, POINTER(c_float))(mvtbl[3])
            return True
        except Exception:
            self._release_meter()
            return False

    def poll(self, now: float) -> tuple[bool, float]:
        """Return (is_audio_playing, current_peak) with 3-second holdover across quiet tracks."""
        if not self._ensure_meter(now):
            return False, 0.0

        peak = c_float(0.0)
        try:
            hr = self._get_peak_fn(self._meter_ptr, byref(peak))
            if hr != 0:
                self._release_meter()
                return (now - self._last_sound_time) <= _HOLDOVER_SEC, 0.0
        except Exception:
            self._release_meter()
            return False, 0.0

        val = float(peak.value)
        if val > _PEAK_THRESHOLD:
            self._last_sound_time = now

        is_playing = (now - self._last_sound_time) <= _HOLDOVER_SEC
        return is_playing, val
