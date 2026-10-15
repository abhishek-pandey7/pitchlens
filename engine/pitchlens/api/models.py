"""Request bodies for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from pitchlens.schema.events import MatchMeta
from pitchlens.schema.futures import MAX_FUTURES_PATHS, FuturesEffects


class CreateMatch(BaseModel):
    seed: int = Field(default=42, ge=0)
    storyline: str | None = None
    home: str | None = None
    away: str | None = None
    tracking: bool = True


class ReplayRequest(BaseModel):
    speed: float = Field(default=10.0, gt=0, le=600)
    from_ms: int = Field(default=0, ge=0)
    frames: bool = True


class ControlRequest(BaseModel):
    action: Literal["pause", "resume", "seek", "speed"]
    to_ms: int | None = Field(default=None, ge=0)
    speed: float | None = Field(default=None, gt=0, le=600)


class FuturesWhatIf(BaseModel):
    """A hypothetical for the futures: anything left out stays as the match stands now."""

    home_goals: int | None = Field(default=None, ge=0, le=20)
    away_goals: int | None = Field(default=None, ge=0, le=20)
    minute: float | None = Field(default=None, ge=0, le=120, description="Clock minute to restart from.")
    home_reds: int | None = Field(default=None, ge=0, le=4)
    away_reds: int | None = Field(default=None, ge=0, le=4)
    momentum: float | None = Field(default=None, ge=-1, le=1, description="Home minus away, -1..1.")
    field_tilt: float | None = Field(default=None, ge=0, le=1, description="Home share, 0..1.")
    paths: int | None = Field(default=None, ge=1, le=MAX_FUTURES_PATHS)
    effects: FuturesEffects | None = None


class ExternalMatch(BaseModel):
    meta: MatchMeta


class IngestBatch(BaseModel):
    match_id: str
    events: list[dict[str, Any]] = Field(max_length=5000)
