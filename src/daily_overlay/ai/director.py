from __future__ import annotations

import datetime as dt
import json
import logging
import random
import time
from pathlib import Path

from google import genai
from google.genai import types

from daily_overlay.ai.schema import (
    ComponentConfig,
    ContextModifiers,
    DailyDeck,
    DailyScenario,
    Palette,
    PinnedAppStyle,
    StateModifier,
    TimeShift,
    TimeShifts,
)
from daily_overlay.assets.manager import sample_catalog_for_prompt
from daily_overlay.config import (
    DATA_DIR,
    GEMINI_FALLBACK_MODELS,
    SCENARIOS_DIR,
    AppProfile,
    CyclePeriod,
    current_cycle_slot,
    ensure_data_dirs,
    load_api_key,
)
from daily_overlay.context.layout_scanner import summarize_wireframe_for_prompt

logger = logging.getLogger(__name__)

DISLIKED_PATH = DATA_DIR / "disliked_themes.json"
_MODEL_COOLDOWN_UNTIL: dict[str, float] = {}
_COOLDOWN_SEC = 900.0

DAY_CREATIVE_LENSES = [
    "Sunlit tropical coast & gentle sea breeze",
    "Botanical greenhouse & fluttering butterflies",
    "Spring blossom festival & drifting petals",
    "Zen lotus pond & sun-warmed water ripples",
    "Minimalist pastel sunrise & quiet soaring birds",
    "Whimsical drifting cumulus clouds & sky vessels",
    "Crisp bamboo courtyard & morning breeze",
    "Clear alpine meadow & drifting dandelion seeds",
]

NIGHT_CREATIVE_LENSES = [
    "Deep ocean bioluminescence & glowing marine creatures",
    "Cozy rainy night cafe & warm glowing lanterns",
    "Stargazing observatory & shooting stars",
    "Arctic twilight, ice crystals & northern auroras",
    "Midnight folklore spirits & floating paper lamps",
    "Moonlit firefly meadow & evening mist",
    "Quiet riverside lantern festival at dusk",
    "Neon rain & tranquil midnight skyline mist",
]


def _scenario_path(day: dt.date, period: CyclePeriod = "day") -> Path:
    ensure_data_dirs()
    return SCENARIOS_DIR / f"{day.isoformat()}_{period}.json"


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


def _recent_theme_names(limit: int = 12) -> list[str]:
    ensure_data_dirs()
    files = sorted(SCENARIOS_DIR.glob("*.json"), reverse=True)[:limit]
    themes: list[str] = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "scenarios" in data:
                for sc in data.get("scenarios", []):
                    name = sc.get("theme_name")
                    if name:
                        themes.append(str(name))
            else:
                name = data.get("theme_name")
                if name:
                    themes.append(str(name))
        except Exception:
            continue
    return themes


