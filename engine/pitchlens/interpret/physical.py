"""Physical metrics from tracking (§8.3): distance, high-speed running, sprints, top speed, ball speed,
and threshold alerts that trigger player-tag overlays.

Speeds are measured over a 1 s sliding window, which filters frame-to-frame jitter the way broadcast
tracking providers do. Distance never accumulates across half-time.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from pitchlens.schema.enriched import AlertKind, PhysicalAlert
from pitchlens.schema.events import TrackingFrame

HSR_KMH = 19.8  # high-speed running
SPRINT_KMH = 25.2
SPRINT_MIN_MS = 1000
SPEED_WINDOW_MS = 1000

TOP_SPEED_THRESHOLDS_KMH = (33.0,)
DISTANCE_THRESHOLDS_KM = (5.0, 8.0, 10.0, 12.0)
SPRINT_COUNT_THRESHOLDS = (10, 20, 30)


@dataclass
class PlayerPhysical:
    player_id: str
    team_id: str
    distance_m: float = 0.0
    hsr_m: float = 0.0
    sprint_m: float = 0.0
    sprints: int = 0
    top_speed_kmh: float = 0.0
    speed_kmh: float = 0.0
    _window: deque[tuple[int, float, float]] = field(default_factory=deque, repr=False)
    _last: tuple[int, float, float] | None = field(default=None, repr=False)
    _above_sprint_since: int | None = field(default=None, repr=False)
    _sprint_counted: bool = field(default=False, repr=False)
    _fired: set[tuple[AlertKind, float]] = field(default_factory=set, repr=False)

    def reset_period(self) -> None:
        self._window.clear()
        self._last = None
        self._above_sprint_since = None
        self._sprint_counted = False


class PhysicalTracker:
    def __init__(self, match_id: str, team_of: dict[str, str]) -> None:
        self.match_id = match_id
        self.team_of = team_of
        self.players: dict[str, PlayerPhysical] = {}
        self.ball_speed_kmh = 0.0
        self.max_ball_speed_kmh = 0.0
        self._ball_window: deque[tuple[int, float, float]] = deque()
        self._period_of_last_frame: int | None = None

    def player(self, player_id: str) -> PlayerPhysical:
        if player_id not in self.players:
            self.players[player_id] = PlayerPhysical(player_id, self.team_of.get(player_id, ""))
        return self.players[player_id]

    def apply(self, frame: TrackingFrame, period: int) -> list[PhysicalAlert]:
        if period != self._period_of_last_frame:
            for p in self.players.values():
                p.reset_period()
            self._ball_window.clear()
            self._period_of_last_frame = period
        t = frame.match_ms
        alerts: list[PhysicalAlert] = []
        self.ball_speed_kmh = _window_speed(self._ball_window, t, frame.ball.x, frame.ball.y)
        self.max_ball_speed_kmh = max(self.max_ball_speed_kmh, self.ball_speed_kmh)
        for tp in frame.players:
            p = self.player(tp.player_id)
            if p._last is not None:
                step = math.hypot(tp.x - p._last[1], tp.y - p._last[2])
                p.distance_m += step
                if p.speed_kmh >= HSR_KMH:
                    p.hsr_m += step
                if p.speed_kmh >= SPRINT_KMH:
                    p.sprint_m += step
            p._last = (t, tp.x, tp.y)
            p.speed_kmh = _window_speed(p._window, t, tp.x, tp.y)
            p.top_speed_kmh = max(p.top_speed_kmh, p.speed_kmh)
            if p.speed_kmh >= SPRINT_KMH:
                if p._above_sprint_since is None:
                    p._above_sprint_since = t
                if not p._sprint_counted and t - p._above_sprint_since >= SPRINT_MIN_MS:
                    p.sprints += 1
                    p._sprint_counted = True
            else:
                p._above_sprint_since = None
                p._sprint_counted = False
            alerts += self._thresholds(p, t)
        return alerts

    def _thresholds(self, p: PlayerPhysical, t: int) -> list[PhysicalAlert]:
        checks = (
            (AlertKind.TOP_SPEED, p.speed_kmh, TOP_SPEED_THRESHOLDS_KMH, "km/h"),
            (AlertKind.DISTANCE, p.distance_m / 1000, DISTANCE_THRESHOLDS_KM, "km"),
            (AlertKind.SPRINT_COUNT, float(p.sprints), SPRINT_COUNT_THRESHOLDS, "sprints"),
        )
        alerts = []
        for kind, value, thresholds, unit in checks:
            for threshold in thresholds:
                if value >= threshold and (kind, threshold) not in p._fired:
                    p._fired.add((kind, threshold))
                    alerts.append(
                        PhysicalAlert(
                            kind=kind,
                            match_id=self.match_id,
                            match_ms=t,
                            player_id=p.player_id,
                            team_id=p.team_id,
                            value=round(value, 2),
                            threshold=threshold,
                            unit=unit,
                        )
                    )
        return alerts


def _window_speed(window: deque[tuple[int, float, float]], t: int, x: float, y: float) -> float:
    """km/h over the last ~1 s of samples (oldest sample in the window to now)."""
    window.append((t, x, y))
    while window and t - window[0][0] > SPEED_WINDOW_MS:
        window.popleft()
    t0, x0, y0 = window[0]
    if t == t0:
        return 0.0
    return math.hypot(x - x0, y - y0) / ((t - t0) / 1000) * 3.6
