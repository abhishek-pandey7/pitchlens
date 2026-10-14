"""Physical metrics from tracking (§8.3): distance, high-speed running, sprints, top speed, ball speed,
and threshold alerts that trigger player-tag overlays.

Speeds are measured over a 1 s sliding window, which filters frame-to-frame jitter the way broadcast
tracking providers do. Distance never accumulates across half-time.

`PhysicalTracker` is the reference implementation. `RustPhysicalTracker` has the same interface and runs
on the Rust core (`pitchlens_core`, engine/core/src/physical.rs); `new_tracker` picks one following
`PITCHLENS_CORE`. Both take single frames (`apply`, the live path) or a range of a `Tracking`'s arrays at
once (`apply_range`, for seeks and whole-match analysis) with the same results.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pitchlens import core
from pitchlens.schema.enriched import AlertKind, PhysicalAlert
from pitchlens.schema.events import TrackingFrame

HSR_KMH = 19.8  # high-speed running
SPRINT_KMH = 25.2
SPRINT_MIN_MS = 1000
SPEED_WINDOW_MS = 1000

TOP_SPEED_THRESHOLDS_KMH = (33.0,)
DISTANCE_THRESHOLDS_KM = (5.0, 8.0, 10.0, 12.0)
SPRINT_COUNT_THRESHOLDS = (10, 20, 30)
UNITS = {AlertKind.TOP_SPEED: "km/h", AlertKind.DISTANCE: "km", AlertKind.SPRINT_COUNT: "sprints"}


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
        t = frame.match_ms
        self._begin_frame(period, t, frame.ball.x, frame.ball.y)
        alerts: list[PhysicalAlert] = []
        for tp in frame.players:
            alerts += self._step(self.player(tp.player_id), t, tp.x, tp.y)
        return alerts

    def apply_range(
        self,
        ms: np.ndarray,
        period: np.ndarray | int,
        ball: np.ndarray,
        xy: np.ndarray,
        player_ids: list[str],
        start: int,
        end: int,
    ) -> list[PhysicalAlert]:
        """Frames `start..end` of a `Tracking`'s arrays (`ms`, `period`, `ball`, `xy`, `player_ids`): the
        same results as `apply(tracking.frame(i), period)` for each, without building the frames.
        `period` is the per-frame array or one int for the whole range."""
        alerts: list[PhysicalAlert] = []
        fixed = isinstance(period, int | np.integer)
        periods = [int(period)] * (end - start) if fixed else period[start:end].tolist()  # type: ignore[index]
        players: list[PlayerPhysical | None] = [self.players.get(pid) for pid in player_ids]
        rows = zip(
            ms[start:end].tolist(), periods, ball[start:end].tolist(), xy[start:end].tolist(), strict=True
        )
        for t, p, (bx, by, _), cols in rows:
            # Rounded as Tracking.frame rounds them, so live frames and ranges give the same numbers.
            self._begin_frame(p, t, round(bx, 2), round(by, 2))
            for c, (x, y) in enumerate(cols):
                if math.isnan(x):
                    continue
                player = players[c]
                if player is None:  # first seen now: created in the same order as apply() would
                    player = players[c] = self.player(player_ids[c])
                alerts += self._step(player, t, round(x, 2), round(y, 2))
        return alerts

    def _begin_frame(self, period: int, t: int, ball_x: float, ball_y: float) -> None:
        if period != self._period_of_last_frame:
            for p in self.players.values():
                p.reset_period()
            self._ball_window.clear()
            self._period_of_last_frame = period
        self.ball_speed_kmh = _window_speed(self._ball_window, t, ball_x, ball_y)
        self.max_ball_speed_kmh = max(self.max_ball_speed_kmh, self.ball_speed_kmh)

    def _step(self, p: PlayerPhysical, t: int, x: float, y: float) -> list[PhysicalAlert]:
        if p._last is not None:
            step = math.hypot(x - p._last[1], y - p._last[2])
            p.distance_m += step
            if p.speed_kmh >= HSR_KMH:
                p.hsr_m += step
            if p.speed_kmh >= SPRINT_KMH:
                p.sprint_m += step
        p._last = (t, x, y)
        p.speed_kmh = _window_speed(p._window, t, x, y)
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
        return self._thresholds(p, t)

    def _thresholds(self, p: PlayerPhysical, t: int) -> list[PhysicalAlert]:
        checks = (
            (AlertKind.TOP_SPEED, p.speed_kmh, TOP_SPEED_THRESHOLDS_KMH),
            (AlertKind.DISTANCE, p.distance_m / 1000, DISTANCE_THRESHOLDS_KM),
            (AlertKind.SPRINT_COUNT, float(p.sprints), SPRINT_COUNT_THRESHOLDS),
        )
        alerts = []
        for kind, value, thresholds in checks:
            for threshold in thresholds:
                if value >= threshold and (kind, threshold) not in p._fired:
                    p._fired.add((kind, threshold))
                    alerts.append(_alert(kind, self.match_id, t, p.player_id, p.team_id, value, threshold))
        return alerts


class RustPhysicalTracker:
    """`PhysicalTracker` on the Rust core: the same interface and the same numbers (bit for bit; see
    engine/core/src/physical.rs). `players` and `player()` return snapshots, not live objects."""

    def __init__(self, match_id: str, team_of: dict[str, str]) -> None:
        assert core.native is not None
        self.match_id = match_id
        self.team_of = team_of
        self._native: Any = core.native.PhysicalTracker()

    @property
    def players(self) -> dict[str, PlayerPhysical]:
        return {row[0]: self._snapshot(row) for row in self._native.players()}

    def player(self, player_id: str) -> PlayerPhysical:
        return self._snapshot(self._native.player(player_id))

    @property
    def ball_speed_kmh(self) -> float:
        return float(self._native.ball_speed_kmh)

    @property
    def max_ball_speed_kmh(self) -> float:
        return float(self._native.max_ball_speed_kmh)

    def apply(self, frame: TrackingFrame, period: int) -> list[PhysicalAlert]:
        return self._alerts(self._native.apply(frame, period))

    def apply_range(
        self,
        ms: np.ndarray,
        period: np.ndarray | int,
        ball: np.ndarray,
        xy: np.ndarray,
        player_ids: list[str],
        start: int,
        end: int,
    ) -> list[PhysicalAlert]:
        """See `PhysicalTracker.apply_range`."""
        return self._alerts(self._native.apply_range(ms, period, ball, xy, player_ids, start, end))

    def _snapshot(self, row: tuple[str, float, float, float, int, float, float]) -> PlayerPhysical:
        pid, distance, hsr, sprint, sprints, top, speed = row
        return PlayerPhysical(pid, self.team_of.get(pid, ""), distance, hsr, sprint, sprints, top, speed)

    def _alerts(self, rows: list[tuple[str, str, int, float, float]]) -> list[PhysicalAlert]:
        return [
            _alert(AlertKind(kind), self.match_id, t, pid, self.team_of.get(pid, ""), value, threshold)
            for kind, pid, t, value, threshold in rows
        ]


Tracker = PhysicalTracker | RustPhysicalTracker
RUST = core.native is not None and hasattr(core.native, "PhysicalTracker")


def new_tracker(match_id: str, team_of: dict[str, str]) -> Tracker:
    """The Rust tracker when the core provides one (see `pitchlens.core`), the Python reference otherwise."""
    return RustPhysicalTracker(match_id, team_of) if RUST else PhysicalTracker(match_id, team_of)


def _alert(
    kind: AlertKind, match_id: str, t: int, player_id: str, team_id: str, value: float, threshold: float
) -> PhysicalAlert:
    return PhysicalAlert(
        kind=kind,
        match_id=match_id,
        match_ms=t,
        player_id=player_id,
        team_id=team_id,
        value=round(value, 2),
        threshold=threshold,
        unit=UNITS[kind],
    )


def _window_speed(window: deque[tuple[int, float, float]], t: int, x: float, y: float) -> float:
    """km/h over the last ~1 s of samples (oldest sample in the window to now)."""
    window.append((t, x, y))
    while window and t - window[0][0] > SPEED_WINDOW_MS:
        window.popleft()
    t0, x0, y0 = window[0]
    if t == t0:
        return 0.0
    return math.hypot(x - x0, y - y0) / ((t - t0) / 1000) * 3.6
