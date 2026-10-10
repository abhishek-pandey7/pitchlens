"""Game rhythm (§8.6): tempo, stoppage share and rhythm breaks.

Tempo is on-ball actions per minute over a trailing 5-minute window. The match builds its own baseline
from one tempo sample per minute (after the opening 10 minutes); a rhythm break is a 5-minute tempo
more than 1.5 standard deviations from that baseline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pitchlens.schema.events import EventType, MatchEvent

from .windows import EventWindow

WINDOW_MS = 5 * 60_000
BASELINE_START_MS = 10 * 60_000
SAMPLE_EVERY_MS = 60_000
DEAD_BALL_GAP_MS = 8_000  # a gap this long between consecutive events is treated as a stoppage
BREAK_Z = 1.5

ON_BALL = {EventType.PASS, EventType.CARRY, EventType.DRIBBLE, EventType.SHOT}


@dataclass(frozen=True, slots=True)
class RhythmReading:
    tempo: float  # on-ball actions per minute (5-minute window)
    baseline: float | None
    z: float | None
    stoppage_share: float  # fraction of the window spent with the ball dead


class Rhythm:
    def __init__(self) -> None:
        self.window = EventWindow(WINDOW_MS)
        self._last_ms: int | None = None
        self._first_ms: int | None = None
        self._next_sample = BASELINE_START_MS
        self._n = 0
        self._mean = 0.0
        self._m2 = 0.0

    def observe(self, e: MatchEvent) -> None:
        ms = e.match_ms
        if self._first_ms is None:
            self._first_ms = ms
        if e.type in ON_BALL:
            self.window.add(ms, "actions")
        if (
            self._last_ms is not None
            and ms - self._last_ms >= DEAD_BALL_GAP_MS
            and e.type is not EventType.PERIOD_START
        ):
            self.window.add(ms, "dead_ms", ms - self._last_ms)
        self._last_ms = ms
        while ms >= self._next_sample:
            self._update_baseline(self.tempo(self._next_sample))
            self._next_sample += SAMPLE_EVERY_MS

    def tempo(self, now_ms: int) -> float:
        minutes = self.window.span_ms(now_ms, self._first_ms or 0) / 60_000
        return self.window.total("actions", now_ms) / minutes

    def _update_baseline(self, value: float) -> None:
        # Welford's online mean/variance.
        self._n += 1
        delta = value - self._mean
        self._mean += delta / self._n
        self._m2 += delta * (value - self._mean)

    def reading(self, now_ms: int) -> RhythmReading:
        tempo = self.tempo(now_ms)
        span = self.window.span_ms(now_ms, self._first_ms or 0)
        stoppage = min(1.0, self.window.total("dead_ms", now_ms) / span)
        if self._n >= 5:
            sd = math.sqrt(self._m2 / (self._n - 1))
            z = (tempo - self._mean) / sd if sd > 0 else 0.0
            return RhythmReading(round(tempo, 2), round(self._mean, 2), round(z, 2), round(stoppage, 3))
        return RhythmReading(round(tempo, 2), None, None, round(stoppage, 3))

    @staticmethod
    def is_break(reading: RhythmReading) -> bool:
        return reading.z is not None and abs(reading.z) > BREAK_Z
