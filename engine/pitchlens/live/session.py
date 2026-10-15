"""One live match: consume the bus, run interpret -> explain -> personalize -> narrate -> cues, and
stream the result to every fan.

Each fan profile gets a **channel** with its own numbered log, so an SSE client that reconnects with
`Last-Event-ID` receives exactly what it missed. Connections without a profile use the raw channel
(every insight, analyst wording, no cues). Shared messages (events, state, alerts, frames) go to every
channel; insights are ranked per fan and carry instant template text, cues pass the fan's scheduler,
and LLM narrations follow asynchronously as `narration` messages.

While the replay is seeking (catch-up) nothing is streamed; when live play resumes each channel
rebuilds its feed from the full insight list and receives one `sync` with the current picture. A seek
replays events only; the tracking frames before the target are applied in bulk from `tracking` (set by
the hub), so physical metrics after a seek match a replay from kick-off.

Futures (interpret/futures.py) run after goals, cards, substitutions and phase changes, after a
momentum or pressing insight, and otherwise every 5 match minutes, in a worker thread so the event loop
never waits. One run at a time: a trigger during a run asks for one more run when it lands. Each result
goes on `state.futures` (and so into the next `state` message, pushed at once), is recorded in the
futures history, and can raise a `futures_swing` insight. Nothing runs while seeking; one run follows
the catch-up.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import Counter, deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pitchlens.explain.engine import ExplainEngine
from pitchlens.ingest.bus import Bus, BusMessage, MessageKind
from pitchlens.interpret.futures import futures_input, simulate, to_futures
from pitchlens.interpret.state import MatchState
from pitchlens.narrate.templates import NarrationContext, render
from pitchlens.narrate.worker import NarrationWorker
from pitchlens.personalize.profiles import ProfileStore
from pitchlens.personalize.ranker import rank
from pitchlens.render.cues import CueScheduler, build_cue
from pitchlens.render.programme import Mode, ProgrammeGate
from pitchlens.schema.events import EventType, MatchEvent, MatchMeta
from pitchlens.schema.futures import FuturesInput
from pitchlens.schema.insights import Insight, InsightType
from pitchlens.schema.overlays import OverlayCue
from pitchlens.schema.profiles import Persona, Profile
from pitchlens.sim.tracking import Tracking
from pitchlens.telemetry import LatencyRecorder

LOG_SIZE = 5000
STATE_EVERY_S = 0.5  # wall-clock throttle for `state` snapshots
# Score-changing and phase events push a state update at once: a goal caption must never sit next to a
# stale score bug, whatever the replay speed.
STATE_NOW = {EventType.GOAL, EventType.OWN_GOAL, EventType.CARD, EventType.PERIOD_START, EventType.PERIOD_END,
             EventType.MATCH_END}  # fmt: skip
FUTURES_EVENTS = {EventType.GOAL, EventType.OWN_GOAL, EventType.CARD, EventType.SUBSTITUTION,
                  EventType.PERIOD_START, EventType.MATCH_END}  # fmt: skip
FUTURES_INSIGHTS = {InsightType.MOMENTUM_SHIFT, InsightType.PRESSURE_CHANGE}
log = logging.getLogger(__name__)
DRAIN_TIMEOUT_S = 60.0  # full time waits this long for background work, then cancels what is left
FUTURES_EVERY_MS = 5 * 60_000  # match time between futures runs when nothing else triggers one
FUTURES_TIMEOUT_S = 30.0  # a run that takes longer is dropped (full time and seeks never wait on it)
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
    events: bool = True  # raw match events (~2,700 per match); fan views and overlays can skip them
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
        self.gate: ProgrammeGate | None = None  # set when this channel is the directed programme

    def reset(self) -> None:
        self.delivered = []
        self.scheduler = CueScheduler(self.profile.max_overlays_per_min)
        if self.gate is not None:
            self.gate.scheduler = self.scheduler
            self.gate.reset()

    def send_cue(self, cue: OverlayCue) -> None:
        self.send("cue", _json(cue.model_dump(mode="json", by_alias=True)))

    def send(self, event: str, data: str, *, ephemeral: bool = False) -> None:
        if ephemeral:
            out = Outgoing(None, event, data)
        else:
            out = Outgoing(self.next_id, event, data)
            self.next_id += 1
            self.log.append(out)
            self.stats[event] += 1
        for sub in list(self.subscribers):
            if (ephemeral and not sub.frames) or (event == "event" and not sub.events):
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
        self.tracking: Tracking | None = None  # the replay's frames, for seek catch-up
        self._frame_pos = 0  # tracking frames before this index are in the state
        self._catch_up_to: int | None = None  # the seek target, while catching up
        self._last_state_wall = 0.0
        self._last_frame_wall = 0.0
        self._task: asyncio.Task[None] | None = None
        self._narrations: set[asyncio.Task[None]] = set()
        self._finishing: asyncio.Future[None] | None = None
        self._futures_task: asyncio.Task[None] | None = None
        self._futures_again = False  # a trigger arrived during a run
        self._futures_due_ms = 0  # the next periodic run, in match time
        self._generation = 0  # bumped by every reset: results from before it are dropped
        self.messages_in = 0
        # Called after each live (not catch-up) event with the insights it produced; agents hook in here.
        self.observers: list[Callable[[MatchSession, list[Insight], MatchEvent | None], None]] = []

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

    async def drain_narrations(self, timeout: float = DRAIN_TIMEOUT_S) -> None:
        """Wait for in-flight background work (narrations, futures, agents) at full time, in tests and at
        shutdown. Work can start more work, so this loops; but never forever: whatever is still running
        after `timeout` is logged with its stack and cancelled, so one stuck task can't hold the session."""
        deadline = time.monotonic() + timeout
        while self._narrations:
            left = deadline - time.monotonic()
            if left <= 0:
                for task in list(self._narrations):
                    stack = "".join(
                        f"  {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}\n"
                        for frame in task.get_stack()
                    )
                    log.warning(
                        "%s: background work still running after %.0f s, cancelled: %s\n%s",
                        self.match_id,
                        timeout,
                        task.get_coro(),
                        stack,
                    )
                    task.cancel()
                await asyncio.wait(list(self._narrations), timeout=5)  # let the cancellations land
                return
            await asyncio.wait(list(self._narrations), timeout=left)

    async def _consume(self, subscription: Any) -> None:
        async for message in subscription:
            self.handle(message)
            await asyncio.sleep(0)  # let SSE writers and narration tasks interleave with a burst

    def reset(self) -> None:
        self.state = MatchState(self.meta)
        self.engine = ExplainEngine(self.state)
        self._frame_pos = 0
        self._catch_up_to = None
        self._generation += 1
        self._futures_again = False
        self._futures_task = None  # cancelled below with the other background work
        self._futures_due_ms = 0
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
                self._catch_up_to = control.get("to_ms")
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
            if message.catch_up:
                self._catch_up_frames(message.event.match_ms, inclusive=False)  # frames come after events
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
                for observe in self.observers:
                    observe(self, insights, message.event)
                self._maybe_futures(message.event, insights)
                self._maybe_state(force=message.event.type in STATE_NOW)
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
                    # The same text either way; the Rust core has already written it.
                    text = message.frame_json or _json(message.frame.model_dump(mode="json"))
                    self.broadcast_text("frame", text, ephemeral=True)

    def publish_insight(self, insight: Insight) -> None:
        ctx = NarrationContext.from_state(self.state)
        to_narrate: list[tuple[Channel, float]] = []
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
                if channel.gate is not None:
                    channel.gate.offer(cue)  # the directed programme: the Director (or producer) decides
                elif channel.scheduler.admit(cue):
                    channel.send_cue(cue)
                else:
                    channel.stats["cue_dropped"] += 1
            if self.worker is not None and decision is not None:
                to_narrate.append((channel, decision.personal_salience))
        if to_narrate:  # one job per insight: every fan group's line is written together (T-123 fan-out)
            task = asyncio.ensure_future(self._narrate(insight, ctx, to_narrate))
            self._narrations.add(task)
            task.add_done_callback(self._narrations.discard)

    def track(self, work: Coroutine[Any, Any, None]) -> None:
        """Run background work for this match (e.g. an agent). A seek cancels it; full time waits for it."""
        task = asyncio.ensure_future(work)
        self._narrations.add(task)
        task.add_done_callback(self._narrations.discard)

    def add_insight(self, insight: Insight) -> None:
        """An insight from outside the rule engine (an agent): recorded with the match, then published."""
        self.engine.insights.append(insight)
        self.publish_insight(insight)

    async def _narrate(
        self, insight: Insight, ctx: NarrationContext, fans: list[tuple[Channel, float]]
    ) -> None:
        assert self.worker is not None
        start = time.perf_counter()
        lines = await self.worker.narrate_many(insight, [(c.profile, salience) for c, salience in fans], ctx)
        elapsed = (time.perf_counter() - start) * 1000
        for channel, _ in fans:
            narration = lines.get(channel.profile.profile_id)
            if narration is None or narration.source != "llm":
                channel.stats["narration_template"] += 1
                continue
            self.latency.record("narration", elapsed)
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

    def _catch_up_frames(self, until_ms: int, *, inclusive: bool) -> None:
        """While seeking: apply the tracking frames before `until_ms` (or at it too, if `inclusive`) in one
        go, as the live pipeline would have applied them one by one (alerts included)."""
        tracking = self.tracking
        if tracking is None:
            return
        end = int(np.searchsorted(tracking.ms, until_ms, side="right" if inclusive else "left"))
        if end <= self._frame_pos:
            return
        with self.latency.stage("catch_up_frames"):
            for alert in self.state.apply_tracking(tracking, self._frame_pos, end):
                self.engine.on_alert(alert)
        self._frame_pos = end

    def _end_catch_up(self) -> None:
        if not self._catching_up:
            return
        self._catching_up = False
        if self._catch_up_to is not None:
            self._catch_up_frames(self._catch_up_to, inclusive=True)
            self._catch_up_to = None
        ctx = NarrationContext.from_state(self.state)
        for channel in self.channels.values():
            channel.delivered = [
                item for i in self.engine.insights if (item := channel.personalise(i, ctx)[0])
            ]
            channel.send("sync", _json(self.sync_payload(channel)))
        self.request_futures()

    # ------------------------------------------------------------------ futures
    def _maybe_futures(self, event: MatchEvent, insights: list[Insight]) -> None:
        if (
            event.type in FUTURES_EVENTS
            or any(i.type in FUTURES_INSIGHTS for i in insights)
            or self.state.match_ms >= self._futures_due_ms
        ):
            self.request_futures()

    def request_futures(self) -> None:
        """Run futures for the match as it stands, in a worker thread; if a run is in flight, one more
        follows it."""
        if self._futures_task is not None and not self._futures_task.done():
            self._futures_again = True
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # driven synchronously (some tests): no loop to run futures on
        self._futures_again = False
        state = self.state
        self._futures_due_ms = state.match_ms + FUTURES_EVERY_MS
        inp = futures_input(state)  # read on the event loop; only the simulation leaves it
        moment = (state.match_ms, state.clock_ms, state.period)
        task = asyncio.ensure_future(self._run_futures(inp, moment, self._generation))
        self._futures_task = task
        self._narrations.add(task)  # a seek cancels it; full time waits for it
        task.add_done_callback(self._narrations.discard)

    async def _run_futures(self, inp: FuturesInput, moment: tuple[int, int, int], generation: int) -> None:
        try:
            raw, backend = await asyncio.wait_for(asyncio.to_thread(simulate, inp), FUTURES_TIMEOUT_S)
        except Exception:  # never let a failed run take the session down; the next trigger retries
            self.latency.record("futures_failed", 0.0)
            return
        if generation != self._generation:
            return  # a seek or restart happened meanwhile: this run describes a state that is gone
        state = self.state
        futures = to_futures(
            raw,
            inp,
            backend=backend,
            match_id=state.match_id,
            team_ids=(state.home_id, state.away_id),
            match_ms=moment[0],
            clock_ms=moment[1],
            period=moment[2],
        )
        state.set_futures(futures)
        self.latency.record("futures", futures.ms)
        for insight in self.engine.on_futures(futures):
            self.publish_insight(insight)
        self._maybe_state(force=True)
        if self._futures_again and not self._catching_up:
            self._futures_task = None
            self.request_futures()

    def _maybe_state(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_state_wall >= STATE_EVERY_S or self.state.finished:
            self._last_state_wall = now
            # `sent_at` (epoch ms, wall clock): viewers and relays (the edge) can measure delivery delay.
            self.broadcast("state", {**self.state.snapshot(), "sent_at": int(time.time() * 1000)})

    # ------------------------------------------------------------------ fan-out
    def broadcast(self, event: str, payload: dict[str, Any], *, ephemeral: bool = False) -> None:
        self.broadcast_text(event, _json(payload), ephemeral=ephemeral)

    def broadcast_text(self, event: str, data: str, *, ephemeral: bool = False) -> None:
        """Broadcast a payload that is already JSON text (written as `_json` would)."""
        for channel in self.channels.values():
            channel.send(event, data, ephemeral=ephemeral)

    def sync_payload(self, channel: Channel) -> dict[str, Any]:
        return {
            "meta": self.meta.model_dump(mode="json"),
            "profile": None if channel.raw else channel.profile.model_dump(mode="json"),
            "state": self.state.snapshot(),
            "insights": channel.delivered,
        }

    def snapshot(self, profile_id: str | None = None) -> tuple[int, str]:
        """The `sync` a new subscriber of this channel would get now (as JSON text), and the id of the last
        message it covers: every message with an id up to and including it is folded into the snapshot, and
        the channel's next message gets id + 1. Synchronous, so nothing can be published in between."""
        channel = self.channel(profile_id)
        return channel.next_id - 1, _json(self.sync_payload(channel))

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

    def programme(self, profile_id: str, mode: Mode) -> ProgrammeGate:
        """Make this fan channel the directed programme (one per match) and set its mode."""
        channel = self.channel(profile_id)
        for other in self.channels.values():
            if other is not channel and other.gate is not None:
                other.gate.set_mode(Mode.OFF)  # releases its pending cues
                other.gate = None
        if channel.gate is None:
            channel.gate = ProgrammeGate(
                channel.scheduler,
                channel.send_cue,
                lambda: (self.state.match_ms, self.state.clock_ms, self.state.period),
            )
        channel.gate.set_mode(mode)
        return channel.gate

    @property
    def gate(self) -> ProgrammeGate | None:
        return next((c.gate for c in self.channels.values() if c.gate is not None), None)

    def subscribe(
        self,
        *,
        profile_id: str | None = None,
        frames: bool = False,
        events: bool = True,
        last_event_id: int | None = None,
    ) -> Subscriber:
        channel = self.channel(profile_id)
        sub = Subscriber(profile_id=profile_id, frames=frames, events=events)
        oldest = channel.log[0].id if channel.log else channel.next_id
        if last_event_id is not None and oldest is not None and last_event_id >= oldest - 1:
            for out in channel.log:
                if out.id is not None and out.id > last_event_id and (events or out.event != "event"):
                    sub.queue.put_nowait(out)
        else:
            sub.queue.put_nowait(Outgoing(None, "sync", self.snapshot(profile_id)[1]))
        channel.subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        for channel in self.channels.values():
            channel.subscribers.discard(sub)
