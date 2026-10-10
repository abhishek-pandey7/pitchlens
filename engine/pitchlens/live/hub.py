"""The hub: owns the bus, the match store, one session per match and their replays."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from pitchlens.ingest.bus import BusMessage, MessageKind
from pitchlens.ingest.memory_bus import MemoryBus
from pitchlens.schema.events import MatchEvent, MatchMeta
from pitchlens.sim.replay import Replay

from .session import MatchSession
from .store import MatchRecord, MatchStore


class HubError(LookupError):
    pass


@dataclass
class Live:
    session: MatchSession
    replay: Replay | None = None
    task: asyncio.Task[None] | None = None


class Hub:
    def __init__(self, store: MatchStore, bus: MemoryBus | None = None) -> None:
        self.store = store
        self.bus = bus or MemoryBus()
        self._live: dict[str, Live] = {}

    def record(self, match_id: str) -> MatchRecord:
        record = self.store.get(match_id)
        if record is None:
            raise HubError(f"unknown match {match_id!r}")
        return record

    def session(self, match_id: str) -> MatchSession:
        """The match's live session, created (and started) on first use."""
        live = self._live.get(match_id)
        if live is None:
            record = self.record(match_id)
            live = self._live[match_id] = Live(MatchSession(record.meta, self.bus))
            live.session.start()
        return live.session

    def replay_state(self, match_id: str) -> dict[str, object]:
        live = self._live.get(match_id)
        if live is None or live.replay is None:
            return {"state": "idle"}
        return {
            "state": live.replay.state.value,
            "speed": live.replay.speed,
            "match_ms": live.replay.match_ms,
        }

    async def start_replay(
        self, match_id: str, *, speed: float, from_ms: int = 0, frames: bool = True
    ) -> Replay:
        record = self.record(match_id)
        if not record.events:
            raise HubError(f"match {match_id!r} has no stored events to replay")
        self.session(match_id)  # subscribed before the replay publishes anything
        live = self._live[match_id]
        await self._stop_replay(live)
        tracking = record.tracking if frames else None
        replay = Replay(self.bus, record.meta, record.events, tracking, speed=speed, thin_frames=False)
        live.replay = replay
        live.task = asyncio.create_task(replay.run(from_ms=from_ms), name=f"replay:{match_id}")
        return replay

    def control(
        self, match_id: str, action: str, *, to_ms: int | None = None, speed: float | None = None
    ) -> None:
        live = self._live.get(match_id)
        if live is None or live.replay is None:
            raise HubError(f"match {match_id!r} is not being replayed")
        replay = live.replay
        if action == "pause":
            replay.pause()
        elif action == "resume":
            replay.resume()
        elif action == "seek" and to_ms is not None:
            replay.seek(to_ms)
        elif action == "speed" and speed:
            replay.set_speed(speed)
        else:
            raise ValueError(f"bad control action {action!r}")

    def register_external(self, meta: MatchMeta) -> MatchRecord:
        """A match fed live from outside (POST /ingest), e.g. a CV pipeline or a partner feed."""
        record = self.store.add(MatchRecord(meta=meta, source="external"))
        session = self.session(meta.match_id)
        session.handle(BusMessage(kind=MessageKind.META, match_id=meta.match_id, match_ms=0, meta=meta))
        return record

    async def ingest(self, match_id: str, events: list[MatchEvent]) -> int:
        record = self.record(match_id)
        if record.source != "external":
            raise HubError(f"match {match_id!r} is not an external feed")
        self.session(match_id)
        for e in events:
            record.events.append(e)
            await self.bus.publish(
                BusMessage(kind=MessageKind.EVENT, match_id=match_id, match_ms=e.match_ms, event=e)
            )
        return len(events)

    async def _stop_replay(self, live: Live) -> None:
        if live.task and not live.task.done():
            live.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await live.task
        live.replay, live.task = None, None

    async def shutdown(self) -> None:
        for live in self._live.values():
            await self._stop_replay(live)
            await live.session.stop()
        await self.bus.close()
