"""Analyst tools (§10A, T-121): typed, read-only views over the deterministic engine.

Every number an agent states must come from one of these tools ("facts first, words second"). The
functions are plain Python with no framework imports, so the same tools serve Microsoft Agent Framework
agents (`agents/runtime.py`) and the PitchLens MCP server (`mcpserver/`). Each call is recorded in a
`Ledger`. The Fact-Checker verifies an agent's numbers against it, and the Agent Trace panel shows it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from statistics import fmean
from typing import Annotated, Any

from pydantic import Field

from pitchlens.clock import minute_label
from pitchlens.interpret.state import MatchState
from pitchlens.schema.insights import Insight, InsightType


class Metric(StrEnum):
    POSSESSION = "possession"  # rolling 5-minute share
    FIELD_TILT = "field_tilt"  # share of final-third touches, rolling 10 minutes
    PPDA = "ppda"  # opponent passes per defensive action; lower = more pressing
    PRESS_RATE = "press_rate"  # pressing actions per opponent pass
    MOMENTUM = "momentum"  # decayed threat, home minus away, -1..1
    XG = "xg"  # cumulative expected goals
    WIN_PROBABILITY = "win_probability"
    CONTROL = "control"  # 0 chaos .. 100 control
    TEMPO = "tempo"  # events per minute


PLAYER_STATS = (
    "goals", "assists", "xg", "shots", "shots_on_target", "max_shot_speed_kmh", "touches", "passes",
    "pass_accuracy", "avg_pass_difficulty", "hardest_completed_pass", "key_passes", "progressive_passes",
    "lines_broken", "tackles", "interceptions", "recoveries", "pressures", "fouls", "distance_km",
    "top_speed_kmh", "sprints",
)  # fmt: skip
PHYSICAL_STATS = ("distance_km", "top_speed_kmh", "sprints")
# Player of the match (§10): goal contributions, chance quality and creation, progression, defending
# and running, minus discipline. Points per unit of each stat.
POTM_WEIGHTS = {
    "goals": 4.0, "assists": 3.0, "xg": 2.0, "key_passes": 0.75, "progressive_passes": 0.1,
    "lines_broken": 0.1, "tackles": 0.25, "interceptions": 0.25, "recoveries": 0.1, "blocks": 0.15,
    "distance_km": 0.2, "on_winning_side": 1.5, "yellow_cards": -0.5, "red_cards": -3.0,
}  # fmt: skip
POTM_METHOD = "points = " + " + ".join(f"{w:g}*{k}" for k, w in POTM_WEIGHTS.items()).replace("+ -", "- ")
PlayerStat = Annotated[str, Field(description=f"One of: {', '.join(PLAYER_STATS)}")]
# Parameters are plain strings and numbers on purpose: some providers (Groq) reject a whole request
# when a generated argument breaks an enum or range in the schema. Our tools validate instead and
# return a ToolError the model can read and correct.
Minute = Annotated[float, Field(description="Match minute, e.g. 60 or 92.5")]
MetricName = Annotated[
    str,
    Field(
        description="One of: possession (share 0..1), field_tilt (share of final-third touches 0..1), "
        "ppda (opponent passes per defensive action; lower = more pressing), press_rate (pressing "
        "actions per opponent pass; higher = more pressing), momentum (home minus away, -1..1), xg "
        "(cumulative expected goals), win_probability (0..1), control (index 0 chaos..100 control), "
        "tempo (events per minute)"
    ),
]
InsightTypeName = Annotated[
    str | None,
    Field(
        description="Optional filter, e.g. goal, big_chance, momentum_shift, pressure_change, "
        "control_chaos_flip"
    ),
]


@dataclass(frozen=True)
class MatchView:
    """What the tools read: a match state plus the insights detected so far (live or stored)."""

    state: MatchState
    insights: list[Insight] = field(default_factory=list)


@dataclass
class ToolCall:
    tool: str
    args: dict[str, Any]
    result: Any
    ms: float
    error: str | None = None


@dataclass
class Ledger:
    """Every tool call in one agent run, in order."""

    calls: list[ToolCall] = field(default_factory=list)

    def results(self) -> list[Any]:
        return [c.result for c in self.calls if c.error is None]


class ToolError(ValueError):
    """Bad arguments (unknown team, player, metric). Returned to the model as an error it can correct."""


def _metric(metric: Metric | str) -> Metric:
    try:
        return Metric(metric)
    except ValueError:
        raise ToolError(
            f"unknown metric {metric!r}; use one of {', '.join(m.value for m in Metric)}"
        ) from None


def _insight_type(value: str) -> InsightType:
    try:
        return InsightType(value)
    except ValueError:
        raise ToolError(f"unknown insight type {value!r}") from None


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


class MatchTools:
    """The analyst tool set for one match view. `view` is a callable so live sessions stay current."""

    def __init__(self, view: Callable[[], MatchView], ledger: Ledger | None = None) -> None:
        self._view = view
        self.ledger = ledger if ledger is not None else Ledger()

    # ------------------------------------------------------------------ plumbing
    @property
    def state(self) -> MatchState:
        return self._view().state

    def _record(self, tool: str, args: dict[str, Any], fn: Callable[[], Any]) -> Any:
        start = time.perf_counter()
        try:
            result = fn()
        except ToolError as exc:
            self.ledger.calls.append(
                ToolCall(tool, args, None, (time.perf_counter() - start) * 1000, str(exc))
            )
            raise
        self.ledger.calls.append(ToolCall(tool, args, result, (time.perf_counter() - start) * 1000))
        return result

    def _validate(self, tool: str, args: dict[str, Any], fn: Callable[[], Any]) -> Any:
        """Argument checks that fail are logged as failed calls too (the trace shows every attempt)."""
        try:
            return fn()
        except ToolError as exc:
            self.ledger.calls.append(ToolCall(tool, args, None, 0.0, str(exc)))
            raise

    def resolve_team(self, team: str) -> str:
        """A club id from a club id or team name (ToolError if unknown)."""
        return self._team(team)

    def _team(self, team: str) -> str:
        s = self.state
        wanted = team.strip().lower()
        for club_id, t in s.teams.items():
            names = {club_id.lower(), t.name.lower(), t.name.split()[0].lower()}
            if wanted in names or any(n.startswith(wanted) for n in names if len(wanted) >= 4):
                return club_id
        raise ToolError(f"unknown team {team!r}; use {', '.join(s.teams)} or the team name")

    def _player(self, player: str) -> str:
        s = self.state
        wanted = player.strip().lower()
        if player in s.players:
            return player
        hits = [
            pid for pid, p in s.players.items()
            if wanted in (p.name.lower(), p.short_name.lower()) or p.name.lower().endswith(" " + wanted)
        ]  # fmt: skip
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise ToolError(f"unknown player {player!r}")
        raise ToolError(f"{player!r} is ambiguous: {', '.join(s.players[h].name for h in hits)}")

    def _names(self) -> tuple[str, str]:
        s = self.state
        return s.teams[s.home_id].name, s.teams[s.away_id].name

    def _now(self) -> dict[str, Any]:
        s = self.state
        return {"minute": minute_label(s.clock_ms, s.period), "period": s.period}

    def _metric_row(self, row: dict[str, Any], metric: Metric) -> dict[str, float | None]:
        """One time-series row as {home team name: value, away team name: value} (or a single value)."""
        home, away = self._names()
        if metric is Metric.POSSESSION:
            return {home: _r(row["possession_5m"], 3), away: _r(1 - row["possession_5m"], 3)}
        if metric is Metric.FIELD_TILT:
            return {home: _r(row["field_tilt"], 3), away: _r(1 - row["field_tilt"], 3)}
        if metric is Metric.PPDA:
            return {home: row["ppda"][0], away: row["ppda"][1]}
        if metric is Metric.PRESS_RATE:
            return {home: row["press_rate"][0], away: row["press_rate"][1]}
        if metric is Metric.XG:
            return {home: row["xg"][0], away: row["xg"][1]}
        if metric is Metric.WIN_PROBABILITY:
            return {home: row["wp"][0], "draw": row["wp"][1], away: row["wp"][2]}
        if metric is Metric.MOMENTUM:
            return {"home_minus_away": row["momentum"]}
        if metric is Metric.CONTROL:
            return {"index": row["control"]}
        return {"events_per_min": row["tempo"]}

    def _series(self, from_minute: float, to_minute: float) -> list[dict[str, Any]]:
        if to_minute < from_minute:
            raise ToolError("to_minute must be after from_minute")
        rows = [r for r in self.state.series if from_minute * 60_000 <= r["clock_ms"] <= to_minute * 60_000]
        if not rows:
            raise ToolError(f"no data between minute {from_minute:g} and {to_minute:g} yet")
        return rows

    # ------------------------------------------------------------------ tools
    def get_match_state(self) -> dict[str, Any]:
        """Score, minute, team stats, win probability, momentum and control index right now."""

        def run() -> dict[str, Any]:
            s = self.state
            snap = s.snapshot()
            home, away = self._names()
            dyn = snap["dynamics"]

            def team(view: dict[str, Any], name: str) -> dict[str, Any]:
                return {"team": name, **{k: v for k, v in view.items() if k != "club_id"}}

            return {
                **self._now(),
                "score": f"{home} {s.score[0]}-{s.score[1]} {away}",
                "finished": s.finished,
                "home": team(snap["home"], home),
                "away": team(snap["away"], away),
                "win_probability": {home: dyn["wp"][0], "draw": dyn["wp"][1], away: dyn["wp"][2]},
                "momentum_home_minus_away": dyn["momentum"],
                "control_index": dyn["control"],
            }

        return self._record("get_match_state", {}, run)

    def get_metric(self, metric: MetricName) -> dict[str, Any]:
        """A team metric at the current moment (possession, field_tilt, ppda, press_rate, momentum, xg,
        win_probability, control, tempo)."""
        metric = self._validate("get_metric", {"metric": metric}, lambda: _metric(metric))

        def run() -> dict[str, Any]:
            return {
                **self._now(),
                "metric": metric.value,
                "value": self._metric_row(self.state.readings(), metric),
            }

        return self._record("get_metric", {"metric": metric.value}, run)

    def get_window(self, metric: MetricName, from_minute: Minute, to_minute: Minute) -> dict[str, Any]:
        """How a metric moved between two minutes: start, end, min, max and mean per team."""
        metric = self._validate("get_window", {"metric": metric}, lambda: _metric(metric))

        def run() -> dict[str, Any]:
            rows = [self._metric_row(r, metric) for r in self._series(from_minute, to_minute)]
            summary = {}
            for key in rows[0]:
                values = [r[key] for r in rows if r[key] is not None]
                if values:
                    summary[key] = {
                        "start": values[0], "end": values[-1], "min": min(values), "max": max(values),
                        "mean": _r(fmean(values), 3),
                    }  # fmt: skip
            return {
                "metric": metric.value,
                "from_minute": from_minute,
                "to_minute": to_minute,
                "teams": summary,
            }

        args = {"metric": metric.value, "from_minute": from_minute, "to_minute": to_minute}
        return self._record("get_window", args, run)

    def compare_periods(
        self, metric: MetricName, a_from: Minute, a_to: Minute, b_from: Minute, b_to: Minute
    ) -> dict[str, Any]:
        """Mean of a metric in period A versus period B, per team, with the change."""
        metric = self._validate("compare_periods", {"metric": metric}, lambda: _metric(metric))

        def run() -> dict[str, Any]:
            def means(lo: float, hi: float) -> dict[str, float]:
                rows = [self._metric_row(r, metric) for r in self._series(lo, hi)]
                return {
                    k: _r(fmean(v), 3) for k in rows[0] if (v := [r[k] for r in rows if r[k] is not None])
                }

            a, b = means(a_from, a_to), means(b_from, b_to)
            change = {k: _r(b[k] - a[k], 3) for k in a if k in b}
            return {
                "metric": metric.value,
                "period_a": {"minutes": [a_from, a_to], "mean": a},
                "period_b": {"minutes": [b_from, b_to], "mean": b},
                "change": change,
            }

        args = {"metric": metric.value, "a_from": a_from, "a_to": a_to, "b_from": b_from, "b_to": b_to}
        return self._record("compare_periods", args, run)

    def win_prob_at(self, minute: Minute) -> dict[str, Any]:
        """Win probability and score at a given minute of the match so far."""

        def run() -> dict[str, Any]:
            series = self.state.series
            if not series:
                raise ToolError("no data yet")
            row = min(series, key=lambda r: abs(r["clock_ms"] - minute * 60_000))
            home, away = self._names()
            return {
                "minute": minute_label(row["clock_ms"], row["period"]),
                "score": f"{home} {row['score'][0]}-{row['score'][1]} {away}",
                "win_probability": self._metric_row(row, Metric.WIN_PROBABILITY),
            }

        return self._record("win_prob_at", {"minute": minute}, run)

    def what_if(self, home_goals: int = 0, away_goals: int = 0) -> dict[str, Any]:
        """Counterfactual: win probability now if each team had this many extra (or fewer) goals."""

        def run() -> dict[str, Any]:
            s = self.state
            score = (s.score[0] + home_goals, s.score[1] + away_goals)
            if min(score) < 0:
                raise ToolError("a team cannot have fewer than 0 goals")
            home, away = self._names()
            actual, hypothetical = s.compute_win_probability(), s.compute_win_probability(score=score)

            def wp(w: Any) -> dict[str, float]:
                return {home: round(w.home, 4), "draw": round(w.draw, 4), away: round(w.away, 4)}

            return {
                **self._now(),
                "actual": {
                    "score": f"{home} {s.score[0]}-{s.score[1]} {away}",
                    "win_probability": wp(actual),
                },
                "what_if": {
                    "score": f"{home} {score[0]}-{score[1]} {away}",
                    "win_probability": wp(hypothetical),
                },
            }

        return self._record("what_if", {"home_goals": home_goals, "away_goals": away_goals}, run)

    def get_player_card(self, player: str) -> dict[str, Any]:
        """Everything about one player in this match: involvement, passing, shooting, defending, running."""

        def run() -> dict[str, Any]:
            pid = self._player(player)
            return self._card(pid)

        return self._record("get_player_card", {"player": player}, run)

    def _card(self, pid: str) -> dict[str, Any]:
        s = self.state
        p = s.players[pid]
        phys = s.physical.player(pid)
        tracked = s.last_frame is not None
        return {
            "player": p.name, "team": s.teams[p.team_id].name, "position": str(p.position),
            "on_pitch": p.on_ms is not None and p.off_ms is None,
            "goals": p.goals, "assists": p.assists, "xg": _r(p.xg), "shots": p.shots,
            "shots_on_target": p.shots_on_target, "max_shot_speed_kmh": _r(p.max_shot_speed_kmh, 1),
            "touches": p.touches, "passes": p.passes,
            "pass_accuracy": _r(p.passes_completed / p.passes, 3) if p.passes else None,
            "avg_pass_difficulty": _r(p.pass_difficulty_sum / p.passes, 1) if p.passes else None,
            "hardest_completed_pass": _r(p.hardest_completed_pass, 1), "key_passes": p.key_passes,
            "progressive_passes": p.progressive_passes, "lines_broken": p.lines_broken,
            "tackles": p.tackles, "interceptions": p.interceptions, "recoveries": p.recoveries,
            "pressures": p.pressures, "fouls": p.fouls,
            # Physical numbers need tracking frames; a match without them reports None, not zero.
            "distance_km": _r(phys.distance_m / 1000, 2) if tracked else None,
            "top_speed_kmh": _r(phys.top_speed_kmh, 1) if tracked else None,
            "sprints": phys.sprints if tracked else None,
        }  # fmt: skip

    def top_players(self, stat: PlayerStat, team: str | None = None, limit: int = 5) -> dict[str, Any]:
        """The leading players for one stat, optionally for one team."""

        def run() -> dict[str, Any]:
            if stat not in PLAYER_STATS:
                raise ToolError(f"unknown stat {stat!r}; use one of {', '.join(PLAYER_STATS)}")
            club = self._team(team) if team else None
            s = self.state
            if stat in PHYSICAL_STATS and s.last_frame is None:
                raise ToolError(f"{stat} needs tracking data, which this match does not have")
            cards = [self._card(pid) for pid, p in s.players.items() if (club is None or p.team_id == club)]
            ranked = sorted((c for c in cards if c.get(stat) is not None), key=lambda c: -c[stat])
            return {
                "stat": stat,
                "team": s.teams[club].name if club else "both",
                "leaders": [
                    {"player": c["player"], "team": c["team"], stat: c[stat]}
                    for c in ranked[: max(1, min(limit, 10))]
                ],
            }

        return self._record("top_players", {"stat": stat, "team": team, "limit": limit}, run)

    def get_goals(self) -> dict[str, Any]:
        """Every goal so far: minute, team, scorer, assist and the score after it."""

        def run() -> dict[str, Any]:
            s = self.state
            home, away = self._names()

            def name(pid: str | None) -> str | None:
                return s.players[pid].name if pid in s.players else None

            return {
                "score": f"{home} {s.score[0]}-{s.score[1]} {away}",
                "goals": [
                    {
                        "minute": minute_label(g.clock_ms, g.period),
                        "team": s.teams[g.team_id].name,
                        "scorer": name(g.scorer_id),
                        "assist": name(g.assist_id),
                        "score_after": f"{home} {g.home_score}-{g.away_score} {away}",
                    }
                    for g in s.goals
                ],
            }

        return self._record("get_goals", {}, run)

    def player_of_the_match(self, limit: int = 3) -> dict[str, Any]:
        """Player-of-the-match ranking: a points score from goals, assists, chance creation, passing,
        defending and running, with the stats behind it."""

        def run() -> dict[str, Any]:
            s = self.state
            tracked = s.last_frame is not None
            home_goals, away_goals = s.score
            leader = s.home_id if home_goals > away_goals else s.away_id if away_goals > home_goals else None
            rows = []
            for pid, p in s.players.items():
                if p.on_ms is None and not p.touches:
                    continue  # unused substitute
                stats = {
                    "goals": p.goals, "assists": p.assists, "xg": _r(p.xg), "key_passes": p.key_passes,
                    "progressive_passes": p.progressive_passes, "lines_broken": p.lines_broken,
                    "tackles": p.tackles, "interceptions": p.interceptions, "recoveries": p.recoveries,
                    "blocks": p.blocks + p.clearances, "yellow_cards": p.yellow_cards,
                    "red_cards": int(p.sent_off), "on_winning_side": int(p.team_id == leader),
                }  # fmt: skip
                if tracked:
                    stats["distance_km"] = _r(s.physical.player(pid).distance_m / 1000, 2)
                points = sum(POTM_WEIGHTS[k] * float(v or 0) for k, v in stats.items())
                rows.append({
                    "player": p.name, "team": s.teams[p.team_id].name, "points": round(points, 1),
                    "stats": {k: v for k, v in stats.items() if v},
                })  # fmt: skip
            rows.sort(key=lambda r: -r["points"])
            return {"method": POTM_METHOD, "ranking": rows[: max(1, min(limit, 5))]}

        return self._record("player_of_the_match", {"limit": limit}, run)

    def get_insights(
        self,
        insight_type: InsightTypeName = None,
        team: str | None = None,
        since_minute: Minute | None = None,
        limit: int = 6,
    ) -> dict[str, Any]:
        """Detected key moments (goals, momentum shifts, pressure changes, ...) with their evidence,
        most important first."""
        if insight_type is not None:
            insight_type = self._validate(
                "get_insights", {"insight_type": insight_type}, lambda: _insight_type(insight_type)
            )

        def run() -> dict[str, Any]:
            club = self._team(team) if team else None
            items = [
                i for i in self._view().insights
                if (insight_type is None or i.type == insight_type)
                and (club is None or club in i.entities.teams)
                and (since_minute is None or i.clock_ms >= since_minute * 60_000)
            ]  # fmt: skip
            items.sort(key=lambda i: -i.salience)
            # Compact rows: free-tier models re-read every tool result on each step of a run.
            return {
                "count": len(items),
                "insights": [self.insight_row(i) for i in items[: max(1, min(limit, 15))]],
            }

        args = {
            "insight_type": insight_type and insight_type.value,
            "team": team,
            "since_minute": since_minute,
            "limit": limit,
        }
        return self._record("get_insights", args, run)

    def insight_row(self, i: Insight) -> dict[str, Any]:
        """One insight as the agents see it: minute, headline key, who, and compact evidence."""
        s = self.state
        return {
            "minute": minute_label(i.clock_ms, i.period),
            "headline": i.headline_key,
            "who": ", ".join(
                [s.players[p].name for p in i.entities.players if p in s.players]
                + [s.teams[t].name for t in i.entities.teams if t in s.teams]
            ),
            "evidence": "; ".join(self._evidence(e) for e in i.evidence),
        }

    def _evidence(self, e: Any) -> str:
        s = self.state
        who = " ".join(
            x for x in (
                s.teams[e.team].name if e.team in s.teams else e.team,
                s.players[e.player].name if e.player in s.players else e.player,
            ) if x
        )  # fmt: skip
        if e.from_ is not None and e.to is not None:
            value = f"{e.from_:g}→{e.to:g}"
        else:
            value = f"{e.value:g}" if isinstance(e.value, float) else str(e.value)
        unit = f" {e.unit}" if e.unit else ""
        window = f" (last {e.window_min:g} min)" if e.window_min else ""
        return f"{e.metric}{' ' + who if who else ''} {value}{unit}{window}"

    # ------------------------------------------------------------------ registry
    def functions(self) -> list[Callable[..., Any]]:
        """The tool callables, in the order agents see them."""
        return [
            self.get_match_state, self.get_metric, self.get_window, self.compare_periods, self.win_prob_at,
            self.what_if, self.get_player_card, self.top_players, self.get_insights, self.get_goals,
            self.player_of_the_match,
        ]  # fmt: skip
