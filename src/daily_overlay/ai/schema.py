from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from daily_overlay.config import CyclePeriod, ZoneType

ComponentType = Literal["animated_actor", "asset_swarm"]
MoodType = Literal["calm", "cozy", "fresh", "dreamy", "focus", "whimsical"]
PresenceMode = Literal["continuous", "cameo", "peek_fade"]
MotionBehavior = Literal[
    "perch_hop",
    "float_drift",
    "corner_companion",
    "parade_pass",
    "flock",
    "drift",
    "fall",
    "edge_peek",
]
BehaviorOverride = Literal["none", "perch_hop", "float_drift", "corner_companion", "parade_pass"]
RelocateFeel = Literal["auto", "glide", "dissolve"]


class Palette(BaseModel):
    primary: str = Field(description="Hex color, e.g. #7FB3D5")
    secondary: str = Field(description="Hex color, e.g. #F5CBA7")
    accent: str = Field(description="Hex color, e.g. #E59866")
    opacity_scale: float = Field(
        default=0.82,
        ge=0.5,
        le=0.95,
        description="Overall ambient visibility (0.72-0.90 recommended)",
    )


class TimeShift(BaseModel):
    brightness: float = Field(default=1.0, ge=0.65, le=1.2)
    speed: float = Field(default=1.0, ge=0.4, le=1.4)


class TimeShifts(BaseModel):
    morning: TimeShift = Field(default_factory=lambda: TimeShift(brightness=1.0, speed=0.95))
    afternoon: TimeShift = Field(default_factory=lambda: TimeShift(brightness=1.0, speed=1.0))
    night: TimeShift = Field(default_factory=lambda: TimeShift(brightness=0.82, speed=0.75))


class StateModifier(BaseModel):
    speed_mult: float = Field(default=1.0, ge=0.35, le=1.6)
    opacity_mult: float = Field(default=1.0, ge=0.45, le=1.25)
    cameo_freq_mult: float = Field(default=1.0, ge=0.25, le=2.0)
    actor_behavior_override: BehaviorOverride = "none"


class ContextModifiers(BaseModel):
    on_music_playing: StateModifier = Field(
        default_factory=lambda: StateModifier(
            speed_mult=1.22,
            opacity_mult=1.06,
            cameo_freq_mult=1.25,
            actor_behavior_override="none",
        )
    )
    on_deep_focus: StateModifier = Field(
        default_factory=lambda: StateModifier(
            speed_mult=0.55,
            opacity_mult=0.72,
            cameo_freq_mult=0.45,
            actor_behavior_override="none",
        )
    )
    on_idle_break: StateModifier = Field(
        default_factory=lambda: StateModifier(
            speed_mult=0.95,
            opacity_mult=1.05,
            cameo_freq_mult=1.55,
            actor_behavior_override="none",
        )
    )


