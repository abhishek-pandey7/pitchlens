"""Control vs chaos index (§8.5): is the match being played on someone's terms, or is it scrappy?

Four components over a trailing 5-minute window, each standardised against baselines measured on the
synthetic league (see `BASELINES`), then combined into 0 (chaos) .. 100 (control):

    + passes per possession   (patient circulation)
    + pass completion
    - turnovers per minute    (end-to-end, loose play)
    - fouls and cards per minute (stoppages, scrappiness)

Every component is returned alongside the index so insights can say *why* the match feels chaotic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pitchlens.schema.events import EventType, MatchEvent, Outcome

from .windows import EventWindow

WINDOW_MS = 5 * 60_000
MIN_SPAN_MS = 2 * 60_000

# (mean, sd) of each 5-minute component across organic simulated matches (calibrated, see tests).
BASELINES: dict[str, tuple[float, float]] = {
    "passes_per_possession": (3.31, 0.88),
    "pass_completion": (0.786, 0.059),
    "turnovers_per_min": (3.54, 0.99),
    "fouls_cards_per_min": (0.32, 0.26),
}
SIGNS = {"passes_per_possession": 1, "pass_completion": 1, "turnovers_per_min": -1, "fouls_cards_per_min": -1}
STEEPNESS = 1.6


@dataclass(frozen=True, slots=True)
class ControlReading:
    index: float  # 0 chaos .. 100 control
    components: dict[str, float]
    z: dict[str, float]

    @property
    def label(self) -> str:
        return "control" if self.index >= 60 else "chaos" if self.index <= 40 else "balanced"


class ControlChaos:
    def __init__(self) -> None:
        self.window = EventWindow(WINDOW_MS)
        self._first_ms: int | None = None

    def observe(self, e: MatchEvent) -> None:
        if self._first_ms is None:
            self._first_ms = e.match_ms
        ms = e.match_ms
        if e.type is EventType.PASS:
            self.window.add(ms, "passes")
            if e.outcome is Outcome.COMPLETE:
                self.window.add(ms, "completed")
        elif e.type is EventType.POSSESSION_CHANGE:
            self.window.add(ms, "possessions")
        elif e.type in (EventType.FOUL, EventType.CARD):
            self.window.add(ms, "fouls_cards")

    def reading(self, now_ms: int) -> ControlReading | None:
        if self._first_ms is None or now_ms - self._first_ms < MIN_SPAN_MS:
            return None
        minutes = self.window.span_ms(now_ms, self._first_ms) / 60_000
        passes = self.window.total("passes", now_ms)
        possessions = max(1.0, self.window.total("possessions", now_ms))
        components = {
            "passes_per_possession": passes / possessions,
            "pass_completion": self.window.total("completed", now_ms) / passes if passes else 0.0,
            "turnovers_per_min": self.window.total("possessions", now_ms) / minutes,
            "fouls_cards_per_min": self.window.total("fouls_cards", now_ms) / minutes,
        }
        z = {k: (v - BASELINES[k][0]) / BASELINES[k][1] for k, v in components.items()}
        score = sum(SIGNS[k] * z[k] for k in z) / len(z)
        index = 100 / (1 + math.exp(-STEEPNESS * score))
        return ControlReading(
            index=round(index, 1),
            components={k: round(v, 3) for k, v in components.items()},
            z={k: round(v, 2) for k, v in z.items()},
        )