def fallback_scenario(
    day: dt.date | None = None,
    period: CyclePeriod | None = None,
) -> DailyScenario:
    """Offline emergency fallback only used if all network/API calls fail."""
    slot_day, slot_period = current_cycle_slot()
    resolved_day = (day or slot_day).isoformat()
    resolved_period = period or slot_period

    common_shifts = TimeShifts(
        morning=TimeShift(brightness=1.0, speed=0.95),
        afternoon=TimeShift(brightness=1.0, speed=1.0),
        night=TimeShift(brightness=0.85, speed=0.75),
    )
    common_mods = ContextModifiers(
        on_music_playing=StateModifier(
            speed_mult=1.25,
            opacity_mult=1.08,
            cameo_freq_mult=1.3,
            actor_behavior_override="float_drift",
        ),
        on_deep_focus=StateModifier(
            speed_mult=0.55,
            opacity_mult=0.72,
            cameo_freq_mult=0.4,
            actor_behavior_override="none",
        ),
        on_idle_break=StateModifier(
            speed_mult=0.95,
            opacity_mult=1.05,
            cameo_freq_mult=1.55,
            actor_behavior_override="none",
        ),
    )

    if resolved_period == "day":
        return DailyScenario(
            date=resolved_day,
            theme_name="Nắng Sớm & Đầm Sen",
            tagline="Ánh nắng mai dịu mát cùng đóa sen khẽ nở và cánh hoa bay trong làn gió nhẹ.",
            mood="fresh",
            movie_speed_pct=68,
            cameo_rest_sec=75,
            palette=Palette(
                primary="#5FA8D3",
                secondary="#BCE0C8",
                accent="#F2B880",
                opacity_scale=0.84,
            ),
            time_shifts=common_shifts,
            modifiers=common_mods,
            global_components=[
                ComponentConfig(
                    type="asset_swarm",
                    zone="corners",
                    presence="continuous",
                    behavior="edge_peek",
                    density=0.5,
                    speed=0.6,
                    size=0.95,
                    style="lotus",
                    avoid_cursor=True,
                ),
                ComponentConfig(
                    type="asset_swarm",
                    zone="left_right_margins",
                    presence="continuous",
                    behavior="fall",
                    density=0.45,
                    speed=0.55,
                    size=0.9,
                    style="cherry-blossom",
                    avoid_cursor=True,
                ),
                ComponentConfig(
                    type="animated_actor",
                    zone="corners",
                    presence="cameo",
                    behavior="float_drift",
                    density=0.45,
                    speed=0.6,
                    size=1.05,
                    style="butterfly",
                    avoid_cursor=True,
                ),
            ],
        )

    return DailyScenario(
        date=resolved_day,
        theme_name="Mưa Đêm & Đèn Lồng",
        tagline="Ánh sao đêm dịu nhẹ cùng đèn lồng ấm áp và chú cá voi lướt qua góc màn hình.",
        mood="cozy",
        movie_speed_pct=52,
        cameo_rest_sec=95,
        palette=Palette(
            primary="#6C8EAD",
            secondary="#F4C095",
            accent="#E07A5F",
            opacity_scale=0.84,
        ),
        time_shifts=common_shifts,
        modifiers=common_mods,
        global_components=[
            ComponentConfig(
                type="asset_swarm",
                zone="top_edge",
                presence="continuous",
                behavior="drift",
                density=0.55,
                speed=0.65,
                size=0.9,
                style="glowing-star",
                avoid_cursor=True,
            ),
            ComponentConfig(
                type="asset_swarm",
                zone="corners",
                presence="continuous",
                behavior="edge_peek",
                density=0.45,
                speed=0.55,
                size=0.95,
                style="diya-lamp",
                avoid_cursor=True,
            ),
            ComponentConfig(
                type="animated_actor",
                zone="bottom_edge",
                presence="cameo",
                behavior="parade_pass",
                density=0.5,
                speed=0.65,
                size=1.1,
                style="whale",
                avoid_cursor=True,
            ),
        ],
    )


def _thinking_config_for_model(model_name: str) -> types.ThinkingConfig | None:
    low = model_name.lower()
    if low.startswith("gemini-3"):
        return types.ThinkingConfig(thinking_level="LOW")
    if low.startswith("gemini-2.5"):
        return types.ThinkingConfig(thinking_budget=0)
    return None