class ComponentConfig(BaseModel):
    type: ComponentType
    zone: ZoneType
    presence: PresenceMode = Field(
        default="continuous",
        description=(
            "'continuous' for calm background ambience; "
            "'cameo' for animated actors to visit briefly and rest off-screen; "
            "'peek_fade' for edge foliage/lantern swarms or actors to peek gently and fade out."
        ),
    )
    behavior: MotionBehavior = Field(
        default="float_drift",
        description=(
            "Motion rule matching `type`: "
            "For `animated_actor`: 'perch_hop' | 'float_drift' | 'corner_companion' | 'parade_pass'. "
            "For `asset_swarm`: 'flock' (boids swimmers/flyers) | 'drift' (clouds/stars/sparkles) | "
            "'fall' (petals/leaves/snow/rain) | 'edge_peek' (swaying flowers/plants/lanterns)."
        ),
    )
    density: float = Field(default=0.7, ge=0.25, le=1.0)
    speed: float = Field(default=0.75, ge=0.2, le=1.4)
    size: float = Field(default=1.05, ge=0.6, le=1.8)
    style: str = Field(
        default="sparkles",
        description=(
            "Exact asset slug from the Public Animated WebP Catalog "
            "(e.g. fish, jellyfish, cloud, cherry-blossom, maple-leaf, snowflake, lotus, plant, "
            "glowing-star, diya-lamp, butterfly, fox-face, whale, sparkles, etc.)."
        ),
    )
    avoid_cursor: bool = True
    relocate_feel: RelocateFeel = Field(
        default="auto",
        description=(
            "Relocation style: 'auto' (geometry-aware glide vs slide-fade), "
            "'glide' (prefer gliding for creatures), "
            "'dissolve' (prefer soft slide-fade for lanterns/flowers/clouds)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        d = dict(data)
        raw_type = str(d.get("type", "asset_swarm")).strip()
        raw_style = str(d.get("style", "sparkles")).strip()
        low_style = raw_style.lower()

        if low_style == "default":
            d["style"] = "sparkles"

        if raw_type == "boids_fish":
            d["type"] = "asset_swarm"
            d["behavior"] = "flock"
            d["style"] = "jellyfish" if low_style == "neon" else "fish"
        elif raw_type == "ambient_clouds":
            d["type"] = "asset_swarm"
            d["behavior"] = "drift"
            d["style"] = "sparkles" if low_style == "aurora_haze" else "cloud"
        elif raw_type == "swaying_foliage":
            d["type"] = "asset_swarm"
            d["behavior"] = "edge_peek"
            d["style"] = {
                "bamboo": "plant",
                "sakura": "cherry-blossom",
                "fern": "leafy-green",
                "willow": "lotus",
            }.get(low_style, "lotus")
        elif raw_type == "floating_particles":
            d["type"] = "asset_swarm"
            d["behavior"] = "drift" if low_style in ("stars", "fireflies", "dust", "bubbles") else "fall"
            d["style"] = {
                "petals": "cherry-blossom",
                "snow": "snowflake",
                "rain": "droplet",
                "stars": "glowing-star",
                "fireflies": "sparkles",
                "bubbles": "droplet",
                "dust": "sparkles",
            }.get(low_style, "sparkles")
        elif raw_type == "ambient_waves":
            d["type"] = "asset_swarm"
            d["behavior"] = "drift"
            d["style"] = {
                "ocean": "droplet",
                "aurora": "sparkles",
                "silk": "lotus",
            }.get(low_style, "sparkles")
        elif raw_type not in ("animated_actor", "asset_swarm"):
            d["type"] = "asset_swarm"

        final_type = d.get("type", "asset_swarm")
        beh = d.get("behavior")
        if final_type == "asset_swarm" and beh not in ("flock", "drift", "fall", "edge_peek"):
            d["behavior"] = "drift"
        elif final_type == "animated_actor" and beh not in (
            "perch_hop",
            "float_drift",
            "corner_companion",
            "parade_pass",
        ):
            d["behavior"] = "float_drift"

        return d


class DailyScenario(BaseModel):
    date: str = Field(description="ISO date YYYY-MM-DD")
    theme_name: str = Field(description="Short poetic theme title for today")
    tagline: str = Field(description="Short 1-sentence description in Vietnamese")
    mood: MoodType
    palette: Palette
    time_shifts: TimeShifts
    movie_speed_pct: int = Field(
        default=65,
        ge=45,
        le=85,
        description="Animated WebP loop playback speed % (45-55 zen/calm, 60-70 gentle, 75-85 lively).",
    )
    cameo_rest_sec: int = Field(
        default=75,
        ge=50,
        le=150,
        description="Minimum seconds between cameo visits (50-75 lively, 80-110 balanced, 115-150 ultra-calm).",
    )
    modifiers: ContextModifiers = Field(
        default_factory=ContextModifiers,
        description="Subtle real-time adjustments when user plays music, enters deep focus typing, or takes an idle break",
    )
    global_components: list[ComponentConfig] = Field(
        description=(
            "2-3 harmonious asset-driven components for this cycle "
            "(1-2 ambient asset_swarm layers + 1 animated_actor cameo); "
            "engine automatically relocates them smoothly into each app's empty zones."
        )
    )


class PinnedAppStyle(BaseModel):
    style_name: str = Field(description="Short title for this app's custom pinned overlay style")
    tagline: str = Field(description="Short 1-sentence Vietnamese description")
    components: list[ComponentConfig] = Field(
        description="1-2 components tailored strictly to this app's spatial wireframe without disturbing work"
    )


class DailyDeck(BaseModel):
    date: str
    period: CyclePeriod = "day"
    active_index: int = 0
    scenarios: list[DailyScenario] = Field(default_factory=list)

    @property
    def cycle_key(self) -> str:
        return f"{self.date}_{self.period}"

    @property
    def active_scenario(self) -> DailyScenario:
        if not self.scenarios:
            raise ValueError("DailyDeck has no scenarios")
        idx = max(0, min(self.active_index, len(self.scenarios) - 1))
        return self.scenarios[idx]
