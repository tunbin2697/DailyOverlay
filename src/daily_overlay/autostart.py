from __future__ import annotations

import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows fallback
    winreg = None  # type: ignore[assignment]

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_VALUE_NAME = "DailyOverlay"


def get_launch_command() -> str:
    """Return the quoted windowless launch command for Windows startup."""
    exe_path = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return f'"{exe_path}"'

    gui_exe = exe_path.parent / "daily-overlay-gui.exe"
    if gui_exe.is_file():
        return f'"{gui_exe}"'

    pythonw = exe_path.parent / "pythonw.exe"
    if pythonw.is_file():
        return f'"{pythonw}" -m daily_overlay.main'

    return f'"{exe_path}" -m daily_overlay.main'


def get_autostart_command(
    key_path: str = RUN_KEY_PATH,
    value_name: str = APP_VALUE_NAME,
) -> str | None:
    """Read the registered Windows startup command, or None if not enabled."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            val, _reg_type = winreg.QueryValueEx(key, value_name)
            text = str(val).strip()
            return text or None
    except (FileNotFoundError, OSError):
        return None


def is_autostart_enabled(
    key_path: str = RUN_KEY_PATH,
    value_name: str = APP_VALUE_NAME,
) -> bool:
    """Return True when DailyOverlay has an active HKCU Run entry."""
    return bool(get_autostart_command(key_path=key_path, value_name=value_name))


def set_autostart(
    enabled: bool,
    command: str | None = None,
    key_path: str = RUN_KEY_PATH,
    value_name: str = APP_VALUE_NAME,
) -> tuple[bool, str]:
    """Enable or disable Windows startup via HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run."""
    if winreg is None:
        return False, "Windows Registry is not available on this platform."

    try:
        if enabled:
            cmd = (command or get_launch_command()).strip()
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, cmd)
            return True, cmd

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                try:
                    winreg.DeleteValue(key, value_name)
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
        return True, ""
    except OSError as exc:
        return False, str(exc)
