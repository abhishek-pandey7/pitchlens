"""Futures: the rest of the match simulated many times (docs/rust-expansion-plan.md §3).

`FuturesInput` is the match as the simulation sees it (also what the browser's kernel takes for a
what-if); `Futures` is one run's result. Probabilities are fractions in 0..1.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import SCHEMA_VERSION, Model

MAX_FUTURES_PATHS = 1_000_000  # per request; the kernel itself allows more


class FuturesEffects(Model):
    """Which effects a run models. All off reproduces the closed-form win probability."""

    game_state: bool = Field(default=True, description="Trailing sides push, leading sides sit deeper.")
    momentum: bool = Field(default=True, description="Momentum and field tilt lift the side on top, fading.")
    discipline: bool = Field(default=True, description="Further red cards at each side's card/foul rate.")


class FuturesInput(Model):
    """Home first in every pair."""

    score: list[int] = Field(min_length=2, max_length=2, description="Goals so far.")
    minute: float = Field(ge=0, le=200, description="Clock minute the simulation starts from.")
    minutes_left: float = Field(ge=0, le=200, description="Including expected added time.")
    rate_90: list[float] = Field(
        min_length=2, max_length=2, description="Scoring rates per 90 minutes before red cards."
    )
    reds: list[int] = Field(min_length=2, max_length=2, description="Red cards so far.")
    momentum: float = Field(ge=-1, le=1, description="Home minus away decayed threat.")
    field_tilt: float = Field(ge=0, le=1, description="Home share of final-third touches.")
    red_rate_90: list[float] = Field(
        min_length=2, max_length=2, description="Red cards per 90 minutes from here, per side."
    )
    effects: FuturesEffects = Field(default_factory=FuturesEffects)
    paths: int = Field(ge=1, le=MAX_FUTURES_PATHS)
    seed: int = Field(ge=0, lt=2**53, description="From the match id and the latest event's seq.")


class ScoreChance(Model):
    home: int
    away: int
    p: float


class Comeback(Model):
    """The side behind now: how often it avoids defeat, and how often it wins."""

    team: str = Field(description="Club id of the side behind.")
    side: Literal["home", "away"]
    avoid_defeat: float
    win: float


class NextGoal(Model):
    home: float
    away: float
    none: float = Field(description="No more goals.")
    median_minute: float | None = Field(description="Median clock minute of the next goal.")


class Futures(Model):
    schema_version: str = SCHEMA_VERSION
    match_id: str
    match_ms: int = Field(ge=0)
    clock_ms: int = Field(ge=0)
    period: int = Field(ge=1, le=4)
    score: list[int] = Field(min_length=2, max_length=2, description="Score when the run started.")
    backend: Literal["rust", "python"]
    hypothetical: bool = Field(default=False, description="A what-if, not the match as it stands.")
    paths: int
    home: float
    draw: float
    away: float
    scores: list[ScoreChance] = Field(description="The most likely final scores, most likely first.")
    other: float = Field(description="Every other final score together.")
    comeback: Comeback | None = Field(description="None when the score is level.")
    next_goal: NextGoal
    goals_left: list[float] = Field(min_length=2, max_length=2, description="Expected goals still to come.")
    ms: float = Field(description="Simulation time.")
    input: FuturesInput | None = Field(default=None, description="The run's input; null in state messages.")

    def win(self, is_home: bool) -> float:
        return self.home if is_home else self.away
