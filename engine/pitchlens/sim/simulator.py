"""Possession-chain match simulator (§7).

Each step the ball carrier may be pressed, then picks an action (pass, carry, dribble, shot, clearance)
whose probabilities depend on pitch zone, player attributes, team style and game state. Pass completion
and goals are sampled from the same models the interpret layer uses (`interpret.passes`, `interpret.shots`),
so the synthetic data is internally consistent with the analytics computed on it.

Storylines inject scripted beats (goals, red cards, pressing changes, sprints) through `Beat`s, executed
with the same action code as organic play so scripted moments look like any other moment in the data.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pitchlens.interpret import passes as pass_model
from pitchlens.interpret import shots as shot_model
from pitchlens.schema.common import PITCH_LENGTH, PITCH_WIDTH, Point
from pitchlens.schema.events import (
    BodyPart,
    EventType,
    Height,
    MatchEvent,
    MatchMeta,
    Outcome,
    Position,
    PreMatch,
)

from .pitch import (
    CENTRE,
    DEFENDERS,
    FORMATION_ANCHORS,
    FORWARDS,
    MIDFIELDERS,
    XY,
    clamp,
    mirror,
    team_shape,
    unit_lines,
)
from .rosters import FORMATION_STARTERS, Club, League, Player

HALF_MS = 45 * 60_000
HALFTIME_BREAK = timedelta(minutes=15)
HOME_ADVANTAGE = 0.15
REFEREES = ["R. Hale", "M. Okonjo", "S. Varga", "L. Pereira", "D. Ashworth", "K. Nakamura", "J. Ferrand"]
WEATHER = ["clear", "overcast", "light rain", "heavy rain", "windy", "cold and dry", "mild"]


@dataclass
class Beat:
    """A scripted storyline moment, triggered once the football clock passes `minute`."""

    minute: float
    kind: str  # goal | red_card | press | style | sprint | pass_of_note
    side: str  # home | away
    params: dict[str, Any] = field(default_factory=dict)
    done: bool = False


@dataclass
class StorylineSpec:
    name: str
    beats: list[Beat] = field(default_factory=list)
    goal_policy: str = "organic"  # organic | scripted_only
    min_added_minutes: tuple[int, int] = (0, 0)
    description: str = ""


@dataclass
class SimulatedMatch:
    meta: MatchMeta
    events: list[MatchEvent]


class TeamState:
    def __init__(self, club: Club, side: str) -> None:
        self.club = club
        self.side = side
        self.slots: list[Position] = list(FORMATION_STARTERS[club.formation])
        self.anchors: list[XY] = list(FORMATION_ANCHORS[club.formation])
        self.players: list[Player] = club.starting_xi()
        on = {p.player_id for p in self.players}
        self.bench: list[Player] = [p for p in club.players if p.player_id not in on]
        self.press = club.style.press_intensity
        self.tempo = club.style.tempo
        self.directness = club.style.directness
        self.width = club.style.width
        self.aggression_mult = 1.0
        self.finishing_boost = 0.0
        self.score = 0
        self.yellows: set[str] = set()
        self.subs_used = 0
        self.sub_windows: list[float] = []

    @property
    def club_id(self) -> str:
        return self.club.club_id

    def gk(self) -> Player:
        return next(p for p, s in zip(self.players, self.slots, strict=True) if s is Position.GK)

    def index(self, player_id: str) -> int:
        return next(i for i, p in enumerate(self.players) if p.player_id == player_id)

    def slot_of(self, player: Player) -> Position:
        return self.slots[self.index(player.player_id)]

    def outfield(self) -> list[Player]:
        return [p for p, s in zip(self.players, self.slots, strict=True) if s is not Position.GK]

    def remove(self, player_id: str) -> None:
        i = self.index(player_id)
        del self.players[i], self.slots[i], self.anchors[i]


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _dist(a: XY, b: XY) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class MatchSimulator:
    def __init__(
        self,
        league: League,
        home_id: str,
        away_id: str,
        *,
        seed: int,
        storyline: StorylineSpec | None = None,
        kickoff_utc: datetime | None = None,
        match_id: str | None = None,
    ) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.league = league
        self.home = TeamState(league.club(home_id), "home")
        self.away = TeamState(league.club(away_id), "away")
        self.storyline = storyline or StorylineSpec(name="organic")
        self.match_id = match_id or f"SYN-{home_id}-{away_id}-{seed}"
        self.kickoff_utc = kickoff_utc or datetime(2026, 10, 18, 14, 0, tzinfo=UTC)
        self.events: list[MatchEvent] = []

        self.ms = 0
        self.period = 1
        self.period_start_ms = 0
        self.period_len_ms = HALF_MS
        self.possession_id = 0
        self.ball: XY = CENTRE
        self.att: TeamState = self.home
        self.dfn: TeamState = self.away
        self.carrier: Player | None = None
        self.restart: tuple[str, TeamState, XY] | None = None
        self.transition_until = 0
        self.last_pass: tuple[Player, Player, bool, int] | None = None  # passer, recipient, cross, ms
        for team in (self.home, self.away):
            team.sub_windows = sorted(self.rng.uniform(lo, hi) for lo, hi in ((56, 64), (66, 74), (76, 84)))

    # ------------------------------------------------------------------ frames & geometry
    def attacks_positive_x(self, team: TeamState) -> bool:
        return (team is self.home) == (self.period == 1)

    def to_team(self, team: TeamState, p: XY) -> XY:
        """Absolute -> team frame (and back: the transform is its own inverse)."""
        return p if self.attacks_positive_x(team) else mirror(p)

    def other(self, team: TeamState) -> TeamState:
        return self.away if team is self.home else self.home

    def shape(self, team: TeamState) -> list[XY]:
        return team_shape(
            team.anchors,
            team.slots,
            self.to_team(team, self.ball),
            in_possession=team is self.att,
            press=team.press,
            width=team.width,
        )

    def nearest(self, team: TeamState, point_abs: XY, *, exclude: str | None = None) -> Player:
        target = self.to_team(team, point_abs)
        best = min(
            (
                (_dist(xy, target), p)
                for p, xy in zip(team.players, self.shape(team), strict=True)
                if p.player_id != exclude
            ),
            key=lambda t: t[0],
        )
        return best[1]

    def defensive_lines_in_attack_frame(self) -> list[float]:
        lines = unit_lines(self.shape(self.dfn), self.dfn.slots)
        return [PITCH_LENGTH - x for x in lines]

    # ------------------------------------------------------------------ clock
    @property
    def clock_ms(self) -> int:
        return HALF_MS * (self.period - 1) + (self.ms - self.period_start_ms)

    @property
    def minute(self) -> float:
        return self.clock_ms / 60_000

    def advance(self, ms: float) -> None:
        self.ms += max(1, int(ms))

    def hold_time(self, pressured: bool) -> float:
        base = self.rng.uniform(600, 1800) * (1.3 - 0.6 * self.att.tempo)
        return base * (0.6 if pressured else 1.0)

    # ------------------------------------------------------------------ emission
    def emit(
        self,
        etype: EventType,
        team: TeamState | None = None,
        player: Player | None = None,
        *,
        start: XY | None = None,
        end: XY | None = None,
        **fields: Any,
    ) -> MatchEvent:
        ts = self.kickoff_utc + timedelta(milliseconds=self.ms)
        if self.period == 2:
            ts += HALFTIME_BREAK
        event = MatchEvent(
            event_id=f"e-{len(self.events):06d}",
            match_id=self.match_id,
            seq=len(self.events),
            type=etype,
            period=self.period,
            match_ms=self.ms,
            clock_ms=self.clock_ms,
            team_id=team.club_id if team else None,
            player_id=player.player_id if player else None,
            possession_id=self.possession_id if team else None,
            start=Point(x=round(start[0], 2), y=round(start[1], 2)) if start else None,
            end=Point(x=round(end[0], 2), y=round(end[1], 2)) if end else None,
            ts_utc=ts,
            **fields,
        )
        self.events.append(event)
        return event

    def gain(self, team: TeamState, player: Player, at: XY, *, open_play: bool) -> None:
        if team is not self.att:
            self.att, self.dfn = team, self.other(team)
            self.possession_id += 1
            self.emit(EventType.POSSESSION_CHANGE, team, player, start=at)
            if open_play:
                self.transition_until = self.ms + 8000
        self.carrier = player
        self.ball = at

    def set_restart(self, kind: str, team: TeamState, at: XY, dead_ms: float) -> None:
        self.carrier = None
        self.ball = at
        self.restart = (kind, team, at)
        self.advance(dead_ms)
        self.maybe_substitute()

    # ------------------------------------------------------------------ match flow
    def run(self) -> SimulatedMatch:
        meta = self.build_meta()
        self.emit(EventType.MATCH_START)
        for period in (1, 2):
            self.play_period(period)
        self.emit(EventType.MATCH_END)
        return SimulatedMatch(meta=meta, events=self.events)

    def play_period(self, period: int) -> None:
        self.period = period
        self.period_start_ms = self.ms
        lo, hi = (1, 4) if period == 1 else (3, 7)
        added = max(self.rng.randint(lo, hi), self.storyline.min_added_minutes[period - 1])
        self.period_len_ms = HALF_MS + added * 60_000
        self.emit(EventType.PERIOD_START, qualifiers=[f"added_minutes:{added}"])
        kicker = self.home if period == 1 else self.away
        self.att, self.dfn = self.other(kicker), kicker  # so the kick-off registers a possession change
        self.set_restart("kick_off", kicker, CENTRE, 0)
        while self.ms - self.period_start_ms < self.period_len_ms:
            self.run_beats()
            if self.restart:
                self.do_restart()
            else:
                self.open_play()
        self.emit(EventType.PERIOD_END)
        self.restart = None

    def trailing_late(self, team: TeamState) -> bool:
        return self.minute > 75 and team.score < self.other(team).score

    # ------------------------------------------------------------------ open play
    def open_play(self) -> None:
        team, carrier = self.att, self.carrier
        assert carrier is not None
        x, y = self.to_team(team, self.ball)
        pressured = False
        p_press = 0.1 + 0.4 * self.dfn.press * (1 - x / PITCH_LENGTH) + (0.25 if x > 75 else 0.0)
        if self.rng.random() < p_press:
            presser = self.nearest(self.dfn, self.ball)
            self.emit(
                EventType.PRESSURE, self.dfn, presser, start=self.ball, related_player_id=carrier.player_id
            )
            self.advance(300)
            pressured = True
            if self.rng.random() < 0.05 * self.dfn.aggression_mult * presser.attributes.aggression / 70:
                self.foul(self.dfn, presser, carrier)
                return
            if self.rng.random() < 0.19 * self.dfn.press:  # an intense press wins the ball back
                self.emit(
                    EventType.TACKLE,
                    self.dfn,
                    presser,
                    start=self.ball,
                    outcome=Outcome.SUCCESS,
                    related_player_id=carrier.player_id,
                )
                self.gain(self.dfn, presser, self.ball, open_play=True)
                self.advance(self.hold_time(False))
                return
        self.advance(self.hold_time(pressured))

        slot = team.slot_of(carrier)
        d_goal = _dist((x, y), (PITCH_LENGTH, PITCH_WIDTH / 2))
        if d_goal < 11:
            shot_w = 0.17
        elif pass_model.in_box(x, y):
            shot_w = 0.06
        elif d_goal < 25:
            shot_w = 0.075
        elif d_goal < 32:
            shot_w = 0.035
        else:
            shot_w = 0.0
        shot_w *= 0.6 + carrier.attributes.finishing / 99
        if self.trailing_late(team):
            shot_w *= 1.5
        weights = {
            "pass": 0.72,
            "carry": 0.16,
            "dribble": 0.03 + (0.07 if x > 60 else 0.0),
            "shot": shot_w,
            "clear": 0.35 if pressured and x < 30 and (slot in DEFENDERS or slot is Position.GK) else 0.0,
        }
        if slot is Position.GK:
            weights.update(carry=0.0, dribble=0.0, shot=0.0)
        action = self.rng.choices(list(weights), weights=list(weights.values()))[0]
        if action == "pass":
            self.pass_ball(pressured)
        elif action == "carry":
            self.carry(pressured)
        elif action == "dribble":
            self.dribble(pressured)
        elif action == "shot":
            self.shoot(pressured)
        else:
            self.clear()

    # ------------------------------------------------------------------ actions
    def pass_ball(
        self,
        pressured: bool,
        *,
        kind: EventType = EventType.PASS,
        to: Player | None = None,
        target: XY | None = None,
        force_complete: bool | None = None,
        max_len: float | None = None,
        qualifiers: tuple[str, ...] = (),
    ) -> bool:
        """Pass from the current carrier. `target` is in the attacking team's frame. Returns completion."""
        team, passer = self.att, self.carrier
        assert passer is not None
        rng = self.rng
        start = self.to_team(team, self.ball)
        shape = self.shape(team)
        quals = list(qualifiers)
        aerial = "cross" in quals

        if to is None:
            wide = start[1] < 16 or start[1] > PITCH_WIDTH - 16
            if (
                kind in (EventType.PASS, EventType.CORNER)
                and start[0] > 78
                and (wide or kind is EventType.CORNER)
            ):
                cross = kind is EventType.CORNER or rng.random() < 0.45
            else:
                cross = False
            if cross:
                target = target or (rng.uniform(88, 99), rng.uniform(25, 43))
                attackers = [
                    (p, xy)
                    for p, xy, s in zip(team.players, shape, team.slots, strict=True)
                    if p.player_id != passer.player_id
                    and (s in FORWARDS or s in MIDFIELDERS or s is Position.CB)
                ]
                to = min(
                    attackers,
                    key=lambda t: _dist(t[1], target) - (6 if team.slot_of(t[0]) in FORWARDS else 0),
                )[0]
                aerial = True
                quals.append("cross")
            else:
                candidates = []
                for p, xy, s in zip(team.players, shape, team.slots, strict=True):
                    if p.player_id == passer.player_id:
                        continue
                    d = _dist(start, xy)
                    if d < 4 or (max_len and d > max_len):
                        continue
                    dx = xy[0] - start[0]
                    w = math.exp(-(((d - 12) / 9) ** 2)) + 0.04
                    w *= 1 + team.directness * max(0.0, dx) / 30
                    if dx < -5:
                        w *= 0.7
                    if s is Position.GK:
                        w *= 0.25 if start[0] < 40 else 0.02
                    candidates.append((w, p, xy))
                if not candidates:
                    candidates = [
                        (1.0, p, xy)
                        for p, xy in zip(team.players, shape, strict=True)
                        if p.player_id != passer.player_id
                    ]
                _, to, xy = rng.choices(candidates, weights=[c[0] for c in candidates])[0]
                target = target or (xy[0] + rng.gauss(0, 1.5), xy[1] + rng.gauss(0, 1.5))
                dx = target[0] - start[0]
                if team.slot_of(to) in FORWARDS and dx > 8 and rng.random() < 0.35:
                    target = (min(target[0] + rng.uniform(4, 10), 97.0), target[1])
                    quals.append("through_ball")
        elif target is None:
            target = shape[team.index(to.player_id)]

        end = clamp(target)
        length = _dist(start, end)
        if length > 35:
            quals.append("long_ball")
            aerial = aerial or rng.random() < 0.65
        if abs(end[1] - start[1]) > 30:
            quals.append("switch")
        if kind is EventType.GOAL_KICK and length > 35:
            aerial = True

        feats = pass_model.pass_features(
            start, end, under_pressure=pressured, lines=self.defensive_lines_in_attack_frame(), aerial=aerial
        )
        p_complete = _sigmoid(
            pass_model.completion_logit(feats) + (passer.attributes.passing - 70) / 30 * 0.5
        )
        complete = rng.random() < p_complete if force_complete is None else force_complete
        speed = rng.uniform(15, 22) if aerial else rng.uniform(12, 18)  # m/s
        duration = length / speed * 1000 + 50
        end_abs = self.to_team(team, end)
        common: dict[str, Any] = {
            "start": self.ball,
            "end": end_abs,
            "recipient_id": to.player_id,
            "under_pressure": pressured,
            "height": Height.HIGH if aerial else Height.GROUND,
            "body_part": BodyPart.RIGHT_FOOT if rng.random() < 0.72 else BodyPart.LEFT_FOOT,
            "qualifiers": quals,
            "ball_speed_kmh": round(speed * 3.6, 1),
            "duration_ms": int(duration),
        }
        if kind is EventType.THROW_IN:
            common["body_part"] = BodyPart.OTHER

        if complete and "through_ball" in quals and force_complete is None and rng.random() < 0.12:
            self.emit(kind, team, passer, outcome=Outcome.OFFSIDE, **common)
            self.advance(duration)
            self.emit(EventType.OFFSIDE, team, to, start=end_abs)
            self.set_restart("free_kick", self.dfn, end_abs, rng.uniform(15_000, 25_000))
            return False

        if complete:
            self.emit(kind, team, passer, outcome=Outcome.COMPLETE, **common)
            self.advance(duration)
            self.last_pass = (passer, to, "cross" in quals, self.ms)
            self.carrier = to
            self.ball = end_abs
            return True

        self.last_pass = None
        near_line = end[1] < 6 or end[1] > PITCH_WIDTH - 6 or end[0] > PITCH_LENGTH - 4
        if (aerial and rng.random() < 0.4) or (near_line and rng.random() < 0.6) or rng.random() < 0.08:
            self.emit(kind, team, passer, outcome=Outcome.OUT, **common)
            self.advance(duration)
            if "cross" in quals:
                if rng.random() < 0.55:
                    corner_y = 0.5 if end[1] < PITCH_WIDTH / 2 else PITCH_WIDTH - 0.5
                    self.set_restart(
                        "corner", team, self.to_team(team, (PITCH_LENGTH - 0.5, corner_y)), 25_000
                    )
                else:
                    self.set_restart(
                        "goal_kick", self.dfn, self.to_team(team, (PITCH_LENGTH - 5.5, 34.0)), 18_000
                    )
            else:
                side_y = 0.5 if end[1] < PITCH_WIDTH / 2 else PITCH_WIDTH - 0.5
                self.set_restart("throw_in", self.dfn, self.to_team(team, (end[0], side_y)), 10_000)
            return False

        # Intercepted: like real event feeds, the pass ends where the ball was won, not where it was aimed.
        t = rng.uniform(0.45, 1.0)
        cut = self.to_team(team, (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t))
        common.update(end=cut, duration_ms=int(duration * t))
        self.emit(kind, team, passer, outcome=Outcome.INCOMPLETE, **common)
        self.advance(duration * t)
        winner = self.nearest(self.dfn, cut)
        if rng.random() < 0.55:
            self.emit(EventType.INTERCEPTION, self.dfn, winner, start=cut, outcome=Outcome.SUCCESS)
        else:
            self.emit(EventType.BALL_RECOVERY, self.dfn, winner, start=cut)
        self.gain(self.dfn, winner, cut, open_play=True)
        return False

    def carry(self, pressured: bool, *, to: XY | None = None, speed_kmh: float | None = None) -> None:
        team, carrier = self.att, self.carrier
        assert carrier is not None
        rng = self.rng
        start = self.to_team(team, self.ball)
        transition = self.ms < self.transition_until
        if to is None:
            length = rng.uniform(12, 32) if transition and rng.random() < 0.5 else rng.uniform(3, 12)
            if start[0] > 75:
                length *= 0.5
            dir_y = rng.gauss(0, 0.35) + (PITCH_WIDTH / 2 - start[1]) / 120
            norm = math.hypot(1.0, dir_y)
            to = (start[0] + length / norm, start[1] + length * dir_y / norm)
        end = clamp(to, margin=1.0)
        length = _dist(start, end)
        top = carrier.top_speed_kmh / 3.6
        if speed_kmh is not None:
            speed = speed_kmh / 3.6
        elif length > 12:
            speed = top * rng.uniform(0.82, 0.96)
        else:
            speed = rng.uniform(3.5, 6.0)
        speed = min(speed, top)
        end_abs = self.to_team(team, end)
        duration = length / speed * 1000
        self.emit(
            EventType.CARRY,
            team,
            carrier,
            start=self.ball,
            end=end_abs,
            under_pressure=pressured,
            duration_ms=int(duration),
        )
        self.advance(duration)
        self.ball = end_abs

    def dribble(self, pressured: bool) -> None:
        team, carrier = self.att, self.carrier
        assert carrier is not None
        rng = self.rng
        defender = self.nearest(self.dfn, self.ball)
        if rng.random() < 0.10 * self.dfn.aggression_mult * defender.attributes.aggression / 70:
            self.foul(self.dfn, defender, carrier)
            return
        a, d = carrier.attributes, defender.attributes
        p_success = _sigmoid(0.2 + ((a.pace + a.composure) / 2 - d.defending) / 18 - 0.5 * pressured)
        if rng.random() < p_success:
            start = self.to_team(team, self.ball)
            end_abs = self.to_team(team, clamp((start[0] + rng.uniform(3, 7), start[1] + rng.gauss(0, 2))))
            self.emit(
                EventType.DRIBBLE,
                team,
                carrier,
                start=self.ball,
                end=end_abs,
                outcome=Outcome.SUCCESS,
                under_pressure=pressured,
                related_player_id=defender.player_id,
                duration_ms=1200,
            )
            self.advance(1200)
            self.ball = end_abs
        else:
            self.emit(
                EventType.DRIBBLE,
                team,
                carrier,
                start=self.ball,
                outcome=Outcome.FAIL,
                under_pressure=pressured,
                related_player_id=defender.player_id,
            )
            self.emit(
                EventType.TACKLE,
                self.dfn,
                defender,
                start=self.ball,
                outcome=Outcome.SUCCESS,
                related_player_id=carrier.player_id,
            )
            self.advance(800)
            self.gain(self.dfn, defender, self.ball, open_play=True)

    def clear(self) -> None:
        team, carrier = self.att, self.carrier
        assert carrier is not None
        rng = self.rng
        start = self.to_team(team, self.ball)
        land = clamp((start[0] + rng.uniform(30, 55), start[1] + rng.gauss(0, 12)))
        land_abs = self.to_team(team, land)
        self.emit(
            EventType.CLEARANCE,
            team,
            carrier,
            start=self.ball,
            end=land_abs,
            height=Height.HIGH,
            under_pressure=True,
        )
        self.advance(_dist(start, land) / 18 * 1000)
        r = rng.random()
        if r < 0.2:
            side_y = 0.5 if land[1] < PITCH_WIDTH / 2 else PITCH_WIDTH - 0.5
            self.set_restart("throw_in", self.dfn, self.to_team(team, (land[0], side_y)), 10_000)
        elif r < 0.7:
            winner = self.nearest(self.dfn, land_abs)
            self.emit(EventType.BALL_RECOVERY, self.dfn, winner, start=land_abs)
            self.gain(self.dfn, winner, land_abs, open_play=True)
        else:
            self.ball = land_abs
            self.carrier = self.nearest(team, land_abs)
            self.emit(EventType.BALL_RECOVERY, team, self.carrier, start=land_abs)

    def foul(self, team: TeamState, offender: Player, victim: Player, *, card: Outcome | None = None) -> None:
        rng = self.rng
        self.emit(EventType.FOUL, team, offender, start=self.ball, related_player_id=victim.player_id)
        dead = rng.uniform(18_000, 35_000)
        if card is None and team.slot_of(offender) is not Position.GK:
            r = rng.random()
            if r < 0.006 * team.aggression_mult:
                card = Outcome.RED
            elif r < 0.16 * team.aggression_mult * offender.attributes.aggression / 70:
                card = Outcome.SECOND_YELLOW if offender.player_id in team.yellows else Outcome.YELLOW
        if card in (Outcome.RED, Outcome.SECOND_YELLOW) and len(team.players) <= 8:
            card = Outcome.YELLOW  # never reduce a side below eight players
        if card is not None:
            self.emit(EventType.CARD, team, offender, start=self.ball, outcome=card)
            dead += 20_000
            if card is Outcome.YELLOW:
                team.yellows.add(offender.player_id)
            else:
                team.remove(offender.player_id)
        self.set_restart("free_kick", self.other(team), self.ball, dead)

    def shoot(
        self,
        pressured: bool,
        *,
        header: bool = False,
        force: str | None = None,
        one_on_one: bool | None = None,
        qualifiers: tuple[str, ...] = (),
    ) -> None:
        team, shooter = self.att, self.carrier
        assert shooter is not None
        rng = self.rng
        x, y = self.to_team(team, self.ball)
        d_goal = _dist((x, y), (PITCH_LENGTH, PITCH_WIDTH / 2))
        if one_on_one is None:
            one_on_one = (
                self.ms < self.transition_until and d_goal < 22 and not pressured and rng.random() < 0.5
            )
        if (
            self.last_pass
            and self.last_pass[1] is shooter
            and self.last_pass[2]
            and self.ms - self.last_pass[3] < 3000
        ):
            header = header or rng.random() < 0.7
        feats = shot_model.shot_features(x, y, header=header, under_pressure=pressured, one_on_one=one_on_one)
        gk = self.dfn.gk()
        p_goal = _sigmoid(
            shot_model.xg_logit(feats)
            + (shooter.attributes.finishing - 80) / 30 * 0.4
            - (gk.attributes.defending - 78) / 30 * 0.3
            + team.finishing_boost
        )
        is_goal = rng.random() < p_goal
        if force == "goal":
            is_goal = True
        elif force == "miss" or (self.storyline.goal_policy == "scripted_only" and force is None):
            is_goal = False

        if header:
            speed = rng.gauss(58, 8)
        else:
            speed = (
                rng.gauss(88, 12) + (shooter.attributes.finishing - 70) * 0.15 - max(0.0, d_goal - 20) * 0.3
            )
        speed = min(max(speed, 35.0), 128.0)
        quals = list(qualifiers)
        if one_on_one:
            quals.append("one_on_one")
        if self.ms < self.transition_until:
            quals.append("counter")
        assist = self.last_pass[0] if self.last_pass and self.last_pass[1] is shooter else None
        travel = d_goal / (speed / 3.6) * 1000
        common: dict[str, Any] = {
            "start": self.ball,
            "under_pressure": pressured,
            "body_part": BodyPart.HEAD if header else BodyPart.RIGHT_FOOT,
            "ball_speed_kmh": round(speed, 1),
            "qualifiers": quals,
            "related_player_id": assist.player_id if assist else None,
            "duration_ms": int(travel),
        }

        if is_goal:
            end = self.to_team(team, (PITCH_LENGTH, rng.uniform(31.0, 37.0)))
            self.emit(EventType.SHOT, team, shooter, end=end, outcome=Outcome.GOAL, **common)
            self.advance(travel)
            team.score += 1
            self.emit(
                EventType.GOAL,
                team,
                shooter,
                start=end,
                related_player_id=assist.player_id if assist else None,
                qualifiers=[f"score:{self.home.score}-{self.away.score}"],
            )
            dead = rng.uniform(50_000, 80_000)
            if rng.random() < 0.1:
                self.advance(5_000)
                self.emit(EventType.VAR_CHECK, team, shooter, outcome=Outcome.SUCCESS)
                dead += 60_000
            self.last_pass = None
            self.set_restart("kick_off", self.other(team), CENTRE, dead)
            return

        r = rng.random()
        if r < 0.25 and not header:
            blocker = self.nearest(self.dfn, self.ball, exclude=gk.player_id)
            block_at = self.to_team(team, clamp((x + rng.uniform(1, 4), y + rng.gauss(0, 1.5))))
            common["duration_ms"] = 200
            self.emit(EventType.SHOT, team, shooter, end=block_at, outcome=Outcome.BLOCKED, **common)
            self.advance(200)
            self.emit(EventType.BLOCK, self.dfn, blocker, start=block_at, outcome=Outcome.SUCCESS)
            if rng.random() < 0.5:
                self.set_restart("corner", team, self.corner_spot(team, y), 25_000)
            else:
                self.emit(EventType.BALL_RECOVERY, self.dfn, blocker, start=block_at)
                self.gain(self.dfn, blocker, block_at, open_play=True)
        elif r < 0.53:
            end = self.to_team(team, (PITCH_LENGTH - 0.3, rng.uniform(30.5, 37.5)))
            self.emit(EventType.SHOT, team, shooter, end=end, outcome=Outcome.ON_TARGET, **common)
            self.advance(travel)
            self.emit(EventType.SAVE, self.dfn, gk, start=end, outcome=Outcome.SUCCESS)
            if rng.random() < 0.55:
                self.gain(self.dfn, gk, end, open_play=False)
                self.ball = self.to_team(self.dfn, (4.0, 34.0))
                self.advance(rng.uniform(3_000, 8_000))
            else:
                self.set_restart("corner", team, self.corner_spot(team, y), 25_000)
        else:
            wide_y = rng.choice([rng.uniform(22, 29.5), rng.uniform(38.5, 46)])
            end = self.to_team(team, (PITCH_LENGTH - 0.3, wide_y))
            self.emit(EventType.SHOT, team, shooter, end=end, outcome=Outcome.OFF_TARGET, **common)
            self.advance(travel)
            self.set_restart("goal_kick", self.dfn, self.to_team(team, (PITCH_LENGTH - 5.5, 34.0)), 18_000)
        self.last_pass = None

    def corner_spot(self, team: TeamState, y_team: float) -> XY:
        corner_y = 0.5 if y_team < PITCH_WIDTH / 2 else PITCH_WIDTH - 0.5
        return self.to_team(team, (PITCH_LENGTH - 0.5, corner_y))

    # ------------------------------------------------------------------ restarts
    def do_restart(self) -> None:
        assert self.restart is not None
        kind, team, at = self.restart
        self.restart = None
        rng = self.rng
        at_team = self.to_team(team, at)
        if kind == "kick_off":
            taker = next(
                (p for p, s in zip(team.players, team.slots, strict=True) if s is Position.ST),
                team.outfield()[-1],
            )
            self.gain(team, taker, CENTRE, open_play=False)
            behind = [
                p
                for p, xy in zip(team.players, self.shape(team), strict=True)
                if xy[0] < CENTRE[0] - 3
                and p.player_id != taker.player_id
                and team.slot_of(p) is not Position.GK
            ]
            self.pass_ball(False, to=rng.choice(behind) if behind else None, qualifiers=("kick_off",))
        elif kind == "throw_in":
            taker = self.nearest(team, at)
            self.gain(team, taker, at, open_play=False)
            self.pass_ball(False, kind=EventType.THROW_IN, max_len=22)
        elif kind == "goal_kick":
            gk = team.gk()
            self.gain(team, gk, self.to_team(team, (5.5, 34.0)), open_play=False)
            self.pass_ball(False, kind=EventType.GOAL_KICK)
        elif kind == "corner":
            wide = [
                p
                for p, s in zip(team.players, team.slots, strict=True)
                if s in {Position.RW, Position.LW, Position.AM}
            ]
            taker = rng.choice(wide) if wide else max(team.outfield(), key=lambda p: p.attributes.passing)
            self.gain(team, taker, at, open_play=False)
            if (
                self.pass_ball(False, kind=EventType.CORNER, qualifiers=("set_piece",))
                and rng.random() < 0.55
            ):
                self.advance(300)
                self.shoot(False, header=True, qualifiers=("set_piece",))
        else:  # free_kick
            taker = self.nearest(team, at)
            self.gain(team, taker, at, open_play=False)
            direct = at_team[0] > 75 and abs(at_team[1] - PITCH_WIDTH / 2) < 16 and rng.random() < 0.3
            if direct:
                best = max(team.outfield(), key=lambda p: p.attributes.finishing)
                self.carrier = best
                self.shoot(False, qualifiers=("free_kick", "set_piece"))
            else:
                self.pass_ball(False, kind=EventType.FREE_KICK)

    def maybe_substitute(self) -> None:
        if self.period != 2:
            return
        for team in (self.home, self.away):
            while team.sub_windows and self.minute >= team.sub_windows[0]:
                team.sub_windows.pop(0)
                for _ in range(self.rng.choice((1, 1, 2))):
                    if team.subs_used >= 5:
                        break
                    self.substitute(team)

    def substitute(self, team: TeamState, off: Player | None = None) -> None:
        outfield = team.outfield()
        if off is None:
            off = min(outfield, key=lambda p: p.attributes.work_rate + self.rng.uniform(0, 30))
        slot = team.slot_of(off)
        units = [{slot}, *(u for u in (DEFENDERS, MIDFIELDERS, FORWARDS) if slot in u)]
        on = None
        for unit in units:
            options = [p for p in team.bench if p.pos in unit]
            if options:
                on = max(options, key=lambda p: p.attributes.work_rate + p.attributes.pace)
                break
        if on is None:
            return
        team.bench.remove(on)
        team.players[team.index(off.player_id)] = on
        team.subs_used += 1
        self.emit(EventType.SUBSTITUTION, team, off, related_player_id=on.player_id)
        if self.carrier is off:
            self.carrier = on
        self.advance(25_000)

    # ------------------------------------------------------------------ storyline beats
    def side(self, name: str) -> TeamState:
        return self.home if name == "home" else self.away

    def run_beats(self) -> None:
        for beat in self.storyline.beats:
            if beat.done or self.minute < beat.minute:
                continue
            team = self.side(beat.side)
            if beat.kind == "press":
                team.press = float(beat.params["value"])
                beat.done = True
            elif beat.kind == "style":
                for key, value in beat.params.items():
                    setattr(team, key, value)
                beat.done = True
            elif beat.kind == "red_card":
                if self.restart is None and self.att is not team:
                    offender = self.pick(
                        team, beat.params.get("player"), {Position.CB, Position.DM, Position.RB}
                    )
                    assert self.carrier is not None
                    self.foul(team, offender, self.carrier, card=Outcome.RED)
                    beat.done = True
            elif beat.kind in ("goal", "sprint", "pass_of_note"):
                if self.restart is None and self.att is team:
                    getattr(self, f"scripted_{beat.kind}")(team, beat.params)
                    beat.done = True
                elif self.restart is None and self.minute > beat.minute + 1.5:
                    # Force a turnover so the scripted moment happens close to its minute.
                    winner = self.pick(team, None, {Position.DM, Position.CM})
                    self.emit(EventType.BALL_RECOVERY, team, winner, start=self.ball)
                    self.gain(team, winner, self.ball, open_play=True)
            if not beat.done:
                return  # keep beats ordered: later beats wait for earlier ones

    def pick(self, team: TeamState, player_id: str | None, prefer: set[Position]) -> Player:
        if player_id:
            on = [p for p in team.players if p.player_id == player_id]
            if on:
                return on[0]
        options = [p for p, s in zip(team.players, team.slots, strict=True) if s in prefer]
        return self.rng.choice(options or team.outfield())

    def move_ball_to(self, team: TeamState, player: Player) -> None:
        """Get the ball to `player` with a completed pass (no-op if they already have it)."""
        if self.carrier is not player:
            self.pass_ball(False, to=player, force_complete=True)
            self.advance(self.hold_time(False))

    def scripted_goal(self, team: TeamState, params: dict[str, Any]) -> None:
        rng = self.rng
        style = params.get("style", "through_ball")
        scorer = self.pick(team, params.get("scorer"), {Position.ST})
        assister = self.pick(
            team, params.get("assister"), {Position.AM, Position.CM, Position.RW, Position.LW}
        )
        if assister is scorer:
            assister = self.pick(team, None, {Position.CM, Position.DM})
        self.move_ball_to(team, assister)
        if style == "long_shot":
            self.pass_ball(
                False, to=scorer, target=(rng.uniform(78, 82), rng.uniform(28, 40)), force_complete=True
            )
            self.advance(self.hold_time(False))
            self.shoot(False, force="goal", one_on_one=False)
        elif style == "cross":
            flank_y = rng.choice([8.0, PITCH_WIDTH - 8.0])
            self.carry(False, to=(rng.uniform(84, 90), flank_y))
            self.pass_ball(
                False,
                to=scorer,
                target=(rng.uniform(96, 100), rng.uniform(29, 39)),
                force_complete=True,
                qualifiers=("cross",),
            )
            self.advance(250)
            self.shoot(False, header=True, force="goal", one_on_one=False)
        else:
            start = self.to_team(team, self.ball)
            if start[0] < 55:
                self.carry(False, to=(rng.uniform(56, 62), rng.uniform(26, 42)))
            self.transition_until = self.ms + 6000
            self.pass_ball(
                True,
                to=scorer,
                target=(rng.uniform(92, 97), rng.uniform(28, 40)),
                force_complete=True,
                qualifiers=("through_ball",),
            )
            self.advance(self.hold_time(False) * 0.5)
            self.shoot(False, force="goal", one_on_one=True)

    def scripted_sprint(self, team: TeamState, params: dict[str, Any]) -> None:
        """A long breakaway run: the runner collects the ball in midfield and sprints at the target speed."""
        runner = self.pick(team, params.get("player"), {Position.RW, Position.LW, Position.ST})
        length = float(params.get("length_m", 40))
        ball = self.to_team(team, self.ball)
        pickup = (min(max(ball[0] + 10, 40.0), 92.0 - length), min(max(ball[1], 10.0), PITCH_WIDTH - 10))
        if self.carrier is not runner:
            self.pass_ball(False, to=runner, target=pickup, force_complete=True)
        self.transition_until = self.ms + 8000
        end = (pickup[0] + length, pickup[1] + (PITCH_WIDTH / 2 - pickup[1]) * 0.3)
        self.carry(False, to=end, speed_kmh=float(params.get("speed_kmh", 34.2)))

    def scripted_pass_of_note(self, team: TeamState, params: dict[str, Any]) -> None:
        passer = self.pick(team, params.get("player"), {Position.AM, Position.CM})
        receiver = self.pick(team, params.get("receiver"), {Position.ST, Position.RW, Position.LW})
        self.move_ball_to(team, passer)
        start = self.to_team(team, self.ball)
        if start[0] < 55 or start[0] > 68:
            self.carry(False, to=(60.0, PITCH_WIDTH / 2 + self.rng.gauss(0, 4)))
        self.pass_ball(
            True,
            to=receiver,
            target=(self.rng.uniform(94, 97), self.rng.uniform(29, 35)),
            force_complete=True,
            qualifiers=("through_ball",),
        )

    # ------------------------------------------------------------------ metadata
    def build_meta(self) -> MatchMeta:
        rng = self.rng
        return MatchMeta(
            match_id=self.match_id,
            competition=self.league.name,
            venue=self.home.club.venue,
            kickoff_utc=self.kickoff_utc,
            weather=rng.choice(WEATHER),
            referee=rng.choice(REFEREES),
            home=self.home.club.team_info(),
            away=self.away.club.team_info(),
            lineups={t.club_id: t.club.lineup() for t in (self.home, self.away)},
            pre_match=PreMatch(
                home_strength=round(self.home.club.strength + HOME_ADVANTAGE, 2),
                away_strength=round(self.away.club.strength, 2),
            ),
            seed=self.seed,
            storyline=self.storyline.name,
        )


def simulate(
    league: League,
    home_id: str,
    away_id: str,
    *,
    seed: int,
    storyline: StorylineSpec | None = None,
    kickoff_utc: datetime | None = None,
) -> SimulatedMatch:
    return MatchSimulator(
        league, home_id, away_id, seed=seed, storyline=storyline, kickoff_utc=kickoff_utc
    ).run()
