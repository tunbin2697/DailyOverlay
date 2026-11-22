from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from daily_overlay.config import ZoneType

ComponentType = Literal[
    "ambient_waves",
    "boids_fish",
    "floating_particles",
    "swaying_foliage",
    "ambient_clouds",
    "animated_actor",
    "cat_pet",  # Backward-compatible alias for animated_actor
]
MoodType = Literal["calm", "cozy", "fresh", "dreamy", "focus", "whimsical"]
PresenceMode = Literal["continuous", "cameo"]
ActorBehavior = Literal["perch_hop", "float_drift", "corner_companion", "parade_pass"]


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


class ComponentConfig(BaseModel):
    type: ComponentType
    zone: ZoneType
    presence: PresenceMode = Field(
        default="continuous",
        description=(
            "'continuous' for calm background ambience; "
            "'cameo' for animated actors so they visit intermittently (20-35s) and rest off-screen (45-80s)."
        ),
    )
    behavior: ActorBehavior = Field(
        default="float_drift",
        description=(
            "Choreography for animated_actor: "
            "'perch_hop' (glides along thumbnails/edge), "
            "'float_drift' (drifts lazily inside zone), "
            "'corner_companion' (cozy animated companion resting in corner), "
            "'parade_pass' (occasionally glides across edge and exits)."
        ),
    )
    density: float = Field(default=0.7, ge=0.25, le=1.0)
    speed: float = Field(default=0.75, ge=0.2, le=1.4)
    size: float = Field(default=1.05, ge=0.6, le=1.8)
    style: str = Field(
        default="default",
        description=(
            "Visual style or public animated WebP asset ID. "
            "ambient_waves: ocean | aurora | silk. "
            "boids_fish: koi | neon | ink. "
            "floating_particles: fireflies | bubbles | petals | snow | dust | rain | stars. "
            "swaying_foliage: bamboo | sakura | fern | willow. "
            "ambient_clouds: mist | cumulus | sunset | aurora_haze. "
            "animated_actor: ANY slug from the provided Public Animated WebP Catalog (e.g. lotus, fox, whale, diya-lamp, butterfly, rocket, mushroom, sailboat, owl, panda, cherry-blossom, etc.) OR vector cat (calico | tuxedo | ginger | shadow)."
        ),
    )
    avoid_cursor: bool = True


class AppRule(BaseModel):
    rule_name: str
    match_process: list[str] = Field(default_factory=list)
    match_title_contains: list[str] = Field(default_factory=list)
    components: list[ComponentConfig] = Field(default_factory=list)


class DailyScenario(BaseModel):
    date: str = Field(description="ISO date YYYY-MM-DD")
    theme_name: str = Field(description="Short poetic theme title for today")
    tagline: str = Field(description="Short 1-sentence description in Vietnamese")
    mood: MoodType
    palette: Palette
    time_shifts: TimeShifts
    global_components: list[ComponentConfig] = Field(
        description="2 harmonious ambient components shown on desktop or unmatched apps"
    )
    app_rules: list[AppRule] = Field(
        description="Context-specific overlay rules matching the user's configured app profiles"
    )
