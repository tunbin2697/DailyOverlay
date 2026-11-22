from __future__ import annotations

import datetime as dt
import json
import logging
import random
from pathlib import Path

from google import genai
from google.genai import types

from daily_overlay.ai.schema import (
    AppRule,
    ComponentConfig,
    DailyScenario,
    Palette,
    TimeShift,
    TimeShifts,
)
from daily_overlay.assets.manager import sample_catalog_for_prompt
from daily_overlay.config import (
    COMMON_BROWSERS,
    DATA_DIR,
    GEMINI_FALLBACK_MODELS,
    SCENARIOS_DIR,
    AppProfile,
    ensure_data_dirs,
    load_api_key,
    load_app_profiles,
)

logger = logging.getLogger(__name__)

DISLIKED_PATH = DATA_DIR / "disliked_themes.json"

# Creative seed directions picked randomly each call so Gemini explores wildly different genres
CREATIVE_LENSES = [
    "Deep ocean bioluminescence & marine creatures",
    "Cozy rainy night cafe & warm lanterns",
    "Stargazing observatory & cosmic drift",
    "Botanical greenhouse & fluttering insects",
    "Autumn mountain mist & drifting leaves",
    "Arctic twilight & auroras",
    "Whimsical floating clouds & sky vessels",
    "Zen lotus pond & gentle ripples",
    "Sunlit tropical coast & sea breeze",
    "Midnight folklore spirits & glowing lamps",
    "Spring blossom festival & drifting petals",
    "Minimalist pastel sunrise & quiet birds",
]


def _scenario_path(day: dt.date) -> Path:
    ensure_data_dirs()
    return SCENARIOS_DIR / f"{day.isoformat()}.json"


def load_disliked_themes() -> list[str]:
    ensure_data_dirs()
    if not DISLIKED_PATH.exists():
        return []
    try:
        data = json.loads(DISLIKED_PATH.read_text(encoding="utf-8"))
        return [str(x) for x in data if x]
    except Exception:
        return []