def _call_gemini_structured(prompt: str, schema_cls, temperature: float = 1.1):
    api_key = load_api_key()
    if not api_key:
        raise RuntimeError("GOOGLE_AI_API_KEY not found in .env")

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version="v1beta", timeout=20_000),
    )

    now = time.monotonic()
    ready = [m for m in GEMINI_FALLBACK_MODELS if _MODEL_COOLDOWN_UNTIL.get(m, 0.0) <= now]
    candidates = ready if ready else list(GEMINI_FALLBACK_MODELS)

    last_error: Exception | None = None
    for model_name in candidates:
        thinking_cfg = _thinking_config_for_model(model_name)
        for attempt_thinking in ([thinking_cfg, None] if thinking_cfg is not None else [None]):
            try:
                logger.info("Requesting %s from %s...", schema_cls.__name__, model_name)
                cfg_kwargs = {
                    "response_mime_type": "application/json",
                    "response_schema": schema_cls,
                    "temperature": temperature,
                }
                if attempt_thinking is not None:
                    cfg_kwargs["thinking_config"] = attempt_thinking

                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(**cfg_kwargs),
                )
                if response.text:
                    logger.info("%s generated successfully with %s.", schema_cls.__name__, model_name)
                    return schema_cls.model_validate_json(response.text)
            except Exception as exc:
                last_error = exc
                err_str = str(exc)
                if attempt_thinking is not None and "INVALID_ARGUMENT" in err_str and "thinking" in err_str.lower():
                    logger.info("Model %s rejected thinking_config; retrying without it...", model_name)
                    continue
                if any(tok in err_str for tok in ("503", "504", "UNAVAILABLE", "DEADLINE_EXCEEDED")):
                    _MODEL_COOLDOWN_UNTIL[model_name] = time.monotonic() + _COOLDOWN_SEC
                logger.warning("Model %s unavailable (%s); trying next fallback...", model_name, exc)
                break

    raise RuntimeError(f"All Gemini models failed: {last_error}")


def _build_prompt(
    today: dt.date,
    period: CyclePeriod,
    profiles: list[AppProfile] | None,
    recent_themes: list[str],
    disliked_themes: list[str],
) -> str:
    weekday = today.strftime("%A")
    catalog_sample = sample_catalog_for_prompt(max_items=95)
    catalog_lines = "\n".join(
        f"  - {cat}: {', '.join(slugs)}" for cat, slugs in catalog_sample.items()
    )
    if random.random() < 0.5:
        lens = random.choice(DAY_CREATIVE_LENSES if period == "day" else NIGHT_CREATIVE_LENSES)
        creative_line = f'Creative inspiration seed: "{lens}".'
    else:
        creative_line = (
            "Creative direction: Completely open — invent ANY surprising, original aesthetic theme fitting this time of day "
            "(think beyond clichés: floating libraries, coral gardens, glowing terrariums, starry bakeries, paper lanterns, misty workshops, etc.)."
        )
    if period == "day":
        cycle_guidance = (
            "Active cycle: DAYTIME (06:00–18:00). Favor bright, airy, sunlit, crisp pastel, "
            "botanical, sky, or refreshing watercolor aesthetics suitable for daytime work."
        )
    else:
        cycle_guidance = (
            "Active cycle: EVENING/NIGHT (18:00–06:00). Favor warm, cozy, moonlit, starry, "
            "bioluminescent, rainy-lantern, or tranquil nocturnal aesthetics gentle on the eyes at night."
        )
    avoid_list = list(dict.fromkeys(recent_themes + disliked_themes))
    avoid_clause = (
        f"STRICTLY AVOID repeating or closely resembling these previous/disliked themes: {', '.join(avoid_list)}."
        if avoid_list
        else "Create an unexpected, memorable, visually cohesive theme."
    )

    return f"""You are the free-spirited Daily Art Director for DailyOverlay, an aesthetic ambient desktop overlay on Windows.
Today is {today.isoformat()} ({weekday}).
{cycle_guidance}
{creative_line}

Design ONE cohesive, original daily visual scenario (plus lightweight context modifiers). Surprise the user — NEVER repeat clichés!

Guidelines:
1. Keep opacity_scale comfortable and clear (0.76 - 0.90), with harmonious hex colors (primary, secondary, accent).
2. {avoid_clause}
3. Provide 2-3 harmonious `global_components` — freely mix `asset_swarm` layers and/or `animated_actor` cameos (all swarm layers naturally share and layer across empty pockets together without clumping).
4. Tailor the rhythm via `movie_speed_pct` (`45–85`%: slower 45–55 for zen/night, livelier 70–85 for fresh/whimsical), `cameo_rest_sec` (`50–150`s between cameo visits), and each component's `relocate_feel` (`"auto"` | `"glide"` for creatures | `"dissolve"` for soft stationary blooms/lanterns/clouds).
5. Populate `modifiers`:
   - `on_music_playing`: livelier speed (`1.15-1.4`) and optionally shift `actor_behavior_override` (`"parade_pass"` or `"float_drift"`) so actors cruise joyfully when music plays.
   - `on_deep_focus`: calmer (`speed_mult: 0.45-0.65`, `opacity_mult: 0.65-0.8`, `cameo_freq_mult: 0.35-0.55`).
   - `on_idle_break`: slightly more lively cameos (`cameo_freq_mult: 1.3-1.8`).
6. Available 100% Asset-Driven Engine Primitives (pick exact slugs from the catalog below for `style`):
   - `asset_swarm`: renders a coordinated group of animated WebP assets from the catalog:
     * `behavior: "flock"` — living Boids swimmers/flyers (`fish`, `jellyfish`, `butterfly`, `bird`, `bee`, `dolphin`...) that swim/fly smoothly across empty zones when switching apps.
     * `behavior: "drift"` — slow drifting horizon elements (`cloud`, `glowing-star`, `sparkles`, `sailboat`...).
     * `behavior: "fall"` — gentle breeze-swayed falling shower (`cherry-blossom`, `maple-leaf`, `fallen-leaf`, `snowflake`, `droplet`...).
     * `behavior: "edge_peek"` — translucent swaying edge/corner elements (`lotus`, `plant`, `blossom`, `diya-lamp`, `evergreen-tree`...).
   - `animated_actor`: 1 spotlight animated WebP cameo (`perch_hop` | `float_drift` | `corner_companion` | `parade_pass`).
7. Public Animated WebP Asset Catalog (use exact slugs for `style`):
{catalog_lines}
8. Write `tagline` in natural, poetic Vietnamese (1 short sentence).
"""


