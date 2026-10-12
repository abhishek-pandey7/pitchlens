"""Moment detectors (§9, T-31). Each proposes `Candidate`s; the engine adds ids, cooldowns and salience.

Three kinds of input:
- `on_event(x)`: every enriched event (goals, chances, passes, cards, streaks, milestones)
- `on_tick(row)`: every 30 s dynamics sample from `MatchState.readings()` (momentum, pressing, control,
  rhythm, xG swings)
- `on_alert(alert)`: physical thresholds from tracking (top speed, distance, sprints)

Evidence only ever carries values the interpret stage computed; detectors decide *that* something matters
and record *why* (why_tags), never invent numbers.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from pitchlens.explain import counterfactual
from pitchlens.interpret.geometry import OWN_DEFENSIVE_60_X, PRESS_ZONE_X
from pitchlens.interpret.state import MatchState
from pitchlens.schema.enriched import AlertKind, EnrichedEvent, PhysicalAlert
from pitchlens.schema.events import EventType, Outcome
from pitchlens.schema.insights import Evidence, InsightType

from .changepoint import RateShift, rate_shift

MIN = 60_000


@dataclass
class Candidate:
    type: InsightType
    headline_key: str
    evidence: list[Evidence]
    teams: list[str] = field(default_factory=list)
    players: list[str] = field(default_factory=list)
    why_tags: list[str] = field(default_factory=list)
    related_events: list[str] = field(default_factory=list)
    wp_delta: float = 0.0
    rarity: float = 0.5
    cooldown_key: str | None = None
    cooldown_ms: int = 0
    weight_override: float | None = None
    match_ms: int | None = None  # defaults to the triggering event / sample
    clock_ms: int | None = None
    period: int | None = None


def ev(metric: str, value: Any = None, **kw: Any) -> Evidence:
    if "from_" in kw:
        kw["from"] = kw.pop("from_")
    return Evidence.model_validate({"metric": metric, "value": value, **kw})


class Detector:
    name = "detector"

    def __init__(self, state: MatchState) -> None:
        self.state = state

    def on_event(self, x: EnrichedEvent) -> list[Candidate]:
        return []

    def on_tick(self, row: dict[str, Any]) -> list[Candidate]:
        return []

    def on_alert(self, alert: PhysicalAlert) -> list[Candidate]:
        return []

    # helpers
    def wp_evidence(self, x: EnrichedEvent, team: str) -> tuple[list[Evidence], float]:
        if "wp_before" not in x.metrics:
            return [], 0.0
        before, after = float(x.metrics["wp_before"]), float(x.metrics["wp_after"])
        return [ev("win_prob", team=team, from_=round(before, 3), to=round(after, 3))], after - before


# ======================================================================== event detectors
class GoalDetector(Detector):
    name = "goal"

    def __init__(self, state: MatchState) -> None:
        super().__init__(state)
        self._last_shot: dict[str, EnrichedEvent] = {}

    def on_event(self, x: EnrichedEvent) -> list[Candidate]:
        e = x.event
        if e.type is EventType.SHOT and e.team_id:
            self._last_shot[e.team_id] = x
            return []
        if e.type is not EventType.GOAL or not e.team_id:
            return []
        team = e.team_id
        home, away = x.home_score, x.away_score
        own, opp = (home, away) if self.state.is_home(team) else (away, home)
        minute = e.clock_ms / MIN
        evidence = [ev("score", f"{home}-{away}")]
        shot = self._last_shot.get(team)
        rarity = 0.5
        related = [e.event_id]
        if shot is not None:
            related.insert(0, shot.event.event_id)
            evidence.append(ev("xg", shot.metrics.get("xg"), player=e.player_id))
            rarity = 1 - float(shot.metrics.get("xg", 0.5))
            if "shot_speed_kmh" in shot.metrics:
                evidence.append(ev("shot_speed_kmh", shot.metrics["shot_speed_kmh"], unit="km/h"))
            if "assist_pass_difficulty" in shot.metrics:
                evidence.append(ev("assist_pass_difficulty", shot.metrics["assist_pass_difficulty"]))
        wp, delta = self.wp_evidence(x, team)
        evidence += wp
        if own == opp:
            key, tags = "goal.equaliser", ["score_change", "equaliser"]
        elif own == opp + 1:
            key, tags = "goal.go_ahead", ["score_change", "go_ahead"]
        elif own > opp:
            key, tags = "goal.extends_lead", ["score_change"]
        else:
            key, tags = "goal.pulls_back", ["score_change"]
        if minute >= 85 and abs(own - opp) <= 1:
            tags.append("late_drama")
        players = [p for p in (e.player_id, e.related_player_id) if p]
        candidates = [
            Candidate(
                InsightType.GOAL,
                key,
                evidence,
                teams=[team],
                players=players,
                why_tags=tags,
                related_events=related,
                wp_delta=delta,
                rarity=rarity,
            )
        ]
        candidates += self._career_goal_milestone(e.player_id, team, e.event_id)
        return candidates

    def _career_goal_milestone(self, player_id: str | None, team: str, event_id: str) -> list[Candidate]:
        if not player_id:
            return []
        lp = next(
            (p for lps in self.state.meta.lineups.values() for p in lps if p.player_id == player_id), None
        )
        if lp is None or lp.career_goals is None:
            return []
        total = lp.career_goals + self.state.players[player_id].goals
        if total == 1 or total % 25 == 0:
            key = "career_milestone.first_goal" if total == 1 else "career_milestone.goals"
            return [
                Candidate(
                    InsightType.CAREER_MILESTONE,
                    key,
                    [ev("career_goals", total, player=player_id)],
                    teams=[team],
                    players=[player_id],
                    why_tags=["milestone"],
                    related_events=[event_id],
                    rarity=0.8 if total % 50 == 0 else 0.6,
                )
            ]
        return []


class AppearanceMilestoneDetector(Detector):
    name = "appearance_milestone"

    def __init__(self, state: MatchState) -> None:
        super().__init__(state)
        self._lineup = {lp.player_id: (club, lp) for club, lps in state.meta.lineups.items() for lp in lps}

    def on_event(self, x: EnrichedEvent) -> list[Candidate]:
        e = x.event
        lineup = self._lineup
        if e.type is EventType.PERIOD_START and e.period == 1:
            entering = [pid for pid, (_, lp) in lineup.items() if lp.starter]
        elif e.type is EventType.SUBSTITUTION and e.related_player_id:
            entering = [e.related_player_id]
        else:
            return []
        out = []
        for pid in entering:
            club, lp = lineup[pid]
            if lp.career_appearances is None:
                continue
            n = lp.career_appearances + 1
            if n == 1 or n % 100 == 0:
                key = "career_milestone.debut" if n == 1 else "career_milestone.appearances"
                out.append(
                    Candidate(
                        InsightType.CAREER_MILESTONE,
                        key,
                        [ev("career_appearances", n, player=pid)],
                        teams=[club],
                        players=[pid],
                        why_tags=["milestone"],
                        related_events=[e.event_id],
                        rarity=0.8 if n % 100 == 0 else 0.5,
                        weight_override=0.4,
                    )
                )
        return out


class BigChanceDetector(Detector):
    name = "big_chance"
    THRESHOLD = 0.35

    def on_event(self, x: EnrichedEvent) -> list[Candidate]:
        e = x.event
        if e.type is not EventType.SHOT or not e.team_id or e.outcome is Outcome.GOAL:
            return []
        xg = float(x.metrics.get("xg", 0.0))
        if xg < self.THRESHOLD:
            return []
        key = {
            Outcome.ON_TARGET: "big_chance.saved",
            Outcome.BLOCKED: "big_chance.blocked",
        }.get(e.outcome or Outcome.OFF_TARGET, "big_chance.missed")
        evidence = [
            ev("xg", xg, player=e.player_id),
            ev("shot_distance_m", x.metrics.get("shot_distance_m"), unit="m"),
        ]
        if "shot_speed_kmh" in x.metrics:
            evidence.append(ev("shot_speed_kmh", x.metrics["shot_speed_kmh"], unit="km/h"))
        evidence.append(counterfactual.if_scored(self.state, e.team_id))  # what if it had gone in
        return [
            Candidate(
                InsightType.BIG_CHANCE,
                key,
                evidence,
                teams=[e.team_id],
                players=[e.player_id or ""],
                why_tags=["missed_opportunity"],
                related_events=[e.event_id],
                rarity=xg,
                cooldown_key=f"big_chance:{e.team_id}",
                cooldown_ms=MIN,
            )
        ]


class ShotSpeedRecordDetector(Detector):
    name = "shot_speed_record"
    ABSOLUTE_KMH = 110.0
    RECORD_MIN_KMH = 95.0

    def __init__(self, state: MatchState) -> None:
        super().__init__(state)
        self._fastest = 0.0
        self._shots = 0

    def on_event(self, x: EnrichedEvent) -> list[Candidate]:
        e = x.event
        if e.type is not EventType.SHOT or "shot_speed_kmh" not in x.metrics:
            return []
        speed = float(x.metrics["shot_speed_kmh"])
        previous, self._fastest = self._fastest, max(self._fastest, speed)
        self._shots += 1
        record = speed > previous and self._shots > 3 and speed >= self.RECORD_MIN_KMH
        if not (record or speed >= self.ABSOLUTE_KMH):
            return []
        evidence = [ev("shot_speed_kmh", speed, player=e.player_id, unit="km/h")]
        if previous:
            evidence.append(ev("previous_fastest_kmh", previous, unit="km/h"))
        return [
            Candidate(
                InsightType.SHOT_SPEED_RECORD,
                "shot_speed_record.fastest",
                evidence,
                teams=[e.team_id or ""],
                players=[e.player_id or ""],
                why_tags=["record"],
                related_events=[e.event_id],
                rarity=min(1.0, (speed - 80) / 45),
                cooldown_key="shot_speed",
                cooldown_ms=5 * MIN,
            )
        ]


class PassOfNoteDetector(Detector):
    name = "pass_of_note"

    def on_event(self, x: EnrichedEvent) -> list[Candidate]:
        e = x.event
        if e.type is not EventType.PASS or e.outcome is not Outcome.COMPLETE:
            return []
        difficulty = float(x.metrics.get("pass_difficulty", 0.0))
        lines = x.metrics.get("lines_broken")
        # Roughly the top 1% of completed passes, or a genuinely line-splitting ball.
        hard = difficulty >= 62
        splitting = lines is not None and int(lines) >= 2 and difficulty >= 50
        if not (hard or splitting):
            return []
        evidence = [
            ev("pass_difficulty", difficulty, player=e.player_id),
            ev("pass_distance_m", x.metrics.get("pass_distance_m"), unit="m"),
        ]
        if lines is not None:
            evidence.append(ev("lines_broken", lines))
        if x.metrics.get("into_box"):
            evidence.append(ev("into_box", "yes"))
        key = (
            "pass_of_note.line_breaking"
            if lines is not None and int(lines) >= 2
            else "pass_of_note.difficult"
        )
        return [
            Candidate(
                InsightType.PASS_OF_NOTE,
                key,
                evidence,
                teams=[e.team_id or ""],
                players=[p for p in (e.player_id, e.recipient_id) if p],
                why_tags=["skill"],
                related_events=[e.event_id],
                rarity=min(1.0, max(0.0, (difficulty - 50) / 40)),
                cooldown_key="pass_of_note",
                cooldown_ms=2 * MIN,
            )
        ]


class PlayerStreakDetector(Detector):
    name = "player_streak"
    PASS_STREAKS = (20, 30, 45)
    TACKLES_IN_WINDOW = 3
    TACKLE_WINDOW_MS = 5 * MIN

    def __init__(self, state: MatchState) -> None:
        super().__init__(state)
        self._streak: defaultdict[str, int] = defaultdict(int)
        self._tackles: defaultdict[str, deque[int]] = defaultdict(deque)

    def on_event(self, x: EnrichedEvent) -> list[Candidate]:
        e = x.event
        pid, team = e.player_id, e.team_id
        if not pid or not team:
            return []
        if e.type is EventType.PASS:
            if e.outcome is not Outcome.COMPLETE:
                self._streak[pid] = 0
                return []
            self._streak[pid] += 1
            n = self._streak[pid]
            if n in self.PASS_STREAKS:
                return [
                    Candidate(
                        InsightType.PLAYER_STREAK,
                        "player_streak.passes",
                        [ev("consecutive_completed_passes", n, player=pid)],
                        teams=[team],
                        players=[pid],
                        why_tags=["consistency"],
                        related_events=[e.event_id],
                        rarity=min(1.0, n / 40),
                    )
                ]
        if e.type is EventType.TACKLE and e.outcome is Outcome.SUCCESS:
            window = self._tackles[pid]
            window.append(e.match_ms)
            while window and e.match_ms - window[0] > self.TACKLE_WINDOW_MS:
                window.popleft()
            if len(window) >= self.TACKLES_IN_WINDOW:
                window.clear()
                return [
                    Candidate(
                        InsightType.PLAYER_STREAK,
                        "player_streak.tackles",
                        [ev("tackles_won", self.TACKLES_IN_WINDOW, player=pid, window_min=5)],
                        teams=[team],
                        players=[pid],
                        why_tags=["defensive_intensity"],
                        related_events=[e.event_id],
                        rarity=0.6,
                        cooldown_key=f"tackles:{pid}",
                        cooldown_ms=10 * MIN,
                    )
                ]
        return []


class CardDetector(Detector):
    name = "card"

    def on_event(self, x: EnrichedEvent) -> list[Candidate]:
        e = x.event
        if e.type is not EventType.CARD or not e.team_id:
            return []
        wp, delta = self.wp_evidence(x, e.team_id)
        outcome = e.outcome or Outcome.YELLOW
        dismissal = outcome in (Outcome.RED, Outcome.SECOND_YELLOW)
        evidence = [ev("card", outcome.value, player=e.player_id), *wp]
        if dismissal:
            evidence.append(ev("players_on_pitch", len(self.state.teams[e.team_id].on_pitch), team=e.team_id))
            evidence.append(counterfactual.if_eleven(self.state, e.team_id))  # what if still eleven
        return [
            Candidate(
                InsightType.CARD,
                f"card.{outcome.value}",
                evidence,
                teams=[e.team_id],
                players=[e.player_id or ""],
                why_tags=["dismissal", "numerical_disadvantage"] if dismissal else ["discipline"],
                related_events=[e.event_id],
                wp_delta=delta,
                rarity=0.9 if dismissal else 0.2,
                weight_override=0.7 if dismissal else 0.2,
            )
        ]


class WinProbSwingDetector(Detector):
    """Large win-probability moves not already explained by a goal or a card."""

    name = "win_prob_swing"
    THRESHOLD = 0.10

    def on_event(self, x: EnrichedEvent) -> list[Candidate]:
        e = x.event
        covered = (EventType.GOAL, EventType.CARD, EventType.SHOT)  # explained by their own detectors
        if e.type in covered or "wp_before" not in x.metrics or not e.team_id:
            return []
        wp, delta = self.wp_evidence(x, e.team_id)
        if abs(delta) < self.THRESHOLD:
            return []
        return [
            Candidate(
                InsightType.WIN_PROB_SWING,
                "win_prob_swing.event",
                wp,
                teams=[e.team_id],
                players=[e.player_id] if e.player_id else [],
                why_tags=["game_state_critical"],
                related_events=[e.event_id],
                wp_delta=delta,
                cooldown_key="wp_swing",
                cooldown_ms=3 * MIN,
            )
        ]


# ======================================================================== tick detectors
class MomentumShiftDetector(Detector):
    name = "momentum_shift"
    MAGNITUDE = 0.2
    HOLD_SAMPLES = 6  # 3 minutes at 30 s sampling

    def __init__(self, state: MatchState) -> None:
        super().__init__(state)
        self._established: str | None = None
        self._candidate: str | None = None
        self._held = 0
        self._history: deque[dict[str, Any]] = deque(maxlen=self.HOLD_SAMPLES + 1)

    def on_tick(self, row: dict[str, Any]) -> list[Candidate]:
        self._history.append(row)
        m = row["momentum"]
        side = (
            self.state.home_id if m > self.MAGNITUDE else self.state.away_id if m < -self.MAGNITUDE else None
        )
        if side is None or side == self._established:
            self._candidate, self._held = None, 0
            return []
        if side != self._candidate:
            self._candidate, self._held = side, 0
        self._held += 1
        if self._held < self.HOLD_SAMPLES:
            return []
        previous, self._established = self._established, side
        self._candidate, self._held = None, 0
        if previous is None:
            return []  # the first side to establish control is not a "shift"
        start = self._history[0]
        is_home = side == self.state.home_id

        def own_tilt(r: dict[str, Any]) -> float:
            return round(r["field_tilt"] if is_home else 1 - r["field_tilt"], 3)

        def own_momentum(r: dict[str, Any]) -> float:
            return round(r["momentum"] if is_home else -r["momentum"], 3)

        evidence = [
            ev("momentum", team=side, from_=own_momentum(start), to=own_momentum(row)),
            ev("field_tilt", team=side, from_=own_tilt(start), to=own_tilt(row), window_min=10),
        ]
        return [
            Candidate(
                InsightType.MOMENTUM_SHIFT,
                "momentum_shift.took_over",
                evidence,
                teams=[side],
                why_tags=["territory_swing"],
                rarity=min(1.0, abs(m)),
                cooldown_key="momentum",
                cooldown_ms=8 * MIN,
            )
        ]


class PressureChangeDetector(Detector):
    """A team's pressing intensity changes.

    Intensity = the team's pressures, tackles and interceptions inside the opponent's own 60%, per
    opponent pass there (the exposure PPDA uses, with ~4x more events than PPDA's defensive actions).
    Per-minute rates would mislead: a successful press wins the ball, leaving fewer passes to press.
    Counts go into one-minute bins; each minute the last 10 minutes are tested against the 20 before
    with a Poisson likelihood-ratio test (`changepoint.rate_shift`). PPDA is attached as evidence.
    """

    name = "pressure_change"
    BIN_MS = MIN
    REFERENCE_BINS = 20
    RECENT_BINS = 10
    PRESSING_ACTIONS = (EventType.PRESSURE, EventType.TACKLE, EventType.INTERCEPTION)

    def __init__(self, state: MatchState) -> None:
        super().__init__(state)
        teams = (state.home_id, state.away_id)
        self._counts: dict[str, list[int]] = {t: [0] for t in teams}
        self._exposure: dict[str, list[int]] = {t: [0] for t in teams}  # opponent passes in their own 60%
        self._bin_clock: list[int] = [0]  # football clock at the start of each bin
        self._last_shift: dict[str, int] = {t: -(10**9) for t in teams}

    def on_event(self, x: EnrichedEvent) -> list[Candidate]:
        e = x.event
        out: list[Candidate] = []
        while e.match_ms >= len(self._bin_clock) * self.BIN_MS:
            out += self._close_bin()
            self._bin_clock.append(e.clock_ms)
            for team in self._counts:
                self._counts[team].append(0)
                self._exposure[team].append(0)
        if e.team_id in self._counts and e.start:
            x_att, _ = self.state.attacking(e.team_id, e.start.x, e.start.y, e.period)
            if e.type in self.PRESSING_ACTIONS and x_att > PRESS_ZONE_X:
                self._counts[e.team_id][-1] += 1
            elif e.type is EventType.PASS and x_att < OWN_DEFENSIVE_60_X:
                self._exposure[self.state.opponent(e.team_id)][-1] += 1
        return out

    def _close_bin(self) -> list[Candidate]:
        out = []
        closed = len(self._bin_clock)  # number of complete bins
        a, b = self.REFERENCE_BINS, self.RECENT_BINS
        if closed < a + b:
            return out
        for team in self._counts:
            if closed - self._last_shift[team] < b:
                continue  # the recent window must not overlap the previous shift's evidence
            c, x = self._counts[team], self._exposure[team]
            shift = rate_shift(
                sum(c[closed - a - b : closed - b]), sum(x[closed - a - b : closed - b]),
                sum(c[closed - b : closed]), sum(x[closed - b : closed]),
            )  # fmt: skip
            if shift is not None:
                self._last_shift[team] = closed
                out.append(self._candidate(team, shift, started=self._bin_clock[closed - b]))
        return out

    def _ppda_at(self, team: str, clock_ms: int) -> float | None:
        i = 0 if team == self.state.home_id else 1
        rows = [r for r in self.state.series if r["clock_ms"] <= clock_ms]
        return round(rows[-1]["ppda"][i], 1) if rows else None

    def _candidate(self, team: str, shift: RateShift, *, started: int) -> Candidate:
        intensified = shift.direction > 0
        evidence = [
            ev("pressing_actions_per_10_opp_passes", team=team, from_=round(10 * shift.before, 1),
               to=round(10 * shift.after, 1), window_min=self.RECENT_BINS),
            ev("ppda", team=team, from_=self._ppda_at(team, started),
               to=self._ppda_at(team, self.state.clock_ms), window_min=10),
            ev("window_start_min", round(started / MIN, 1), team=team),
        ]  # fmt: skip
        return Candidate(
            InsightType.PRESSURE_CHANGE,
            "pressure_change.press_intensified" if intensified else "pressure_change.press_eased",
            evidence,
            teams=[team],
            why_tags=["pressure_change"],
            rarity=min(1.0, abs(math.log(max(shift.after, 1e-3) / shift.before))),
            cooldown_key=f"pressure:{team}",
            cooldown_ms=12 * MIN,
            weight_override=None if intensified else 0.35,  # a press easing off is less newsworthy
        )


class ControlChaosDetector(Detector):
    name = "control_chaos_flip"
    HOLD_SAMPLES = 8  # 4 minutes

    def __init__(self, state: MatchState) -> None:
        super().__init__(state)
        self._zone: str | None = None
        self._pending: str | None = None
        self._held = 0
        self._zone_values: list[float] = []

    @staticmethod
    def zone(index: float) -> str:
        return "control" if index >= 60 else "chaos" if index <= 40 else "balanced"

    def on_tick(self, row: dict[str, Any]) -> list[Candidate]:
        index = row["control"]
        if index is None:
            return []
        z = self.zone(index)
        if z == self._zone:
            self._zone_values.append(index)
            self._pending, self._held = None, 0
            return []
        if z != self._pending:
            self._pending, self._held = z, 0
        self._held += 1
        if self._held < self.HOLD_SAMPLES:
            return []
        previous_values, previous_zone = self._zone_values, self._zone
        self._zone, self._zone_values, self._pending, self._held = z, [index], None, 0
        if previous_zone is None or z == "balanced":
            return []
        before = round(sum(previous_values) / len(previous_values), 1) if previous_values else None
        comps = row["control_components"] or {}
        evidence = [ev("control_index", from_=before, to=index)]
        for name, value in comps.items():
            evidence.append(ev(name, round(value, 2), window_min=5))
        teams = []
        if z == "control":
            home_share = row["possession_5m"]
            teams = [self.state.home_id if home_share >= 0.5 else self.state.away_id]
            evidence.append(ev("possession_5m", round(max(home_share, 1 - home_share), 2), team=teams[0]))
        return [
            Candidate(
                InsightType.CONTROL_CHAOS_FLIP,
                f"control_chaos_flip.to_{z}",
                evidence,
                teams=teams,
                why_tags=["control_vs_chaos"],
                rarity=min(1.0, abs(index - 50) / 40),
                cooldown_key="control_chaos",
                cooldown_ms=6 * MIN,
            )
        ]


class RhythmBreakDetector(Detector):
    name = "rhythm_break"
    Z = 1.8
    HOLD_SAMPLES = 2

    def __init__(self, state: MatchState) -> None:
        super().__init__(state)
        self._run = 0
        self._sign = 0

    def on_tick(self, row: dict[str, Any]) -> list[Candidate]:
        z = row.get("tempo_z")
        if z is None or abs(z) <= self.Z:
            self._run, self._sign = 0, 0
            return []
        sign = 1 if z > 0 else -1
        self._run = self._run + 1 if sign == self._sign else 1
        self._sign = sign
        if self._run != self.HOLD_SAMPLES:
            return []
        evidence = [
            ev("tempo", row["tempo"], unit="actions/min", window_min=5),
            ev("tempo_baseline", row.get("tempo_baseline"), unit="actions/min"),
            ev("stoppage_share", row["stoppage_share"], window_min=5),
        ]
        return [
            Candidate(
                InsightType.RHYTHM_BREAK,
                "rhythm_break.faster" if sign > 0 else "rhythm_break.slower",
                evidence,
                why_tags=["game_rhythm"],
                rarity=min(1.0, (abs(z) - 1.5) / 1.5),
                cooldown_key="rhythm",
                cooldown_ms=10 * MIN,
            )
        ]


class XgSwingDetector(Detector):
    name = "xg_swing"
    WINDOW_SAMPLES = 20  # 10 minutes
    GAP = 0.8

    def __init__(self, state: MatchState) -> None:
        super().__init__(state)
        self._history: deque[float] = deque(maxlen=self.WINDOW_SAMPLES + 1)
        self._leader: int = 0

    def on_tick(self, row: dict[str, Any]) -> list[Candidate]:
        home_xg, away_xg = row["xg"]
        diff = home_xg - away_xg
        self._history.append(diff)
        leader = 1 if diff >= 0.3 else -1 if diff <= -0.3 else 0
        out_key = None
        if leader and leader != self._leader and self._leader:
            out_key = "xg_swing.lead_flipped"
        elif len(self._history) > self.WINDOW_SAMPLES and abs(diff - self._history[0]) >= self.GAP:
            out_key = "xg_swing.gap_moved"
        if leader:
            self._leader = leader
        if out_key is None:
            return []
        gaining = self.state.home_id if diff - self._history[0] > 0 else self.state.away_id
        evidence = [
            ev("xg", round(home_xg, 2), team=self.state.home_id),
            ev("xg", round(away_xg, 2), team=self.state.away_id),
            ev(
                "xg_difference",
                team=gaining,
                from_=round(abs(self._history[0]), 2),
                to=round(abs(diff), 2),
                window_min=10,
            ),
        ]
        return [
            Candidate(
                InsightType.XG_SWING,
                out_key,
                evidence,
                teams=[gaining],
                why_tags=["chance_quality"],
                rarity=min(1.0, abs(diff - self._history[0]) / 2),
                cooldown_key="xg_swing",
                cooldown_ms=10 * MIN,
            )
        ]


# ======================================================================== physical alerts
class PhysicalAlertDetector(Detector):
    name = "physical"

    def on_alert(self, alert: PhysicalAlert) -> list[Candidate]:
        if alert.kind is AlertKind.TOP_SPEED:
            itype, key, weight = InsightType.SPRINT_THRESHOLD, "sprint_threshold.top_speed", None
        elif alert.kind is AlertKind.DISTANCE:
            itype, key, weight = InsightType.DISTANCE_MILESTONE, "distance_milestone.km", None
            if alert.threshold < 10:
                weight = 0.12  # 5 km / 8 km are worth a tag, rarely a headline
        else:
            itype, key, weight = InsightType.DISTANCE_MILESTONE, "distance_milestone.sprints", 0.15
        evidence = [
            ev(alert.kind.value, alert.value, player=alert.player_id, unit=alert.unit),
            ev("threshold", alert.threshold, unit=alert.unit),
        ]
        return [
            Candidate(
                itype,
                key,
                evidence,
                teams=[alert.team_id],
                players=[alert.player_id],
                why_tags=["physical"],
                rarity=0.7 if alert.kind is AlertKind.TOP_SPEED else 0.4,
                weight_override=weight,
                match_ms=alert.match_ms,
            )
        ]


DEFAULT_DETECTORS: tuple[type[Detector], ...] = (
    GoalDetector,
    AppearanceMilestoneDetector,
    BigChanceDetector,
    ShotSpeedRecordDetector,
    PassOfNoteDetector,
    PlayerStreakDetector,
    CardDetector,
    WinProbSwingDetector,
    MomentumShiftDetector,
    PressureChangeDetector,
    ControlChaosDetector,
    RhythmBreakDetector,
    XgSwingDetector,
    PhysicalAlertDetector,
)
