"""One live match: consume the bus, run interpret -> explain -> personalize -> narrate -> cues, and
stream the result to every fan.

Each fan profile gets a **channel** with its own numbered log, so an SSE client that reconnects with
`Last-Event-ID` receives exactly what it missed. Connections without a profile use the raw channel
(every insight, analyst wording, no cues). Shared messages (events, state, alerts, frames) go to every
channel; insights are ranked per fan and carry instant template text, cues pass the fan's scheduler,
and LLM narrations follow asynchronously as `narration` messages.

While the replay is seeking (catch-up) nothing is streamed; when live play resumes each channel
rebuilds its feed from the full insight list and receives one `sync` with the current picture.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

from pitchlens.explain.engine import ExplainEngine
from pitchlens.ingest.bus import Bus, BusMessage, MessageKind
from pitchlens.interpret.state import MatchState
from pitchlens.narrate.templates import NarrationContext, render
from pitchlens.narrate.worker import NarrationWorker
from pitchlens.personalize.profiles import ProfileStore
from pitchlens.personalize.ranker import rank
from pitchlens.render.cues import CueScheduler, build_cue
from pitchlens.schema.events import MatchMeta
from pitchlens.schema.insights import Insight
from pitchlens.schema.profiles import Persona, Profile
from pitchlens.telemetry import LatencyRecorder

LOG_SIZE = 5000
STATE_EVERY_S = 0.5  # wall-clock throttle for `state` snapshots
FRAMES_PER_S = 25  # tracking frames forwarded to clients (all frames still feed the physical metrics)
SUBSCRIBER_QUEUE_LIMIT = 20_000  # a client this far behind is disconnected
RAW = "*"
RAW_PROFILE = Profile(profile_id="raw", persona=Persona.ANALYST, language="en", max_overlays_per_min=0)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


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


class Channel:
    """One fan's personalised stream (or the raw stream)."""

    def __init__(self, profile: Profile | None) -> None:
        self.profile = profile or RAW_PROFILE
        self.raw = profile is None
        self.subscribers: set[Subscriber] = set()
        self.log: deque[Outgoing] = deque(maxlen=LOG_SIZE)
        self.next_id = 1
        self.delivered: list[dict[str, Any]] = []
        self.scheduler = CueScheduler(self.profile.max_overlays_per_min)
        self.stats: Counter[str] = Counter()

    def reset(self) -> None:
        self.delivered = []
        self.scheduler = CueScheduler(self.profile.max_overlays_per_min)

    def send(self, event: str, data: str, *, ephemeral: bool = False) -> None:
        if ephemeral:
            out = Outgoing(None, event, data)
        else:
            out = Outgoing(self.next_id, event, data)
            self.next_id += 1
            self.log.append(out)
            self.stats[event] += 1
        for sub in list(self.subscribers):
            if ephemeral and not sub.frames:
                continue
            if sub.queue.qsize() > SUBSCRIBER_QUEUE_LIMIT:
                sub.dropped = True
                self.subscribers.discard(sub)
                sub.queue.put_nowait(None)
                continue
            sub.queue.put_nowait(out)

    def personalise(self, insight: Insight, ctx: NarrationContext) -> tuple[dict[str, Any] | None, Any]:
        """The feed item for this fan (None if it doesn't make their feed) and the ranking decision."""
        if self.raw:
            decision = None
            salience, reasons, spoiler = insight.salience, [], False
        else:
            decision = rank(insight, self.profile)
            if not decision.feed:
                return None, decision
            salience, reasons, spoiler = decision.personal_salience, decision.reasons, decision.spoiler
        text = render(insight, ctx, self.profile)
        item = {
            "insight": insight.model_dump(mode="json", by_alias=True),
            "personal_salience": salience,
            "reasons": reasons,
            "spoiler": spoiler,
            "narration": {
                "text": text.text,
                "headline": text.headline,
                "body": text.body,
                "lang": self.profile.language,
                "source": "template",
            },
        }
        return item, decision


