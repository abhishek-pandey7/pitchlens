"""Request bodies for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from pitchlens.schema.events import MatchMeta


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


class ExternalMatch(BaseModel):
    meta: MatchMeta


class IngestBatch(BaseModel):
    match_id: str
    events: list[dict[str, Any]] = Field(max_length=5000)
