from __future__ import annotations

import argparse
import logging
import sys

from PySide6.QtWidgets import QApplication

from daily_overlay.ai.director import get_or_create_daily_scenario
from daily_overlay.assets.manager import bulk_download_public_pack, ensure_manifest
from daily_overlay.context.browser_bridge import BrowserBridgeServer
from daily_overlay.overlay.engine import OverlayEngine
from daily_overlay.overlay.window import OverlayWindow
from daily_overlay.ui.tray import TrayController


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="daily-overlay",
        description="Ambient context-aware Windows desktop overlay directed daily by Gemini.",
    )
    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Generate (or load cached) today's scenario JSON and print it without starting the GUI.",
    )
    parser.add_argument(
        "--force-regenerate",
        action="store_true",
        help="Force Gemini to generate a fresh scenario for today.",
    )
    parser.add_argument(
        "--download-assets",
        type=int,
        nargs="?",
        const=120,
        default=0,
        help="Download N public free animated WebP assets once into data/assets/ for long-term offline variety (default: 120).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = parse_args(argv)
    ensure_manifest()

    if args.download_assets > 0:
        print(f"Downloading up to {args.download_assets} public animated WebP assets...")
        downloaded, total = bulk_download_public_pack(limit=args.download_assets)
        print(f"Done! Newly downloaded: {downloaded} | Total cached locally: {total}")
        if not args.generate_only:
            return 0

    scenario = get_or_create_daily_scenario(force_regenerate=args.force_regenerate)

    if args.generate_only:
        print(scenario.model_dump_json(indent=2))
        return 0

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    bridge = BrowserBridgeServer()
    bridge.start()

    engine = OverlayEngine(scenario)
    window = OverlayWindow(engine, browser_bridge=bridge)

    def _shutdown() -> None:
        bridge.stop()
        window.close()

    tray = TrayController(engine=engine, browser_bridge=bridge, on_quit=_shutdown)
    window._tray_ref = tray  # type: ignore[attr-defined]
    window.start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
