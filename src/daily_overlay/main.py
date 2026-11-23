from __future__ import annotations

import argparse
import ctypes
import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication

from daily_overlay.ai.director import load_or_create_daily_deck
from daily_overlay.assets.manager import bulk_download_public_pack, sync_public_catalog
from daily_overlay.autostart import get_launch_command
from daily_overlay.config import (
    ASSETS_DIR,
    DATA_DIR,
    LOG_PATH,
    PROJECT_ROOT,
    ensure_data_dirs,
)
from daily_overlay.context.browser_bridge import BrowserBridgeServer
from daily_overlay.overlay.engine import OverlayEngine
from daily_overlay.overlay.window import OverlayWindow
from daily_overlay.ui.tray import TrayController

logger = logging.getLogger(__name__)

_MUTEX_NAME = "Local\\DailyOverlay_SingleInstance_v1"
_ERROR_ALREADY_EXISTS = 183


def _emit_stdout(msg: str) -> None:
    if sys.stdout is not None:
        print(msg)


def _configure_logging() -> None:
    ensure_data_dirs()
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            LOG_PATH,
            maxBytes=2 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )

    def _log_unhandled(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            return
        logging.getLogger("daily_overlay").critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    sys.excepthook = _log_unhandled


def _acquire_single_instance_mutex() -> int | None:
    """Acquire a per-user Windows named mutex; return None if another instance is already running."""
    if sys.platform != "win32":
        return 0
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        return 0
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return int(handle)


def _release_single_instance_mutex(handle: int | None) -> None:
    if sys.platform != "win32" or not handle:
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ReleaseMutex(handle)
    kernel32.CloseHandle(handle)


def _run_self_check() -> int:
    ensure_data_dirs()
    formats = [bytes(f).lower() for f in QImageReader.supportedImageFormats()]
    has_webp = b"webp" in formats
    launch_cmd = get_launch_command()
    dirs_ok = DATA_DIR.is_dir() and ASSETS_DIR.is_dir()
    if not has_webp or not dirs_ok:
        msg = f"SELF_CHECK_FAILED webp={has_webp} dirs_ok={dirs_ok} root={PROJECT_ROOT}"
        logger.error(msg)
        _emit_stdout(msg)
        return 1

    msg = (
        f"SELF_CHECK_OK frozen={bool(getattr(sys, 'frozen', False))} "
        f"webp={has_webp} root={PROJECT_ROOT} launch={launch_cmd}"
    )
    logger.info(msg)
    _emit_stdout(msg)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="daily-overlay",
        description="Ambient context-aware Windows desktop overlay directed daily by Gemini.",
    )
    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Generate (or load cached) current 06:00/18:00 cycle scenario deck JSON and print active scenario without starting GUI.",
    )
    parser.add_argument(
        "--force-regenerate",
        action="store_true",
        help="Force Gemini to generate a fresh scenario for the current 06:00/18:00 cycle.",
    )
    parser.add_argument(
        "--download-assets",
        type=int,
        nargs="?",
        const=120,
        default=0,
        help="Download N public free animated WebP assets once into data/assets/ for long-term offline variety (default: 120).",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Verify frozen/runtime paths, WebP Qt plugin support, and startup command without opening UI.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = parse_args(argv)

    if args.self_check:
        return _run_self_check()

    sync_public_catalog()

    if args.download_assets > 0:
        msg = f"Downloading up to {args.download_assets} public animated WebP assets..."
        logger.info(msg)
        _emit_stdout(msg)
        downloaded, total = bulk_download_public_pack(limit=args.download_assets)
        done_msg = f"Done! Newly downloaded: {downloaded} | Total cached locally: {total}"
        logger.info(done_msg)
        _emit_stdout(done_msg)
        if not args.generate_only:
            return 0

    if args.generate_only:
        deck = load_or_create_daily_deck(force_new=args.force_regenerate)
        _emit_stdout(deck.active_scenario.model_dump_json(indent=2))
        return 0

    mutex_handle = _acquire_single_instance_mutex()
    if mutex_handle is None:
        logger.info("Another DailyOverlay instance is already running; exiting.")
        return 0

    try:
        deck = load_or_create_daily_deck(force_new=args.force_regenerate)

        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)

        bridge = BrowserBridgeServer()
        bridge.start()

        engine = OverlayEngine(deck)
        window = OverlayWindow(engine, browser_bridge=bridge)

        def _shutdown() -> None:
            bridge.stop()
            window.close()

        tray = TrayController(engine=engine, browser_bridge=bridge, on_quit=_shutdown)
        window.on_fullscreen_changed = tray.set_fullscreen_hidden
        window.on_new_day = tray.handle_new_day
        window.watcher.add_own_hwnd(int(tray.quick_bar.winId()))

        window._tray_ref = tray  # type: ignore[attr-defined]
        window.start()

        return app.exec()
    finally:
        _release_single_instance_mutex(mutex_handle)


if __name__ == "__main__":
    raise SystemExit(main())
