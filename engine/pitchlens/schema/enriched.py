"""Interpret-stage outputs: events enriched with metrics, and physical threshold alerts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .common import SCHEMA_VERSION, Model
from .events import MatchEvent

MetricValue = float | int | bool | str


class EnrichedEvent(Model):
    schema_version: str = SCHEMA_VERSION
    event: MatchEvent
    metrics: dict[str, MetricValue] = Field(
        default_factory=dict,
        description="Derived values, e.g. pass_difficulty, lines_broken, xg, shot_speed_kmh.",
    )
    home_score: int = Field(ge=0)
    away_score: int = Field(ge=0)
    possession_team: str | None = None


class AlertKind(StrEnum):
    TOP_SPEED = "top_speed"
    DISTANCE = "distance"
    SPRINT_COUNT = "sprint_count"


class PhysicalAlert(Model):
    """A predefined physical threshold reached by a player (drives player-tag overlays)."""

    schema_version: str = SCHEMA_VERSION
    kind: AlertKind
    match_id: str
    match_ms: int = Field(ge=0)
    player_id: str
    team_id: str
    value: float
    threshold: float
    unit: str
