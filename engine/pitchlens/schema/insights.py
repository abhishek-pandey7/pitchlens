"""Insights: the explain stage's output. Every number an insight carries lives in `evidence`."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .common import SCHEMA_VERSION, Model


class InsightType(StrEnum):
    GOAL = "goal"
    BIG_CHANCE = "big_chance"
    XG_SWING = "xg_swing"
    WIN_PROB_SWING = "win_prob_swing"
    MOMENTUM_SHIFT = "momentum_shift"
    PRESSURE_CHANGE = "pressure_change"
    CONTROL_CHAOS_FLIP = "control_chaos_flip"
    RHYTHM_BREAK = "rhythm_break"
    PASS_OF_NOTE = "pass_of_note"
    SHOT_SPEED_RECORD = "shot_speed_record"
    SPRINT_THRESHOLD = "sprint_threshold"
    DISTANCE_MILESTONE = "distance_milestone"
    CAREER_MILESTONE = "career_milestone"
    PLAYER_STREAK = "player_streak"
    TACTICAL_NOTE = "tactical_note"
    FATIGUE_ALERT = "fatigue_alert"
    DUEL = "duel"
    CARD = "card"


class Evidence(Model):
    """One grounded fact. Either a point value or a from→to change."""

    metric: str
    team: str | None = None
    player: str | None = None
    value: float | str | None = None
    from_: float | None = Field(default=None, alias="from")
    to: float | None = None
    unit: str | None = None
    window_min: float | None = None
    source: str = Field(default="engine", description="engine | agent:<name>")


class Entities(Model):
    teams: list[str] = Field(default_factory=list)
    players: list[str] = Field(default_factory=list)


class Insight(Model):
    schema_version: str = SCHEMA_VERSION
    insight_id: str
    match_id: str
    match_ms: int = Field(ge=0)
    clock_ms: int = Field(ge=0, description="Football clock, for display (62', 90+3').")
    period: int = Field(ge=1, le=4)
    type: InsightType
    salience: float = Field(ge=0.0, le=1.0)
    entities: Entities = Field(default_factory=Entities)
    headline_key: str = Field(description="Template key, e.g. 'momentum_shift.press_intensified'.")
    evidence: list[Evidence] = Field(min_length=1)
    why_tags: list[str] = Field(default_factory=list)
    related_events: list[str] = Field(default_factory=list)
    author: str = Field(default="engine", description="engine | agent:<name>")
