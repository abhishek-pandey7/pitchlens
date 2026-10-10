"""Incremental match state (§8, T-20): apply events (and tracking frames) in order, get enriched events.

`MatchState` is the single source of truth the explain and render stages read from. It is cheap to
rebuild: a bus `reset` simply means constructing a new one and re-applying the catch-up stream.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from pitchlens.schema.common import PITCH_LENGTH
from pitchlens.schema.enriched import EnrichedEvent, MetricValue, PhysicalAlert
from pitchlens.schema.events import (
    BodyPart,
    EventType,
    Height,
    MatchEvent,
    MatchMeta,
    Outcome,
    Position,
    TrackingFrame,
)

from . import passes as pass_model
from . import shots as shot_model
from .control_chaos import ControlChaos
from .geometry import distance, to_attacking, unit_lines
from .momentum import Observation, TeamDynamics
from .physical import PhysicalTracker
from .rhythm import Rhythm
from .win_prob import WinProbability, remaining_minutes, scoring_rate_per_90, win_probability

FRAME_MAX_AGE_MS = 1000  # tracking older than this is not used for pass context
SAMPLE_EVERY_MS = 30_000  # time-series resolution for charts and change-point detection
WP_EVENTS = {EventType.GOAL, EventType.SHOT, EventType.CARD}
GOAL: tuple[float, float] = (PITCH_LENGTH, 34.0)


@dataclass
class PlayerStats:
    player_id: str
    team_id: str
    name: str
    short_name: str
    position: Position
    on_ms: int | None = None
    off_ms: int | None = None
    touches: int = 0
    passes: int = 0
    passes_completed: int = 0
    pass_distance_m: float = 0.0
    pass_difficulty_sum: float = 0.0
    hardest_completed_pass: float = 0.0
    progressive_passes: int = 0
    lines_broken: int = 0
    key_passes: int = 0
    shots: int = 0
    shots_on_target: int = 0
    goals: int = 0
    assists: int = 0
    xg: float = 0.0
    max_shot_speed_kmh: float = 0.0
    tackles: int = 0
    interceptions: int = 0
    recoveries: int = 0
    pressures: int = 0
    clearances: int = 0
    blocks: int = 0
    fouls: int = 0
    fouled: int = 0
    dribbles: int = 0
    dribbles_won: int = 0
    carries: int = 0
    carry_distance_m: float = 0.0
    yellow_cards: int = 0
    sent_off: bool = False

    @property
    def pass_accuracy(self) -> float | None:
        return self.passes_completed / self.passes if self.passes else None

    @property
    def mean_pass_difficulty(self) -> float | None:
        return self.pass_difficulty_sum / self.passes if self.passes else None

    def minutes(self, now_ms: int) -> float:
        if self.on_ms is None:
            return 0.0
        return ((self.off_ms if self.off_ms is not None else now_ms) - self.on_ms) / 60_000


@dataclass
class TeamStats:
    club_id: str
    name: str
    is_home: bool
    score: int = 0
    shots: int = 0
    shots_on_target: int = 0
    xg: float = 0.0
    passes: int = 0
    passes_completed: int = 0
    possession_ms: int = 0
    corners: int = 0
    fouls: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    offsides: int = 0
    tackles: int = 0
    interceptions: int = 0
    pressures: int = 0
    on_pitch: set[str] = field(default_factory=set)

    @property
    def pass_accuracy(self) -> float | None:
        return self.passes_completed / self.passes if self.passes else None


@dataclass
class Goal:
    match_ms: int
    clock_ms: int
    period: int
    team_id: str
    scorer_id: str | None
    assist_id: str | None
    home_score: int
    away_score: int


class MatchState:
    def __init__(self, meta: MatchMeta) -> None:
        self.meta = meta
        self.match_id = meta.match_id
        self.home_id = meta.home.club_id
        self.away_id = meta.away.club_id
        self.teams = {
            self.home_id: TeamStats(self.home_id, meta.home.name, True),
            self.away_id: TeamStats(self.away_id, meta.away.name, False),
        }
        self.players: dict[str, PlayerStats] = {}
        for club_id, lineup in meta.lineups.items():
            for lp in lineup:
                ps = PlayerStats(lp.player_id, club_id, lp.name, lp.name.split()[-1], lp.pos)
                if lp.starter:
                    ps.on_ms = 0
                    self.teams[club_id].on_pitch.add(lp.player_id)
                self.players[lp.player_id] = ps
        self.physical = PhysicalTracker(meta.match_id, {pid: p.team_id for pid, p in self.players.items()})
        self.period = 1
        self.match_ms = 0
        self.clock_ms = 0
        self.finished = False
        self.possession_team: str | None = None
        self.possession_id = 0
        self._possession_since = 0
        self.goals: list[Goal] = []
        self.events_applied = 0
        self.last_frame: TrackingFrame | None = None
        self._last_completed_pass: tuple[MatchEvent, float] | None = None  # event, difficulty
        self.dynamics = TeamDynamics(self.home_id, self.away_id)
        self.control = ControlChaos()
        self.rhythm = Rhythm()
        self.added_minutes: dict[int, float] = {}
        self.win_prob = self.compute_win_probability()
        self.series: list[dict[str, Any]] = []
        self._next_sample_ms = 0

    # ------------------------------------------------------------------ helpers
    def is_home(self, club_id: str | None) -> bool:
        return club_id == self.home_id

    def opponent(self, club_id: str) -> str:
        return self.away_id if club_id == self.home_id else self.home_id

    @property
    def score(self) -> tuple[int, int]:
        return self.teams[self.home_id].score, self.teams[self.away_id].score

    def possession_share(self, club_id: str) -> float:
        total = sum(t.possession_ms for t in self.teams.values())
        return self.teams[club_id].possession_ms / total if total else 0.5

    def attacking(self, club_id: str, x: float, y: float, period: int | None = None) -> tuple[float, float]:
        return to_attacking((x, y), is_home=self.is_home(club_id), period=period or self.period)

    def defensive_lines(self, attacking_team: str, at_ms: int) -> list[float] | None:
        """Opposition unit lines in the attacking team's frame, from the latest fresh tracking frame."""
        frame = self.last_frame
        if frame is None or at_ms - frame.match_ms > FRAME_MAX_AGE_MS:
            return None
        defenders = self.teams[self.opponent(attacking_team)].on_pitch
        pairs = [
            (self.attacking(attacking_team, p.x, p.y)[0], self.players[p.player_id].position)
            for p in frame.players
            if p.player_id in defenders
        ]
        return unit_lines(pairs) if pairs else None

    # ------------------------------------------------------------------ frames
    def apply_frame(self, frame: TrackingFrame) -> list[PhysicalAlert]:
        self.last_frame = frame
        return self.physical.apply(frame, self.period)

    # ------------------------------------------------------------------ events
    def apply(self, e: MatchEvent) -> EnrichedEvent:
        self._advance_clock(e)
        metrics: dict[str, MetricValue] = {}
        team = self.teams.get(e.team_id or "")
        player = self.players.get(e.player_id or "")
        if player is not None and e.type not in (EventType.SUBSTITUTION, EventType.CARD, EventType.FOUL):
            player.touches += 1

        t = e.type
        if t is EventType.PERIOD_START:
            self.period = e.period
            for q in e.qualifiers:
                if q.startswith("added_minutes:"):
                    self.added_minutes[e.period] = float(q.split(":", 1)[1])
        elif t is EventType.MATCH_END:
            self.finished = True
        elif t is EventType.POSSESSION_CHANGE and e.team_id:
            self.possession_team = e.team_id
            self.possession_id = e.possession_id or self.possession_id + 1
        elif t is EventType.PASS and team and player:
            metrics = self._apply_pass(e, team, player)
        elif t is EventType.SHOT and team and player:
            metrics = self._apply_shot(e, team, player)
        elif t is EventType.GOAL and team:
            self._apply_goal(e, team)
        elif t is EventType.CARRY and player and e.start and e.end:
            player.carries += 1
            length = distance((e.start.x, e.start.y), (e.end.x, e.end.y))
            player.carry_distance_m += length
            metrics["carry_distance_m"] = round(length, 1)
            if e.duration_ms:
                metrics["carry_speed_kmh"] = round(length / (e.duration_ms / 1000) * 3.6, 1)
        elif t is EventType.DRIBBLE and player:
            player.dribbles += 1
            player.dribbles_won += e.outcome is Outcome.SUCCESS
        elif t is EventType.TACKLE and player and team:
            player.tackles += 1
            team.tackles += 1
        elif t is EventType.INTERCEPTION and player and team:
            player.interceptions += 1
            team.interceptions += 1
        elif t is EventType.BALL_RECOVERY and player:
            player.recoveries += 1
        elif t is EventType.PRESSURE and player and team:
            player.pressures += 1
            team.pressures += 1
        elif t is EventType.CLEARANCE and player:
            player.clearances += 1
        elif t is EventType.BLOCK and player:
            player.blocks += 1
        elif t is EventType.FOUL and player and team:
            player.fouls += 1
            team.fouls += 1
            if e.related_player_id in self.players:
                self.players[e.related_player_id].fouled += 1
        elif t is EventType.CARD and player and team:
            if e.outcome is Outcome.YELLOW:
                player.yellow_cards += 1
                team.yellow_cards += 1
            else:
                if e.outcome is Outcome.SECOND_YELLOW:
                    player.yellow_cards += 1
                player.sent_off = True
                player.off_ms = e.match_ms
                team.red_cards += 1
                team.on_pitch.discard(player.player_id)
        elif t is EventType.SUBSTITUTION and player and team and e.related_player_id:
            player.off_ms = e.match_ms
            team.on_pitch.discard(player.player_id)
            incoming = self.players[e.related_player_id]
            incoming.on_ms = e.match_ms
            team.on_pitch.add(incoming.player_id)
        elif t is EventType.CORNER and team:
            team.corners += 1
            if player:
                metrics = self._apply_pass(e, team, player, counts_as_pass=False)
        elif t is EventType.OFFSIDE and team:
            team.offsides += 1
        elif t in (EventType.FREE_KICK, EventType.THROW_IN, EventType.GOAL_KICK) and team and player:
            metrics = self._apply_pass(e, team, player, counts_as_pass=False)

        self._observe_dynamics(e, metrics)
        wp_before = self.win_prob
        self.win_prob = self.compute_win_probability()
        if t in WP_EVENTS and e.team_id in self.teams:
            is_home = self.is_home(e.team_id)
            metrics["wp_before"] = round(wp_before.for_team(is_home), 4)
            metrics["wp_after"] = round(self.win_prob.for_team(is_home), 4)
        while e.match_ms >= self._next_sample_ms:
            self.series.append(self.readings())
            self._next_sample_ms += SAMPLE_EVERY_MS

        self.events_applied += 1
        home, away = self.score
        return EnrichedEvent(
            event=e, metrics=metrics, home_score=home, away_score=away, possession_team=self.possession_team
        )

    def _observe_dynamics(self, e: MatchEvent, metrics: dict[str, MetricValue]) -> None:
        start = end = None
        if e.team_id in self.teams:
            if e.start:
                start = self.attacking(e.team_id, e.start.x, e.start.y, e.period)
            if e.end:
                end = self.attacking(e.team_id, e.end.x, e.end.y, e.period)
            self.dynamics.observe(Observation(e, e.team_id, start, end, metrics))
        self.control.observe(e)
        self.rhythm.observe(e)

    def compute_win_probability(self) -> WinProbability:
        if self.finished:
            return win_probability(*self.score, home_rate_90=0, away_rate_90=0, minutes_left=0)
        minute = self.clock_ms / 60_000
        played = max(0.0, self.match_ms / 60_000)
        home, away = self.teams[self.home_id], self.teams[self.away_id]
        regulation_end = 45 * self.period
        added = self.added_minutes.get(self.period) if minute >= regulation_end - 1 else None
        left = remaining_minutes(minute, self.period, added)
        return win_probability(
            home.score,
            away.score,
            home_rate_90=scoring_rate_per_90(self.meta.pre_match.home_strength, home.xg, played),
            away_rate_90=scoring_rate_per_90(self.meta.pre_match.away_strength, away.xg, played),
            minutes_left=left,
            home_reds=home.red_cards,
            away_reds=away.red_cards,
        )

    def readings(self) -> dict[str, Any]:
        """Team-dynamics readings at the current moment (one row of the time series)."""
        now = self.match_ms
        dyn, home, away = self.dynamics, self.home_id, self.away_id
        control = self.control.reading(now)
        rhythm = self.rhythm.reading(now)
        return {
            "match_ms": now,
            "clock_ms": self.clock_ms,
            "period": self.period,
            "score": list(self.score),
            "wp": [round(self.win_prob.home, 4), round(self.win_prob.draw, 4), round(self.win_prob.away, 4)],
            "xg": [round(self.teams[home].xg, 3), round(self.teams[away].xg, 3)],
            "momentum": round(dyn.momentum(now), 3),
            "possession_5m": round(dyn.possession_share(home, now), 3),
            "field_tilt": round(dyn.field_tilt(home, now), 3),
            "ppda": [round(dyn.ppda(home, now), 2), round(dyn.ppda(away, now), 2)],
            "control": control.index if control else None,
            "control_components": control.components if control else None,
            "tempo": rhythm.tempo,
            "tempo_z": rhythm.z,
            "stoppage_share": rhythm.stoppage_share,
        }

    def _advance_clock(self, e: MatchEvent) -> None:
        if self.possession_team is not None and e.match_ms > self._possession_since:
            self.teams[self.possession_team].possession_ms += e.match_ms - self._possession_since
        self._possession_since = e.match_ms
        self.match_ms, self.clock_ms = e.match_ms, e.clock_ms
        self.period = e.period

    def _apply_pass(
        self, e: MatchEvent, team: TeamStats, player: PlayerStats, *, counts_as_pass: bool = True
    ) -> dict[str, MetricValue]:
        if e.start is None or e.end is None:
            return {}
        start = self.attacking(team.club_id, e.start.x, e.start.y, e.period)
        end = self.attacking(team.club_id, e.end.x, e.end.y, e.period)
        lines = self.defensive_lines(team.club_id, e.match_ms)
        feats = pass_model.pass_features(
            start, end, under_pressure=e.under_pressure, lines=lines or [], aerial=e.height is Height.HIGH
        )
        difficulty = pass_model.difficulty(feats)
        complete = e.outcome is Outcome.COMPLETE
        progressive = complete and distance(end, GOAL) <= 0.75 * distance(start, GOAL) and end[0] > 35
        metrics: dict[str, MetricValue] = {
            "pass_distance_m": round(feats.distance, 1),
            "pass_difficulty": difficulty,
            "progressive": progressive,
            "into_box": feats.into_box,
        }
        if lines is not None:
            metrics["lines_broken"] = feats.lines_broken
        if e.ball_speed_kmh is not None:
            metrics["ball_speed_kmh"] = e.ball_speed_kmh
        if counts_as_pass:
            team.passes += 1
            team.passes_completed += complete
            player.passes += 1
            player.passes_completed += complete
            player.pass_distance_m += feats.distance
            player.pass_difficulty_sum += difficulty
            if complete:
                player.hardest_completed_pass = max(player.hardest_completed_pass, difficulty)
                player.progressive_passes += progressive
                player.lines_broken += feats.lines_broken if lines is not None else 0
        self._last_completed_pass = (e, difficulty) if complete else None
        return metrics

    def _apply_shot(self, e: MatchEvent, team: TeamStats, player: PlayerStats) -> dict[str, MetricValue]:
        if e.start is None:
            return {}
        x, y = self.attacking(team.club_id, e.start.x, e.start.y, e.period)
        feats = shot_model.shot_features(
            x,
            y,
            header=e.body_part is BodyPart.HEAD,
            under_pressure=e.under_pressure,
            one_on_one="one_on_one" in e.qualifiers,
            penalty="penalty" in e.qualifiers,
        )
        xg = shot_model.xg(feats)
        on_target = e.outcome in (Outcome.GOAL, Outcome.ON_TARGET)
        team.shots += 1
        team.shots_on_target += on_target
        team.xg += xg
        player.shots += 1
        player.shots_on_target += on_target
        player.xg += xg
        metrics: dict[str, MetricValue] = {
            "xg": xg,
            "shot_distance_m": round(feats.distance, 1),
            "on_target": on_target,
        }
        if e.ball_speed_kmh is not None:
            metrics["shot_speed_kmh"] = e.ball_speed_kmh
            player.max_shot_speed_kmh = max(player.max_shot_speed_kmh, e.ball_speed_kmh)
        last = self._last_completed_pass
        if last and last[0].recipient_id == e.player_id and last[0].team_id == e.team_id:
            passer = self.players.get(last[0].player_id or "")
            if passer:
                passer.key_passes += 1
                metrics["assist_pass_difficulty"] = last[1]
                metrics["assist_player_id"] = passer.player_id
        self._last_completed_pass = None
        return metrics

    def _apply_goal(self, e: MatchEvent, team: TeamStats) -> None:
        team.score += 1
        scorer = self.players.get(e.player_id or "")
        if scorer:
            scorer.goals += 1
        assister = self.players.get(e.related_player_id or "")
        if assister:
            assister.assists += 1
        home, away = self.score
        self.goals.append(
            Goal(e.match_ms, e.clock_ms, e.period, team.club_id, e.player_id, e.related_player_id, home, away)
        )

    # ------------------------------------------------------------------ views
    def snapshot(self) -> dict[str, Any]:
        """Compact JSON-able view for the API's `state` stream."""
        home, away = self.teams[self.home_id], self.teams[self.away_id]

        def team_view(t: TeamStats) -> dict[str, Any]:
            return {
                "club_id": t.club_id,
                "score": t.score,
                "possession": round(self.possession_share(t.club_id), 3),
                "shots": t.shots,
                "shots_on_target": t.shots_on_target,
                "xg": round(t.xg, 2),
                "passes": t.passes,
                "pass_accuracy": round(t.pass_accuracy, 3) if t.pass_accuracy is not None else None,
                "corners": t.corners,
                "fouls": t.fouls,
                "yellow_cards": t.yellow_cards,
                "red_cards": t.red_cards,
            }

        return {
            "match_id": self.match_id,
            "period": self.period,
            "match_ms": self.match_ms,
            "clock_ms": self.clock_ms,
            "finished": self.finished,
            "possession_team": self.possession_team,
            "dynamics": self.readings(),
            "home": team_view(home),
            "away": team_view(away),
        }


def timeline(
    events: Iterable[MatchEvent], frames: Iterable[TrackingFrame] = ()
) -> Iterator[MatchEvent | TrackingFrame]:
    """Merge events and frames by match time (events first at equal times, like the live replay)."""
    items: list[tuple[int, int, int, MatchEvent | TrackingFrame]] = [
        (e.match_ms, 0, e.seq, e) for e in events
    ] + [(f.match_ms, 1, i, f) for i, f in enumerate(frames)]
    for *_, item in sorted(items, key=lambda r: r[:3]):
        yield item


def build_state(
    meta: MatchMeta, events: Iterable[MatchEvent], frames: Iterable[TrackingFrame] = ()
) -> tuple[MatchState, list[EnrichedEvent], list[PhysicalAlert]]:
    """Apply a whole (stored) match offline: the same code path the live pipeline uses."""
    state = MatchState(meta)
    enriched: list[EnrichedEvent] = []
    alerts: list[PhysicalAlert] = []
    for item in timeline(events, frames):
        if isinstance(item, MatchEvent):
            enriched.append(state.apply(item))
        else:
            alerts += state.apply_frame(item)
    return state, enriched, alerts
