from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
DATA_DIR = PROJECT_ROOT / "data"
SCENARIOS_DIR = DATA_DIR / "scenarios"
ASSETS_DIR = DATA_DIR / "assets"
PROFILES_PATH = DATA_DIR / "app_profiles.json"
EXTENSION_DIR = PROJECT_ROOT / "browser_extension"

GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
]
TARGET_FPS = 30
IDLE_POLL_MS = 250
BROWSER_BRIDGE_HOST = "127.0.0.1"
BROWSER_BRIDGE_PORT = 18765

COMMON_BROWSERS = [
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "firefox.exe",
    "browser.exe",
    "opera.exe",
    "arc.exe",
    "zen.exe",
]

ZoneType = Literal[
    "bottom_edge",
    "left_right_margins",
    "right_edge",
    "top_edge",
    "corners",
]


class AppProfile(BaseModel):
    """One-time user setup mapping an app/web context to safe spatial layout (never hardcoding visuals)."""

    name: str
    match_process: list[str] = Field(default_factory=list)
    match_title_contains: list[str] = Field(default_factory=list)
    preferred_zone: ZoneType = "bottom_edge"
    preferred_vibe: str = "calm and unobtrusive"
    enabled: bool = True


DEFAULT_APP_PROFILES: list[AppProfile] = [
    AppProfile(
        name="YouTube",
        match_process=list(COMMON_BROWSERS),
        match_title_contains=["YouTube"],
        preferred_zone="bottom_edge",
        preferred_vibe="media browsing with video thumbnail anchors — feel free to pick any playful or atmospheric theme (intermittent cameo works great)",
    ),
    AppProfile(
        name="Google Docs / Notion",
        match_process=list(COMMON_BROWSERS),
        match_title_contains=["Google Docs", "Notion", "Word", "Docs"],
        preferred_zone="left_right_margins",
        preferred_vibe="writing/reading page with wide empty left and right margins — keep tranquil and low-distraction",
    ),
    AppProfile(
        name="VS Code / IDE",
        match_process=["code.exe", "cursor.exe", "pycharm64.exe", "idea64.exe", "antigravity.exe"],
        match_title_contains=[],
        preferred_zone="right_edge",
        preferred_vibe="deep focus coding environment — keep minimal, quiet, and tucked to edges/corners",
    ),
]


def load_api_key() -> str | None:
    load_dotenv(ENV_PATH)
    key = os.getenv("GOOGLE_AI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if key:
        return key.strip().strip('"').strip("'")
    return None


def ensure_data_dirs() -> None:
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def load_app_profiles() -> list[AppProfile]:
    ensure_data_dirs()
    if not PROFILES_PATH.exists():
        save_app_profiles(DEFAULT_APP_PROFILES)
        return list(DEFAULT_APP_PROFILES)
    try:
        raw = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        return [AppProfile.model_validate(item) for item in raw]
    except Exception:
        return list(DEFAULT_APP_PROFILES)


def save_app_profiles(profiles: list[AppProfile]) -> None:
    ensure_data_dirs()
    payload = [p.model_dump() for p in profiles]
    PROFILES_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