def generate_daily_scenario(
    day: dt.date | None = None,
    period: CyclePeriod | None = None,
    profiles: list[AppProfile] | None = None,
    extra_avoid: list[str] | None = None,
) -> DailyScenario:
    slot_day, slot_period = current_cycle_slot()
    today = day or slot_day
    resolved_period = period or slot_period
    recent = _recent_theme_names(limit=12) + (extra_avoid or [])
    disliked = load_disliked_themes()
    prompt = _build_prompt(today, resolved_period, profiles, recent, disliked)

    scenario: DailyScenario = _call_gemini_structured(prompt, DailyScenario, temperature=1.15)
    scenario.date = today.isoformat()
    return scenario


def generate_pinned_style_for_profile(profile: AppProfile) -> PinnedAppStyle:
    """Ask Gemini to design a bespoke non-intrusive style tailored strictly to one app's Zero-PII wireframe."""
    catalog_sample = sample_catalog_for_prompt(max_items=80)
    catalog_lines = "\n".join(
        f"  - {cat}: {', '.join(slugs)}" for cat, slugs in catalog_sample.items()
    )
    wireframe_summary = summarize_wireframe_for_prompt(
        profile.layout_wireframe,
        profile.custom_allowed_zones,
        profile.custom_forbidden_zones,
    )

    prompt = f"""You are designing a dedicated, long-lived **pinned overlay style** tailored specifically to one app/website.
This style stays active whenever the user uses this app until they choose to unpin it, so it MUST be charming, context-fitting, and NEVER annoying.

App Profile:
- Name: {profile.name}
- Preferred safe zone: {profile.preferred_zone}
- User vibe note: {profile.preferred_vibe}
- Zero-PII UI Layout Wireframe: {wireframe_summary}

Choreography & anti-annoyance rules:
1. Pick 1-2 components tailored strictly to the wireframe's empty/edge space without covering `main_content`:
   - If the app has `media_cards` or bottom open area and you pick a walking/climbing creature (`cat-face`, `fox-face`, `panda`, etc.): use `animated_actor` with `perch_hop` and `presence: "cameo"` so it visits briefly then leaves.
   - If the app has wide `empty_space` margins (like Docs/Notion) and you pick swimmers/flyers: use `asset_swarm` (`flock` / `drift` / `fall`) or `animated_actor` (`float_drift` / `parade_pass` with `presence: "cameo"`).
   - In compact workspaces: prefer `presence: "peek_fade"` so edge_peek/drift/fall layers peek gently for ~12s and fade out gracefully without cluttering UI!
2. Available 100% Asset-Driven Primitives (pick exact catalog slugs below for `style`):
   - `asset_swarm`: `behavior` = `"flock"` | `"drift"` | `"fall"` | `"edge_peek"`.
   - `animated_actor`: `behavior` = `"perch_hop"` | `"float_drift"` | `"corner_companion"` | `"parade_pass"`.
{catalog_lines}
3. Write `style_name` and `tagline` in natural Vietnamese.
"""
    return _call_gemini_structured(prompt, PinnedAppStyle, temperature=1.05)


