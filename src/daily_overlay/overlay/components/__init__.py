"""Procedural and animated WebP overlay visual components."""

from daily_overlay.ai.schema import ComponentConfig, Palette
from daily_overlay.overlay.components.base import OverlayComponent
from daily_overlay.overlay.components.boids_fish import BoidsFishComponent
from daily_overlay.overlay.components.cat_pet import AnimatedActorComponent, CatPetComponent
from daily_overlay.overlay.components.clouds import AmbientCloudsComponent
from daily_overlay.overlay.components.foliage import SwayingFoliageComponent
from daily_overlay.overlay.components.particles import FloatingParticlesComponent
from daily_overlay.overlay.components.waves import AmbientWavesComponent


def create_component(config: ComponentConfig, palette: Palette) -> OverlayComponent:
    if config.type == "ambient_waves":
        return AmbientWavesComponent(config, palette)
    if config.type == "boids_fish":
        return BoidsFishComponent(config, palette)
    if config.type == "floating_particles":
        return FloatingParticlesComponent(config, palette)
    if config.type in ("animated_actor", "cat_pet"):
        return AnimatedActorComponent(config, palette)
    if config.type == "swaying_foliage":
        return SwayingFoliageComponent(config, palette)
    if config.type == "ambient_clouds":
        return AmbientCloudsComponent(config, palette)
    return FloatingParticlesComponent(config, palette)


__all__ = [
    "OverlayComponent",
    "AmbientWavesComponent",
    "AmbientCloudsComponent",
    "BoidsFishComponent",
    "FloatingParticlesComponent",
    "AnimatedActorComponent",
    "CatPetComponent",
    "SwayingFoliageComponent",
    "create_component",
]
