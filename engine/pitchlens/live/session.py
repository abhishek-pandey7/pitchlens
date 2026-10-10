"""One live match: consume the bus, run interpret -> explain, fan the results out to stream subscribers.

Every outgoing message except tracking frames gets a sequential id and is kept in a bounded log, so an
SSE client that reconnects with `Last-Event-ID` receives exactly what it missed. While the replay is
seeking (catch-up messages) nothing is broadcast; when live play resumes, subscribers receive a single
`sync` with the full current picture instead of thousands of individual messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pitchlens.explain.engine import ExplainEngine
from pitchlens.ingest.bus import Bus, BusMessage, MessageKind
from pitchlens.interpret.state import MatchState
from pitchlens.schema.events import MatchMeta
from pitchlens.telemetry import LatencyRecorder

LOG_SIZE = 5000
STATE_EVERY_S = 0.5  # wall-clock throttle for `state` snapshots
FRAMES_PER_S = 25  # tracking frames forwarded to clients (all frames still feed the physical metrics)
SUBSCRIBER_QUEUE_LIMIT = 20_000  # a client this far behind is disconnected


@dataclass
class Outgoing:
    id: int | None  # None for ephemeral messages (tracking frames)
    event: str
    data: str  # JSON


@dataclass(eq=False)  # each subscriber is a distinct connection: identity, hashable
class Subscriber:
    profile_id: str | None = None
    frames: bool = False
    queue: asyncio.Queue[Outgoing | None] = field(default_factory=asyncio.Queue)
    dropped: bool = False


class MatchSession:
    def __init__(self, meta: MatchMeta, bus: Bus) -> None:
        self.meta = meta
        self.bus = bus
        self.latency = LatencyRecorder()
        self.state = MatchState(meta)
        self.engine = ExplainEngine(self.state)
        self.subscribers: set[Subscriber] = set()
        self._log: deque[Outgoing] = deque(maxlen=LOG_SIZE)
        self._next_id = 1
        self._catching_up = False
        self._last_state_wall = 0.0
        self._last_frame_wall = 0.0
        self._task: asyncio.Task[None] | None = None
        self.messages_in = 0

    @property
    def match_id(self) -> str:
        return self.meta.match_id

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None or self._task.done():
            subscription = self.bus.subscribe(self.match_id)  # register before any replay can publish
            self._task = asyncio.create_task(self._consume(subscription), name=f"session:{self.match_id}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        for sub in list(self.subscribers):
            sub.queue.put_nowait(None)

    async def _consume(self, subscription: Any) -> None:
        async for message in subscription:
            self.handle(message)

    def reset(self) -> None:
        self.state = MatchState(self.meta)
        self.engine = ExplainEngine(self.state)

    # ------------------------------------------------------------------ pipeline
    def handle(self, message: BusMessage) -> None:
        """Apply one bus message. Synchronous and fast (~tens of microseconds per event)."""
        self.messages_in += 1
        if message.kind is MessageKind.META:
            self.reset()
            self.broadcast("reset", {"reason": "start", "match_id": self.match_id})
            return
        if message.kind is MessageKind.CONTROL:
            action = (message.control or {}).get("action")
            if action == "reset":
                self.reset()
                self._catching_up = True
                self.broadcast("reset", {"reason": "seek", "to_ms": (message.control or {}).get("to_ms")})
            else:
                self._end_catch_up()
                self.broadcast("control", message.control or {})
            return
        if not message.catch_up:
            self._end_catch_up()

        if message.kind is MessageKind.EVENT and message.event is not None:
            start = time.perf_counter()
            with self.latency.stage("interpret"):
                enriched = self.state.apply(message.event)
            with self.latency.stage("explain"):
                insights = self.engine.on_event(enriched)
            if not message.catch_up:
                with self.latency.stage("broadcast"):
                    self.broadcast(
                        "event", enriched.model_dump(mode="json", by_alias=True, exclude_none=True)
                    )
                    for insight in insights:
                        self.broadcast("insight", insight.model_dump(mode="json", by_alias=True))
                    self._maybe_state()
                self.latency.record("event_to_broadcast", (time.perf_counter() - start) * 1000)
        elif message.kind is MessageKind.FRAME and message.frame is not None:
            with self.latency.stage("physical"):
                alerts = self.state.apply_frame(message.frame)
                insights = [i for a in alerts for i in self.engine.on_alert(a)]
            if not message.catch_up:
                for alert in alerts:
                    self.broadcast("alert", alert.model_dump(mode="json"))
                for insight in insights:
                    self.broadcast("insight", insight.model_dump(mode="json", by_alias=True))
                now = time.monotonic()
                if now - self._last_frame_wall >= 1 / FRAMES_PER_S:
                    self._last_frame_wall = now
                    self.broadcast("frame", message.frame.model_dump(mode="json"), ephemeral=True)

    def _end_catch_up(self) -> None:
        if self._catching_up:
            self._catching_up = False
            self.broadcast("sync", self.sync_payload())

    def _maybe_state(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_state_wall >= STATE_EVERY_S or self.state.finished:
            self._last_state_wall = now
            self.broadcast("state", self.state.snapshot())

    # ------------------------------------------------------------------ fan-out
    def broadcast(self, event: str, payload: dict[str, Any], *, ephemeral: bool = False) -> None:
        data = json.dumps(payload, separators=(",", ":"))
        if ephemeral:
            out = Outgoing(None, event, data)
        else:
            out = Outgoing(self._next_id, event, data)
            self._next_id += 1
            self._log.append(out)
        for sub in list(self.subscribers):
            if ephemeral and not sub.frames:
                continue
            if sub.queue.qsize() > SUBSCRIBER_QUEUE_LIMIT:
                sub.dropped = True
                self.subscribers.discard(sub)
                sub.queue.put_nowait(None)
                continue
            sub.queue.put_nowait(out)

    def sync_payload(self) -> dict[str, Any]:
        return {
            "meta": self.meta.model_dump(mode="json"),
            "state": self.state.snapshot(),
            "insights": [i.model_dump(mode="json", by_alias=True) for i in self.engine.insights],
        }

    def subscribe(
        self, *, profile_id: str | None = None, frames: bool = False, last_event_id: int | None = None
    ) -> Subscriber:
        sub = Subscriber(profile_id=profile_id, frames=frames)
        oldest = self._log[0].id if self._log else self._next_id
        if last_event_id is not None and oldest is not None and last_event_id >= oldest - 1:
            for out in self._log:
                if out.id is not None and out.id > last_event_id:
                    sub.queue.put_nowait(out)
        else:
            sub.queue.put_nowait(
                Outgoing(None, "sync", json.dumps(self.sync_payload(), separators=(",", ":")))
            )
        self.subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self.subscribers.discard(sub)
