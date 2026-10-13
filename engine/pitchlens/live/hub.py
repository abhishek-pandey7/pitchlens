"""The hub: owns the bus, the match store, one session per match and their replays."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass

from pitchlens.agents.narration import AgentNarrator, narration_agents_enabled
from pitchlens.ingest.bus import BusMessage, MessageKind
from pitchlens.ingest.memory_bus import MemoryBus
from pitchlens.narrate.mock import MockProvider
from pitchlens.narrate.narrator import Narrator
from pitchlens.narrate.provider import LLMProvider
from pitchlens.narrate.worker import NarrationWorker
from pitchlens.personalize.profiles import ProfileStore
from pitchlens.schema.events import MatchEvent, MatchMeta
from pitchlens.sim.replay import Replay

from .session import MatchSession
from .store import MatchRecord, MatchStore

REPLAY_BATCH_S = 0.05  # publish in ~50 ms bursts: imperceptible to viewers, far fewer loop wake-ups


class HubError(LookupError):
    pass


@dataclass
class Live:
    session: MatchSession
    replay: Replay | None = None
    task: asyncio.Task[None] | None = None


class Hub:
    def __init__(
        self,
        store: MatchStore,
        bus: MemoryBus | None = None,
        *,
        profiles: ProfileStore | None = None,
        provider: LLMProvider | None = None,
        narrate: bool = True,
    ) -> None:
        self.store = store
        self.bus = bus or MemoryBus()
        self.profiles = profiles or ProfileStore()
        self.provider = provider or MockProvider()
        self.narrate = narrate
        self._live: dict[str, Live] = {}
        self.session_hooks: list[Callable[[MatchSession], None]] = []  # e.g. agents attaching observers

    def record(self, match_id: str) -> MatchRecord:
        record = self.store.get(match_id)
        if record is None:
            raise HubError(f"unknown match {match_id!r}")
        return record

    def live_session(self, match_id: str) -> MatchSession | None:
        """The match's session if one is running, without starting one (read-only callers)."""
        live = self._live.get(match_id)
        return live.session if live else None

    def session(self, match_id: str) -> MatchSession:
        """The match's live session, created (and started) on first use."""
        live = self._live.get(match_id)
        if live is None:
            record = self.record(match_id)
            worker = NarrationWorker(self._narrator()) if self.narrate else None
            session = MatchSession(record.meta, self.bus, profiles=self.profiles, worker=worker)
            live = self._live[match_id] = Live(session)
            for hook in self.session_hooks:
                hook(session)
            live.session.start()
        return live.session

    def _narrator(self) -> Narrator | AgentNarrator:
        """Narrator and Localization agents with the concurrent fan-out (T-123) when the agent clients
        support the provider; the direct path otherwise (Claude on Foundry). Both keep the template
        fallback and the grounding validator."""
        if narration_agents_enabled(getattr(self.provider, "name", None)):
            return AgentNarrator()
        return Narrator(self.provider)

    def replay_state(self, match_id: str) -> dict[str, object]:
        live = self._live.get(match_id)
        if live is None or live.replay is None:
            return {"state": "idle"}
        replay = live.replay
        return {
            "state": replay.state.value,
            "speed": replay.speed,
            "match_ms": replay.match_ms,  # where the replay clock says the match is
            "position_ms": replay.position_ms,  # last item actually published
            "published": replay.published,
            "session_ms": live.session.state.match_ms,  # last item the pipeline has processed
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
        # Stored samples: players and ball from the cache, else built in the background for the next replay.
        if frames and not await asyncio.to_thread(self.store.load_cached_tracking, record):
            self.store.request_tracking(record)
        tracking = record.tracking if frames else None
        replay = Replay(
            self.bus,
            record.meta,
            record.events,
            tracking,
            speed=speed,
            thin_frames=False,
            batch_s=REPLAY_BATCH_S,
        )
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