class MatchSession:
    def __init__(
        self,
        meta: MatchMeta,
        bus: Bus,
        *,
        profiles: ProfileStore | None = None,
        worker: NarrationWorker | None = None,
    ) -> None:
        self.meta = meta
        self.bus = bus
        self.profiles = profiles or ProfileStore()
        self.worker = worker
        self.latency = LatencyRecorder()
        self.state = MatchState(meta)
        self.engine = ExplainEngine(self.state)
        self.channels: dict[str, Channel] = {RAW: Channel(None)}
        self._catching_up = False
        self._last_state_wall = 0.0
        self._last_frame_wall = 0.0
        self._task: asyncio.Task[None] | None = None
        self._narrations: set[asyncio.Task[None]] = set()
        self._finishing: asyncio.Future[None] | None = None
        self.messages_in = 0

    @property
    def match_id(self) -> str:
        return self.meta.match_id

    @property
    def subscribers(self) -> set[Subscriber]:
        return {s for ch in self.channels.values() for s in ch.subscribers}

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None or self._task.done():
            subscription = self.bus.subscribe(self.match_id)  # register before any replay can publish
            self._task = asyncio.create_task(self._consume(subscription), name=f"session:{self.match_id}")

    async def stop(self) -> None:
        for task in [self._task, *self._narrations]:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        for sub in self.subscribers:
            sub.queue.put_nowait(None)

    async def drain_narrations(self) -> None:
        """Wait for in-flight LLM narrations (used by tests and graceful shutdown)."""
        while self._narrations:
            await asyncio.gather(*list(self._narrations), return_exceptions=True)

    async def _consume(self, subscription: Any) -> None:
        async for message in subscription:
            self.handle(message)
            await asyncio.sleep(0)  # let SSE writers and narration tasks interleave with a burst

    def reset(self) -> None:
        self.state = MatchState(self.meta)
        self.engine = ExplainEngine(self.state)
        for task in self._narrations:
            task.cancel()
        for channel in self.channels.values():
            channel.reset()

    # ------------------------------------------------------------------ pipeline
    def handle(self, message: BusMessage) -> None:
        """Apply one bus message. Synchronous and fast; LLM work is scheduled, never awaited here."""
        self.messages_in += 1
        if message.kind is MessageKind.META:
            self.reset()
            self.broadcast("reset", {"reason": "start", "match_id": self.match_id})
            return
        if message.kind is MessageKind.CONTROL:
            control = message.control or {}
            if control.get("action") == "reset":
                self.reset()
                self._catching_up = True
                self.broadcast("reset", {"reason": "seek", "to_ms": control.get("to_ms")})
            else:
                self._end_catch_up()
                if control.get("action") == "finished":
                    # "finished" means nothing more is coming: let in-flight narrations land first.
                    self._finishing = asyncio.ensure_future(self._finish(control))
                else:
                    self.broadcast("control", control)
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
                with self.latency.stage("personalize"):
                    for insight in insights:
                        self.publish_insight(insight)
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
                    self.publish_insight(insight)
                now = time.monotonic()
                if now - self._last_frame_wall >= 1 / FRAMES_PER_S:
                    self._last_frame_wall = now
                    self.broadcast("frame", message.frame.model_dump(mode="json"), ephemeral=True)

    def publish_insight(self, insight: Insight) -> None:
        ctx = NarrationContext.from_state(self.state)
        for channel in self.channels.values():
            item, decision = channel.personalise(insight, ctx)
            if item is None:
                channel.stats["filtered"] += 1
                continue
            channel.delivered.append(item)
            channel.send("insight", _json(item))
            if decision is not None and decision.overlay:
                cue = build_cue(
                    insight,
                    channel.profile,
                    headline=item["narration"]["headline"],
                    body=item["narration"]["body"],
                )
                if channel.scheduler.admit(cue):
                    channel.send("cue", _json(cue.model_dump(mode="json", by_alias=True)))
                else:
                    channel.stats["cue_dropped"] += 1
            if self.worker is not None and decision is not None:
                task = asyncio.ensure_future(self._narrate(channel, insight, ctx, decision.personal_salience))
                self._narrations.add(task)
                task.add_done_callback(self._narrations.discard)

    async def _narrate(
        self, channel: Channel, insight: Insight, ctx: NarrationContext, salience: float
    ) -> None:
        assert self.worker is not None
        start = time.perf_counter()
        narration = await self.worker.narrate(insight, channel.profile, ctx, personal_salience=salience)
        if narration is None or narration.source != "llm":
            channel.stats["narration_template"] += 1
            return
        self.latency.record("narration", (time.perf_counter() - start) * 1000)
        for item in channel.delivered:
            if item["insight"]["insight_id"] == insight.insight_id:
                item["narration"] = {
                    "text": narration.text, "headline": narration.text, "body": "",
                    "lang": narration.lang, "source": "llm",
                }  # fmt: skip
        channel.send("narration", _json(narration.model_dump(mode="json")))

    async def _finish(self, control: dict[str, Any]) -> None:
        await self.drain_narrations()
        self.broadcast("control", control)

    def _end_catch_up(self) -> None:
        if not self._catching_up:
            return
        self._catching_up = False
        ctx = NarrationContext.from_state(self.state)
        for channel in self.channels.values():
            channel.delivered = [
                item for i in self.engine.insights if (item := channel.personalise(i, ctx)[0])
            ]
            channel.send("sync", _json(self.sync_payload(channel)))

    def _maybe_state(self) -> None:
        now = time.monotonic()
        if now - self._last_state_wall >= STATE_EVERY_S or self.state.finished:
            self._last_state_wall = now
            self.broadcast("state", self.state.snapshot())

    # ------------------------------------------------------------------ fan-out
    def broadcast(self, event: str, payload: dict[str, Any], *, ephemeral: bool = False) -> None:
        data = _json(payload)
        for channel in self.channels.values():
            channel.send(event, data, ephemeral=ephemeral)

    def sync_payload(self, channel: Channel) -> dict[str, Any]:
        return {
            "meta": self.meta.model_dump(mode="json"),
            "profile": None if channel.raw else channel.profile.model_dump(mode="json"),
            "state": self.state.snapshot(),
            "insights": channel.delivered,
        }

    def channel(self, profile_id: str | None) -> Channel:
        if profile_id is None:
            return self.channels[RAW]
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        channel = self.channels.get(profile_id)
        if channel is None:
            channel = self.channels[profile_id] = Channel(profile)
            ctx = NarrationContext.from_state(self.state)
            channel.delivered = [
                item for i in self.engine.insights if (item := channel.personalise(i, ctx)[0])
            ]
        else:
            channel.profile = profile  # pick up profile edits
        return channel

    def subscribe(
        self, *, profile_id: str | None = None, frames: bool = False, last_event_id: int | None = None
    ) -> Subscriber:
        channel = self.channel(profile_id)
        sub = Subscriber(profile_id=profile_id, frames=frames)
        oldest = channel.log[0].id if channel.log else channel.next_id
        if last_event_id is not None and oldest is not None and last_event_id >= oldest - 1:
            for out in channel.log:
                if out.id is not None and out.id > last_event_id:
                    sub.queue.put_nowait(out)
        else:
            sub.queue.put_nowait(Outgoing(None, "sync", _json(self.sync_payload(channel))))
        channel.subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        for channel in self.channels.values():
            channel.subscribers.discard(sub)
