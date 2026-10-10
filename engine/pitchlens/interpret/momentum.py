"""Territory, pressing and momentum (§8.4): rolling possession, field tilt, PPDA and decayed threat."""

from __future__ import annotations

from dataclasses import dataclass

from pitchlens.schema.enriched import MetricValue
from pitchlens.schema.events import EventType, MatchEvent, Outcome

from .geometry import FINAL_THIRD_X, OWN_DEFENSIVE_60_X, PRESS_ZONE_X, XY
from .passes import in_box
from .windows import DecayedSum, EventWindow, SegmentShare

POSSESSION_WINDOW_MS = 5 * 60_000
TILT_WINDOW_MS = 10 * 60_000
PPDA_WINDOW_MS = 10 * 60_000
PRESS_WINDOW_MS = 10 * 60_000
MOMENTUM_HALF_LIFE_MS = 3 * 60_000
PPDA_CAP = 40.0  # no defensive actions at all reads as "no press", not infinity

ON_BALL = {EventType.PASS, EventType.CARRY, EventType.DRIBBLE, EventType.SHOT}
# Standard PPDA definition: tackles, interceptions and fouls (loose-ball recoveries do not count).
DEFENSIVE_ACTIONS = {EventType.TACKLE, EventType.INTERCEPTION, EventType.FOUL}
THREAT_ENTRY_FINAL_THIRD = 0.2
THREAT_ENTRY_BOX = 0.5
THREAT_PER_XG = 3.0


@dataclass(frozen=True, slots=True)
class Observation:
    """An event seen from its team's attacking frame (None where it has no location)."""

    event: MatchEvent
    team_id: str
    start: XY | None
    end: XY | None
    metrics: dict[str, MetricValue]


class TeamDynamics:
    def __init__(self, home_id: str, away_id: str) -> None:
        self.home_id, self.away_id = home_id, away_id
        self.possession = SegmentShare(POSSESSION_WINDOW_MS)
        self.tilt = EventWindow(TILT_WINDOW_MS)
        self._ppda = EventWindow(PPDA_WINDOW_MS)
        self._press = EventWindow(PRESS_WINDOW_MS)
        self._first_ms: int | None = None
        self._threat = {
            home_id: DecayedSum(MOMENTUM_HALF_LIFE_MS),
            away_id: DecayedSum(MOMENTUM_HALF_LIFE_MS),
        }

    def other(self, club_id: str) -> str:
        return self.away_id if club_id == self.home_id else self.home_id

    def observe(self, ob: Observation) -> None:
        e, team, ms = ob.event, ob.team_id, ob.event.match_ms
        if self._first_ms is None:
            self._first_ms = ms
        if e.type is EventType.PRESSURE and ob.start and ob.start[0] > PRESS_ZONE_X:
            self._press.add(ms, team)
        if e.type is EventType.POSSESSION_CHANGE:
            self.possession.change(ms, team)
            return
        if e.type in ON_BALL and ob.start and ob.start[0] >= FINAL_THIRD_X:
            self.tilt.add(ms, team)
        if e.type is EventType.PASS and ob.start and ob.start[0] < OWN_DEFENSIVE_60_X:
            self._ppda.add(ms, f"passes_allowed:{self.other(team)}")
        if e.type in DEFENSIVE_ACTIONS and ob.start and ob.start[0] > PRESS_ZONE_X:
            # Defender's frame: beyond 42 m is inside the opponent's own 60%.
            self._ppda.add(ms, f"actions:{team}")
        threat = 0.0
        moved = e.type in (EventType.CARRY, EventType.DRIBBLE) or (
            e.type is EventType.PASS and e.outcome is Outcome.COMPLETE
        )
        if moved and ob.start and ob.end:
            if ob.start[0] < FINAL_THIRD_X <= ob.end[0]:
                threat += THREAT_ENTRY_FINAL_THIRD
            if in_box(*ob.end) and not in_box(*ob.start):
                threat += THREAT_ENTRY_BOX
        if e.type is EventType.SHOT:
            threat += THREAT_PER_XG * float(ob.metrics.get("xg", 0.0))
        if threat:
            self._threat[team].add(ms, threat)

    # ------------------------------------------------------------------ readings
    def possession_share(self, club_id: str, now_ms: int) -> float:
        return self.possession.shares(now_ms).get(club_id, 0.0)

    def field_tilt(self, club_id: str, now_ms: int) -> float:
        own = self.tilt.total(club_id, now_ms)
        both = own + self.tilt.total(self.other(club_id), now_ms)
        return own / both if both else 0.5

    def ppda(self, club_id: str, now_ms: int) -> float:
        """Opponent passes allowed per defensive action by `club_id` (lower = more intense press)."""
        passes = self._ppda.total(f"passes_allowed:{club_id}", now_ms)
        actions = self._ppda.total(f"actions:{club_id}", now_ms)
        return min(PPDA_CAP, passes / actions) if actions else (PPDA_CAP if passes else 0.0)

    def press_rate(self, club_id: str, now_ms: int) -> float:
        """Pressures per minute applied inside the opponent's own 60% (higher = more aggressive press).
        Pressure events are ~3x more frequent than PPDA's defensive actions, so this moves more cleanly."""
        minutes = self._press.span_ms(now_ms, self._first_ms or 0) / 60_000
        return self._press.total(club_id, now_ms) / minutes

    def threat(self, club_id: str, now_ms: int) -> float:
        return self._threat[club_id].value(now_ms)

    def momentum(self, now_ms: int) -> float:
        """Home minus away decayed threat, scaled to [-1, 1] (positive = home on top)."""
        h, a = self._threat[self.home_id].value(now_ms), self._threat[self.away_id].value(now_ms)
        return (h - a) / (h + a + 0.5)
