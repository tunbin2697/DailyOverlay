from __future__ import annotations

import random
from dataclasses import dataclass

from daily_overlay.ai.schema import PresenceMode


@dataclass
class VisitVariation:
    corner_index: int
    speed_jitter: float
    size_jitter: float
    y_ratio: float


class OrganicCameoScheduler:
    """Bounded organic randomness scheduler for non-intrusive cameos and peek-fade ambience.

    Guarantees a strict minimum off-screen cooldown (>=45s after a visit ends), uses a
    gradual hazard-rate arrival afterward so visits never feel robotic/predictable, defers
    appearing while the user is actively typing/clicking, and varies angle/speed each visit.
    """

    def __init__(
        self,
        presence: PresenceMode,
        first_visit_delay: float | None = None,
        min_cooldown_override: float | None = None,
    ) -> None:
        self.presence = presence
        self._continuous = presence == "continuous"
        self.alpha: float = 1.0 if self._continuous else 0.0
        self.variation = self._roll_variation()

        if presence == "peek_fade":
            self._visit_range = (10.0, 16.0)
            self._min_cooldown = (
                max(45.0, float(min_cooldown_override) * 0.8)
                if min_cooldown_override is not None
                else 55.0
            )
            self._max_wait = max(self._min_cooldown + 40.0, self._min_cooldown * 2.4)
            self._fade_in_rate = 1.4
            self._fade_out_rate = 1.1
        else:  # "cameo"
            self._visit_range = (14.0, 28.0)
            self._min_cooldown = (
                max(45.0, float(min_cooldown_override))
                if min_cooldown_override is not None
                else 70.0
            )
            self._max_wait = max(self._min_cooldown + 40.0, self._min_cooldown * 2.4)
            self._fade_in_rate = 1.8
            self._fade_out_rate = 1.3

        if self._continuous:
            self.state = "visiting"
            self._visit_remaining = 999999.0
            self._cooldown_elapsed = 0.0
            self._first_delay = 0.0
        else:
            self.state = "first_preview"
            self._first_delay = (
                first_visit_delay if first_visit_delay is not None else random.uniform(2.0, 5.5)
            )
            self._visit_remaining = 0.0
            self._cooldown_elapsed = 0.0

    @staticmethod
    def _roll_variation() -> VisitVariation:
        return VisitVariation(
            corner_index=random.randint(0, 1),
            speed_jitter=random.uniform(0.82, 1.18),
            size_jitter=random.uniform(0.90, 1.12),
            y_ratio=random.uniform(0.28, 0.80),
        )

    @property
    def is_visible(self) -> bool:
        return self._continuous or self.state == "visiting" or self.alpha > 0.01

    def _begin_visit(self) -> None:
        self.state = "visiting"
        self._visit_remaining = random.uniform(*self._visit_range)
        self._cooldown_elapsed = 0.0
        self.variation = self._roll_variation()

    def force_cooldown(self) -> None:
        """Immediately enter the hard off-screen cooldown phase (useful for testing or resets)."""
        if self._continuous:
            return
        self.state = "cooldown"
        self._visit_remaining = 0.0
        self._cooldown_elapsed = 0.0

    def update(
        self,
        dt: float,
        idle_seconds: float,
        cursor_near: bool = False,
        freq_mult: float = 1.0,
    ) -> bool:
        """Advance scheduler by dt seconds. Returns True on the exact tick a new visit starts."""
        if self._continuous:
            self.alpha = 1.0
            return False

        dt = max(0.0, min(dt, 1.0))
        started = False
        user_busy = (idle_seconds < 2.2) or cursor_near

        if self.state == "first_preview":
            self._first_delay -= dt
            if self._first_delay <= 0.0 and not user_busy:
                self._begin_visit()
                started = True
        elif self.state == "visiting":
            self._visit_remaining -= dt
            if self._visit_remaining <= 0.0:
                self.state = "cooldown"
                self._cooldown_elapsed = 0.0
        elif self.state == "cooldown":
            self._cooldown_elapsed += dt
            eff_min = max(45.0, self._min_cooldown / max(0.5, min(2.0, freq_mult)))
            eff_max = max(eff_min + 25.0, self._max_wait / max(0.5, min(2.0, freq_mult)))
            if self._cooldown_elapsed >= eff_min and not user_busy:
                if self._cooldown_elapsed >= eff_max:
                    self._begin_visit()
                    started = True
                else:
                    # Gradual organic hazard rate so appearance timing feels alive & unpredictable
                    span = max(12.0, (eff_max - self._cooldown_elapsed) * 0.55)
                    prob = min(0.5, dt / span)
                    if random.random() < prob:
                        self._begin_visit()
                        started = True

        target_alpha = 1.0 if self.state == "visiting" else 0.0
        rate = self._fade_in_rate if target_alpha > self.alpha else self._fade_out_rate
        self.alpha += (target_alpha - self.alpha) * min(1.0, dt * rate)
        if abs(target_alpha - self.alpha) < 0.004:
            self.alpha = target_alpha

        return started
