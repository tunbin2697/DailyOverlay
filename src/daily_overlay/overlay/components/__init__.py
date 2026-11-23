"""Pure 100% asset-driven animated WebP overlay visual components."""

from daily_overlay.ai.schema import ComponentConfig, Palette
from daily_overlay.config import MOVIE_PLAYBACK_SPEED_PCT
from daily_overlay.overlay.components.animated_actor import AnimatedActorComponent
from daily_overlay.overlay.components.asset_swarm import AssetSwarmComponent
from daily_overlay.overlay.components.base import OverlayComponent


def create_component(
    config: ComponentConfig,
    palette: Palette,
    movie_speed_pct: int = MOVIE_PLAYBACK_SPEED_PCT,
    cameo_rest_sec: float | None = None,
) -> OverlayComponent:
    if config.type == "animated_actor":
        return AnimatedActorComponent(
            config,
            palette,
            movie_speed_pct=movie_speed_pct,
            cameo_rest_sec=cameo_rest_sec,
        )
    return AssetSwarmComponent(
        config,
        palette,
        movie_speed_pct=movie_speed_pct,
    )


__all__ = [
    "OverlayComponent",
    "AnimatedActorComponent",
    "AssetSwarmComponent",
    "create_component",
]