def save_daily_deck(deck: DailyDeck) -> Path:
    target_day = dt.date.fromisoformat(deck.date)
    path = _scenario_path(target_day, deck.period)
    path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_or_create_daily_deck(
    force_new: bool = False,
    profiles: list[AppProfile] | None = None,
    now: dt.datetime | None = None,
) -> DailyDeck:
    slot_day, slot_period = current_cycle_slot(now)
    path = _scenario_path(slot_day, slot_period)
    if path.exists() and not force_new:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "scenarios" in raw:
                deck = DailyDeck.model_validate(raw)
                deck.date = slot_day.isoformat()
                deck.period = slot_period
                if deck.scenarios:
                    return deck
            else:
                # Auto-migrate legacy single-scenario JSON into a DailyDeck
                sc = DailyScenario.model_validate(raw)
                deck = DailyDeck(
                    date=slot_day.isoformat(),
                    period=slot_period,
                    active_index=0,
                    scenarios=[sc],
                )
                save_daily_deck(deck)
                return deck
        except Exception as exc:
            logger.warning("Failed to load cached deck %s: %s", path, exc)

    try:
        sc = generate_daily_scenario(slot_day, period=slot_period, profiles=profiles)
    except Exception as exc:
        logger.error("Gemini generation failed (%s); falling back to default scenario.", exc)
        sc = fallback_scenario(slot_day, period=slot_period)

    deck = DailyDeck(
        date=slot_day.isoformat(),
        period=slot_period,
        active_index=0,
        scenarios=[sc],
    )
    save_daily_deck(deck)
    return deck


def append_scenario_to_deck(
    deck: DailyDeck,
    profiles: list[AppProfile] | None = None,
) -> DailyScenario:
    today = dt.date.fromisoformat(deck.date)
    existing_names = [s.theme_name for s in deck.scenarios]
    sc = generate_daily_scenario(
        today,
        period=deck.period,
        profiles=profiles,
        extra_avoid=existing_names,
    )
    deck.scenarios.append(sc)
    save_daily_deck(deck)
    return sc


def cycle_next_scenario(deck: DailyDeck) -> tuple[DailyScenario, bool]:
    """Advance immediately (0s) to the next cached scenario in today's deck."""
    if not deck.scenarios:
        raise ValueError("Deck is empty")
    deck.active_index = (deck.active_index + 1) % len(deck.scenarios)
    save_daily_deck(deck)
    needs_refill = len(deck.scenarios) < DAILY_DECK_SIZE
    return deck.active_scenario, needs_refill


def dislike_active_scenario(deck: DailyDeck) -> tuple[DailyScenario | None, DailyDeck]:
    """Blacklist current theme, drop it from today's deck, and return next cached one if available."""
    if not deck.scenarios:
        return None, deck
    idx = max(0, min(deck.active_index, len(deck.scenarios) - 1))
    removed = deck.scenarios.pop(idx)
    record_disliked_theme(removed.theme_name)
    if deck.scenarios:
        deck.active_index = idx % len(deck.scenarios)
        save_daily_deck(deck)
        return deck.active_scenario, deck
    deck.active_index = 0
    save_daily_deck(deck)
    return None, deck
