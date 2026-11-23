from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field


def _resolve_app_base_dir() -> Path:
    """Resolve runtime base directory for both frozen .exe builds and source checkouts."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _resolve_app_base_dir()
ENV_PATH = PROJECT_ROOT / ".env"
DATA_DIR = PROJECT_ROOT / "data"
SCENARIOS_DIR = DATA_DIR / "scenarios"
ASSETS_DIR = DATA_DIR / "assets"
PROFILES_PATH = DATA_DIR / "app_profiles.json"
EXTENSION_DIR = PROJECT_ROOT / "browser_extension"
LOG_PATH = DATA_DIR / "daily_overlay.log"

GEMINI_FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
]
DAILY_DECK_SIZE = 3
DAY_ROLLOVER_HOUR = 6       # 06:00 AM — new logical day + daytime cycle
EVENING_ROLLOVER_HOUR = 18  # 18:00 PM — evening/night cycle
TARGET_FPS = 30
IDLE_POLL_MS = 250
MAX_AMBIENT_SPEED_PX_S = 18.0     # Hard ceiling for calm ambient drift/swim/walk/fall (px/s)
MAX_TRANSLATE_SPEED_PX_S = 140.0  # Hard ceiling for smooth zone relocation translation (px/s)
MOVIE_PLAYBACK_SPEED_PCT = 65     # Gentle playback speed (%) for animated WebP/GIF loops
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
ProfileMode = Literal["follow_daily", "pinned"]
CyclePeriod = Literal["day", "night"]


def current_cycle_slot(now: dt.datetime | None = None) -> tuple[dt.date, CyclePeriod]:
    """Resolve (logical_date, period) using 06:00 day rollover and 18:00 evening rollover.

    - 06:00..17:59 -> (today, "day")
    - 18:00..23:59 -> (today, "night")
    - 00:00..05:59 -> (yesterday, "night")
    """
    ts = now or dt.datetime.now()
    if DAY_ROLLOVER_HOUR <= ts.hour < EVENING_ROLLOVER_HOUR:
        return ts.date(), "day"
    if ts.hour >= EVENING_ROLLOVER_HOUR:
        return ts.date(), "night"
    return ts.date() - dt.timedelta(days=1), "night"


def cycle_key(day: dt.date, period: CyclePeriod) -> str:
    """Return canonical identifier YYYY-MM-DD_day or YYYY-MM-DD_night."""
    return f"{day.isoformat()}_{period}"


def slugify_profile_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "custom_app"


class AppProfile(BaseModel):
    """One-time user setup mapping an app/web context to safe spatial layout and optional pinned style."""

    id: str = ""
    name: str
    match_process: list[str] = Field(default_factory=list)
    match_title_contains: list[str] = Field(default_factory=list)
    match_hosts: list[str] = Field(default_factory=list)
    preferred_zone: ZoneType = "bottom_edge"
    preferred_vibe: str = "calm and unobtrusive"
    mode: ProfileMode = "follow_daily"
    layout_wireframe: dict | None = None
    custom_allowed_zones: list[list[float]] = Field(default_factory=list)
    custom_forbidden_zones: list[list[float]] = Field(default_factory=list)
    pinned_theme_name: str = ""
    pinned_since: str = ""
    pinned_components: list[dict] = Field(default_factory=list)
    enabled: bool = True

    def model_post_init(self, __context) -> None:
        if not self.id:
            self.id = slugify_profile_id(self.name)


DEFAULT_APP_PROFILES: list[AppProfile] = [
    AppProfile(
        id="youtube",
        name="YouTube",
        match_process=list(COMMON_BROWSERS),
        match_title_contains=["YouTube"],
        match_hosts=["youtube.com"],
        preferred_zone="bottom_edge",
        preferred_vibe="media browsing with video thumbnail anchors — feel free to pick any playful or atmospheric theme (intermittent cameo works great)",
    ),
    AppProfile(
        id="google_docs_notion",
        name="Google Docs / Notion",
        match_process=list(COMMON_BROWSERS),
        match_title_contains=["Google Docs", "Notion", "Word", "Docs"],
        match_hosts=["docs.google.com", "notion.so"],
        preferred_zone="left_right_margins",
        preferred_vibe="writing/reading page with wide empty left and right margins — keep tranquil and low-distraction",
    ),
    AppProfile(
        id="vs_code_ide",
        name="VS Code / IDE",
        match_process=["code.exe", "cursor.exe", "pycharm64.exe", "idea64.exe", "antigravity.exe", "antigravity ide.exe"],
        match_title_contains=[],
        match_hosts=[],
        preferred_zone="right_edge",
        preferred_vibe="deep focus coding environment — keep minimal, quiet, and tucked to edges/corners",
    ),
]


def load_api_key() -> str | None:
    load_dotenv(ENV_PATH)
    key = os.getenv("GOOGLE_AI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key and getattr(sys, "frozen", False):
        fallback_env = PROJECT_ROOT.parent.parent / ".env"
        if fallback_env.is_file():
            load_dotenv(fallback_env)
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
        return [p.model_copy(deep=True) for p in DEFAULT_APP_PROFILES]
    try:
        raw = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        return [AppProfile.model_validate(item) for item in raw]
    except Exception:
        return [p.model_copy(deep=True) for p in DEFAULT_APP_PROFILES]


def save_app_profiles(profiles: list[AppProfile]) -> None:
    ensure_data_dirs()
    payload = [p.model_dump() for p in profiles]
    PROFILES_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def upsert_app_profile(profile: AppProfile) -> list[AppProfile]:
    profiles = load_app_profiles()
    for idx, existing in enumerate(profiles):
        if existing.id == profile.id:
            profiles[idx] = profile
            save_app_profiles(profiles)
            return profiles
    profiles.append(profile)
    save_app_profiles(profiles)
    return profiles


def match_profile_for_context(
    profiles: list[AppProfile],
    process_name: str,
    title: str = "",
    browser_host: str = "",
) -> AppProfile | None:
    """Resolve the best matching enabled AppProfile for an active app/browser tab."""
    if not process_name:
        return None

    proc = process_name.lower()
    is_browser = proc in COMMON_BROWSERS
    host_l = browser_host.lower().strip()
    title_l = title.lower()

    # 1. Exact host match when inside a browser
    if is_browser and host_l:
        for p in profiles:
            if not p.enabled or not p.match_hosts:
                continue
            if any(h.lower() in host_l for h in p.match_hosts if h.strip()):
                return p

    # 2. Process + title keyword match
    combined_title = f"{title_l} {host_l}".strip() if is_browser else title_l
    for p in profiles:
        if not p.enabled:
            continue
        procs = [x.lower() for x in p.match_process]
        titles = [x.lower() for x in p.match_title_contains if x.strip()]
        proc_ok = (not procs) or (proc in procs)
        if proc_ok and titles and any(t in combined_title for t in titles):
            return p

    # 3. Process-only match (no title/host constraint)
    for p in profiles:
        if not p.enabled:
            continue
        procs = [x.lower() for x in p.match_process]
        titles = [x.lower() for x in p.match_title_contains if x.strip()]
        hosts = [x.lower() for x in p.match_hosts if x.strip()]
        if procs and proc in procs and not titles and not hosts:
            return p

    return None
