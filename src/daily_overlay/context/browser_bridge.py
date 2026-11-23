from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import websockets

from daily_overlay.config import BROWSER_BRIDGE_HOST, BROWSER_BRIDGE_PORT
from daily_overlay.context.zones import ZoneRect

logger = logging.getLogger(__name__)


@dataclass
class BrowserContext:
    host: str = ""
    url: str = ""
    title: str = ""
    is_scrolling: bool = False
    elements: list[ZoneRect] = field(default_factory=list)
    margins: list[ZoneRect] = field(default_factory=list)
    wireframe: dict | None = None
    updated_at: float = 0.0

    def is_fresh(self, max_age: float = 6.0) -> bool:
        return self.updated_at > 0.0 and (time.monotonic() - self.updated_at) <= max_age


class BrowserBridgeServer:
    """Local WebSocket server receiving live DOM bounding boxes & Zero-PII layout scans from browser extension."""

    def __init__(
        self,
        host: str = BROWSER_BRIDGE_HOST,
        port: int = BROWSER_BRIDGE_PORT,
        on_layout_scan: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_layout_scan = on_layout_scan
        self._lock = threading.Lock()
        self._context = BrowserContext()
        self._latest_scan: tuple[str, dict, float] | None = None
        self._client_count = 0
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None

    def set_on_layout_scan(self, cb: Callable[[str, dict], None] | None) -> None:
        self._on_layout_scan = cb

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="BrowserBridgeWS",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    @property
    def connected_clients(self) -> int:
        with self._lock:
            return self._client_count

    def get_latest_scan(self) -> tuple[str, dict, float] | None:
        with self._lock:
            return self._latest_scan

    def get_context(self) -> BrowserContext | None:
        with self._lock:
            if self._context.is_fresh():
                return BrowserContext(
                    host=self._context.host,
                    url=self._context.url,
                    title=self._context.title,
                    is_scrolling=self._context.is_scrolling,
                    elements=list(self._context.elements),
                    margins=list(self._context.margins),
                    wireframe=self._context.wireframe,
                    updated_at=self._context.updated_at,
                )
        return None

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._stop_event = asyncio.Event()
        try:
            loop.run_until_complete(self._serve())
        except Exception as exc:
            logger.warning("BrowserBridgeServer stopped: %s", exc)
        finally:
            loop.close()

    async def _serve(self) -> None:
        assert self._stop_event is not None
        try:
            async with websockets.serve(
                self._handler,
                self._host,
                self._port,
                ping_interval=20,
                ping_timeout=20,
            ):
                logger.info(
                    "BrowserBridge WebSocket listening on ws://%s:%d",
                    self._host,
                    self._port,
                )
                await self._stop_event.wait()
        except OSError as exc:
            logger.warning("Could not bind BrowserBridge port %d: %s", self._port, exc)

    async def _handler(self, websocket) -> None:
        with self._lock:
            self._client_count += 1
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                msg_type = msg.get("type")
                if msg_type not in ("dom_context", "layout_scan"):
                    continue

                elements = [
                    ZoneRect(
                        x=float(e["x"]),
                        y=float(e["y"]),
                        w=float(e["w"]),
                        h=float(e["h"]),
                    )
                    for e in msg.get("elements", [])
                    if e.get("w", 0) >= 40 and e.get("h", 0) >= 30
                ]
                margins = [
                    ZoneRect(
                        x=float(m["x"]),
                        y=float(m["y"]),
                        w=float(m["w"]),
                        h=float(m["h"]),
                    )
                    for m in msg.get("margins", [])
                    if m.get("w", 0) >= 50 and m.get("h", 0) >= 100
                ]
                wireframe = msg.get("wireframe") if isinstance(msg.get("wireframe"), dict) else None
                host = str(msg.get("host", ""))

                with self._lock:
                    self._context = BrowserContext(
                        host=host,
                        url=str(msg.get("url", "")),
                        title=str(msg.get("title", "")),
                        is_scrolling=bool(msg.get("is_scrolling", False)),
                        elements=elements[:16],
                        margins=margins[:4],
                        wireframe=wireframe,
                        updated_at=time.monotonic(),
                    )
                    if msg_type == "layout_scan" and wireframe is not None and host:
                        self._latest_scan = (host, wireframe, time.monotonic())

                if msg_type == "layout_scan" and wireframe is not None and host and self._on_layout_scan:
                    try:
                        self._on_layout_scan(host, wireframe)
                    except Exception as exc:
                        logger.warning("on_layout_scan callback failed: %s", exc)
        except Exception:
            pass
        finally:
            with self._lock:
                self._client_count = max(0, self._client_count - 1)
