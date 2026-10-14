"""Event bus abstraction (§3): the in-memory bus for local runs, Azure Event Hubs in the cloud (T-71).

Messages are published per match. Consumers apply `event` messages in order to rebuild state; a `reset`
control message means "drop your state, a fresh ordered stream follows" (sent before a seek).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field

from pitchlens.schema.common import Model
from pitchlens.schema.events import MatchEvent, MatchMeta, TrackingFrame


class MessageKind(StrEnum):
    META = "meta"
    EVENT = "event"
    FRAME = "frame"
    CONTROL = "control"


class BusMessage(Model):
    kind: MessageKind
    match_id: str
    match_ms: int = Field(ge=0)
    catch_up: bool = Field(
        default=False, description="Replayed instantly while seeking; skip live-only work."
    )
    meta: MatchMeta | None = None
    event: MatchEvent | None = None
    frame: TrackingFrame | None = None
    frame_json: str | None = Field(
        default=None,
        exclude=True,  # in-process only: never part of a serialised bus message
        description="`frame` as compact JSON text, when the producer already has it (the Rust core).",
    )
    control: dict[str, Any] | None = None


class Bus(Protocol):
    async def publish(self, message: BusMessage) -> None: ...

    def subscribe(self, match_id: str) -> AsyncIterator[BusMessage]: ...

    async def close(self) -> None: ...