def record_disliked_theme(theme_name: str) -> None:
    if not theme_name:
        return
    items = load_disliked_themes()
    if theme_name not in items:
        items.append(theme_name)
    DISLIKED_PATH.write_text(
        json.dumps(items[-40:], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def delete_today_scenario(dislike_theme_name: str | None = None) -> None:
    """Remove today's cached scenario and optionally blacklist its theme so Gemini avoids it."""
    if dislike_theme_name:
        record_disliked_theme(dislike_theme_name)
    path = _scenario_path(dt.date.today())
    if path.exists():
        path.unlink(missing_ok=True)


def _recent_theme_names(limit: int = 12) -> list[str]:
    ensure_data_dirs()
    files = sorted(SCENARIOS_DIR.glob("*.json"), reverse=True)[:limit]
    themes: list[str] = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            name = data.get("theme_name")
            if name:
                themes.append(str(name))
        except Exception:
            continue
    return themes


def fallback_scenario(day: dt.date | None = None) -> DailyScenario:
    """Offline emergency fallback only used if all network/API calls fail."""
    today = (day or dt.date.today()).isoformat()
    return DailyScenario(
        date=today,
        theme_name="Mưa Đêm & Đèn Lồng",
        tagline="Làn mưa đêm dịu nhẹ cùng ánh đèn lồng ấm áp nơi góc màn hình.",
        mood="cozy",
        palette=Palette(
            primary="#6C8EAD",
            secondary="#F4C095",
            accent="#E07A5F",
            opacity_scale=0.84,
        ),
        time_shifts=TimeShifts(
            morning=TimeShift(brightness=1.0, speed=0.95),
            afternoon=TimeShift(brightness=1.0, speed=1.0),
            night=TimeShift(brightness=0.85, speed=0.75),
        ),
        global_components=[
            ComponentConfig(
                type="floating_particles",
                zone="right_edge",
                presence="continuous",
                density=0.7,
                speed=0.85,
                size=1.0,
                style="rain",
                avoid_cursor=True,
            ),
            ComponentConfig(
                type="animated_actor",
                zone="corners",
                presence="continuous",
                behavior="corner_companion",
                density=0.5,
                speed=0.6,
                size=1.1,
                style="diya-lamp",
                avoid_cursor=False,
            ),
        ],
        app_rules=[
            AppRule(
                rule_name="YouTube",
                match_process=list(COMMON_BROWSERS),
                match_title_contains=["YouTube"],
                components=[
                    ComponentConfig(
                        type="animated_actor",
                        zone="bottom_edge",
                        presence="cameo",
                        behavior="parade_pass",
                        density=0.6,
                        speed=0.7,
                        size=1.1,
                        style="whale",
                        avoid_cursor=False,
                    ),
                    ComponentConfig(
                        type="ambient_clouds",
                        zone="top_edge",
                        presence="continuous",
                        density=0.55,
                        speed=0.5,
                        size=1.0,
                        style="mist",
                        avoid_cursor=True,
                    ),
                ],
            ),
            AppRule(
                rule_name="Google Docs / Notion",
                match_process=list(COMMON_BROWSERS),
                match_title_contains=["Google Docs", "Notion", "Word", "Docs"],
                components=[
                    ComponentConfig(
                        type="floating_particles",
                        zone="left_right_margins",
                        presence="continuous",
                        density=0.6,
                        speed=0.5,
                        size=1.0,
                        style="stars",
                        avoid_cursor=True,
                    )
                ],
            ),
            AppRule(
                rule_name="VS Code / IDE",
                match_process=["code.exe", "cursor.exe", "pycharm64.exe", "idea64.exe", "antigravity.exe"],
                match_title_contains=[],
                components=[
                    ComponentConfig(
                        type="ambient_waves",
                        zone="bottom_edge",
                        presence="continuous",
                        density=0.5,
                        speed=0.55,
                        size=0.95,
                        style="aurora",
                        avoid_cursor=True,
                    )
                ],
            ),
        ],
    )


def _build_prompt(
    today: dt.date,
    profiles: list[AppProfile],
    recent_themes: list[str],
    disliked_themes: list[str],
) -> str:
    weekday = today.strftime("%A")
    profiles_json = json.dumps(
        [p.model_dump() for p in profiles if p.enabled],
        indent=2,
        ensure_ascii=False,
    )
    catalog_sample = sample_catalog_for_prompt(max_items=95)
    catalog_lines = "\n".join(
        f"  - {cat}: {', '.join(slugs)}" for cat, slugs in catalog_sample.items()
    )
    lens = random.choice(CREATIVE_LENSES)
    avoid_list = list(dict.fromkeys(recent_themes + disliked_themes))
    avoid_clause = (
        f"STRICTLY AVOID repeating or closely resembling these previous/disliked themes: {', '.join(avoid_list)}."
        if avoid_list
        else "Create an unexpected, memorable, visually cohesive theme."
    )

    return f"""You are the free-spirited Daily Art Director for DailyOverlay, an aesthetic ambient desktop overlay on Windows.
Today is {today.isoformat()} ({weekday}).
Creative inspiration seed for this generation (interpret freely or blend creatively): "{lens}".

Design ONE cohesive, genuinely original daily visual scenario. You have FULL creative freedom — DO NOT fall back on clichés (like always putting a cat on YouTube or bamboo everywhere). Surprise the user with fresh combinations of atmosphere, motion, and public animated assets!

Guidelines:
1. Keep opacity_scale clearly visible yet comfortable (0.76 - 0.90), with rich harmonious hex colors (primary, secondary, accent) that work on both light and dark screens.
2. {avoid_clause}
3. Balance **calm continuous atmosphere** (`presence: "continuous"`) with **intermittent cameos** (`presence: "cameo"` for active moving actors so they peek in for 20-35s and rest off-screen instead of nagging non-stop).
4. Provide 2 complementary `global_components` for Desktop / unmatched apps.
5. Provide `app_rules` matching EVERY enabled App Profile below (copy `match_process` and `match_title_contains` accurately). Choose 1-2 fitting components per app freely based on its spatial layout (`preferred_zone`) — vary your choices boldly each day!
6. Available Engine Primitives:
   - `ambient_waves`: styles = "ocean" | "aurora" | "silk"
   - `ambient_clouds`: styles = "mist" | "cumulus" | "sunset" | "aurora_haze"
   - `floating_particles`: styles = "fireflies" | "bubbles" | "petals" | "snow" | "dust" | "rain" | "stars"
   - `swaying_foliage`: styles = "bamboo" | "sakura" | "fern" | "willow" (use density 0.7-0.95 for lush look)
   - `boids_fish`: styles = "koi" | "neon" | "ink"
   - `animated_actor`: Renders ANY high-res animated transparent WebP from the Public Asset Catalog below! Pair it with one of 4 behaviors:
       * `perch_hop`: gently perches & glides on web thumbnails (YouTube) or bottom edge
       * `float_drift`: lazily floats/bobs across margins, corners, or sky
       * `corner_companion`: cozy animated object/creature resting gently in a corner (e.g. lantern, lotus, campfire, owl, mushroom, tea)
       * `parade_pass`: occasionally cruises smoothly across the screen edge and exits (e.g. whale, sailboat, rocket, bird, snail, bicycle)
7. Public Animated WebP Asset Catalog (pick exact slugs for `animated_actor.style` — you are encouraged to mix diverse objects, plants, cosmic items, sea creatures, lanterns, food, and animals!):
{catalog_lines}
8. Write `tagline` in natural, poetic Vietnamese (1 short sentence).

User's configured App Profiles:
{profiles_json}
"""


def generate_daily_scenario(
    day: dt.date | None = None,
    profiles: list[AppProfile] | None = None,
) -> DailyScenario:
    today = day or dt.date.today()
    api_key = load_api_key()
    if not api_key:
        logger.warning("GOOGLE_AI_API_KEY not found; using fallback scenario.")
        return fallback_scenario(today)

    active_profiles = profiles if profiles is not None else load_app_profiles()
    recent = _recent_theme_names(limit=12)
    disliked = load_disliked_themes()
    prompt = _build_prompt(today, active_profiles, recent, disliked)

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version="v1beta", timeout=20_000),
    )
    last_error: Exception | None = None
    response = None
    for model_name in GEMINI_FALLBACK_MODELS:
        try:
            logger.info("Requesting daily scenario from %s...", model_name)
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=DailyScenario,
                    temperature=1.15,
                ),
            )
            if response.text:
                logger.info("Daily scenario generated successfully with %s.", model_name)
                break
        except Exception as exc:
            last_error = exc
            logger.warning("Model %s unavailable (%s); trying next fallback...", model_name, exc)

    if response is None or not response.text:
        raise RuntimeError(f"All Gemini models failed: {last_error}")

    scenario = DailyScenario.model_validate_json(response.text)
    scenario.date = today.isoformat()
    save_scenario(scenario, today)
    return scenario


def save_scenario(scenario: DailyScenario, day: dt.date | None = None) -> Path:
    target_day = day or dt.date.fromisoformat(scenario.date)
    path = _scenario_path(target_day)
    path.write_text(
        scenario.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return path


def get_or_create_daily_scenario(
    force_regenerate: bool = False,
    profiles: list[AppProfile] | None = None,
) -> DailyScenario:
    today = dt.date.today()
    path = _scenario_path(today)
    if path.exists() and not force_regenerate:
        try:
            return DailyScenario.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load cached scenario %s: %s", path, exc)

    try:
        return generate_daily_scenario(today, profiles=profiles)
    except Exception as exc:
        logger.error("Gemini generation failed (%s); falling back to default scenario.", exc)
        scenario = fallback_scenario(today)
        save_scenario(scenario, today)
        return scenario
