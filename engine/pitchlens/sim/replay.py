"""Replay a stored match onto the bus in match time (§7, T-14).

Events and tracking frames are merged into one timeline ordered by `match_ms` and published when the
wall clock reaches `origin + match_ms / speed`. Supports pause/resume, live speed changes and seeking.
A seek publishes a `reset` control message, then every event up to the target instantly with
`catch_up=True`, so consumers rebuild state just by applying events in order.

Clock and sleep are injectable so scheduling can be tested deterministically.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from pitchlens.ingest.bus import Bus, BusMessage, MessageKind
from pitchlens.schema.events import MatchEvent, MatchMeta

from .tracking import TICK_MS, Tracking

MAX_FRAME_MSGS_PER_SEC = 25  # frames are thinned at high speeds; events are never dropped


class ReplayState(StrEnum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    FINISHED = "finished"


@dataclass(order=True)
class _Item:
    match_ms: int
    order: int  # events before frames at the same instant, then stable by sequence
    seq: int
    event: MatchEvent | None = field(default=None, compare=False)
    frame_index: int | None = field(default=None, compare=False)


class Replay:
    def __init__(
        self,
        bus: Bus,
        meta: MatchMeta,
        events: list[MatchEvent],
        tracking: Tracking | None = None,
        *,
        speed: float = 1.0,
        thin_frames: bool = True,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        # Thin frames only when the bus feeds displays directly. A pipeline that computes physical
        # metrics needs every frame and thins for display at its own edge (see live.session).
        self.thin_frames = thin_frames
        self.bus = bus
        self.meta = meta
        self.tracking = tracking
        self.speed = speed
        self._clock = clock
        self._sleep = sleep
        items = [_Item(e.match_ms, 0, e.seq, event=e) for e in events]
        if tracking is not None:
            items += [_Item(int(ms), 1, i, frame_index=i) for i, ms in enumerate(tracking.ms)]
        self._items = sorted(items)
        self._pos = 0
        self.state = ReplayState.IDLE
        self._resume = asyncio.Event()
        self._resume.set()
        self._wake = asyncio.Event()
        self._origin_wall = 0.0
        self._origin_ms = 0
        self._pending_seek: int | None = None
        self._last_frame_wall = -1.0
        self.published = 0

    @property
    def match_id(self) -> str:
        return self.meta.match_id

    @property
    def match_ms(self) -> int:
        """Current position in match time."""
        if self.state is ReplayState.PLAYING:
            return int(self._origin_ms + (self._clock() - self._origin_wall) * 1000 * self.speed)
        return self._origin_ms

    # ----------------------------------------------------------------- controls
    def pause(self) -> None:
        if self.state is ReplayState.PLAYING:
            self._origin_ms = self.match_ms
            self.state = ReplayState.PAUSED
            self._resume.clear()
            self._wake.set()

    def resume(self) -> None:
        if self.state is ReplayState.PAUSED:
            self._origin_wall = self._clock()
            self.state = ReplayState.PLAYING
            self._resume.set()

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self._origin_ms, self._origin_wall = self.match_ms, self._clock()
        self.speed = speed
        self._wake.set()

    def seek(self, match_ms: int) -> None:
        self._pending_seek = max(0, match_ms)
        self._wake.set()

    # ----------------------------------------------------------------- running
    async def run(self, from_ms: int = 0) -> None:
        await self.bus.publish(
            BusMessage(kind=MessageKind.META, match_id=self.match_id, match_ms=0, meta=self.meta)
        )
        self.state = ReplayState.PLAYING
        if from_ms:
            await self._do_seek(from_ms)
        else:
            self._origin_ms, self._origin_wall = 0, self._clock()
        while self._pos < len(self._items):
            if self._pending_seek is not None:
                target, self._pending_seek = self._pending_seek, None
                await self._do_seek(target)
                continue
            if not self._resume.is_set():
                # Paused: wake up for resume, or for a seek (applied immediately so consumers update).
                self._wake.clear()
                await self._first_of(self._resume.wait(), self._wake.wait())
                continue
            item = self._items[self._pos]
            due = self._origin_wall + (item.match_ms - self._origin_ms) / 1000 / self.speed
            delay = due - self._clock()
            if delay > 0:
                self._wake.clear()
                woken = await self._first_of(self._sleep(delay), self._wake.wait()) == 1
                # A pause/seek/speed change may land in the same tick the sleep finished: re-check.
                if woken or not self._resume.is_set() or self._pending_seek is not None:
                    continue
            self._pos += 1
            await self._publish(item, catch_up=False)
        self.state = ReplayState.FINISHED
        await self.bus.publish(
            BusMessage(
                kind=MessageKind.CONTROL,
                match_id=self.match_id,
                match_ms=self._items[-1].match_ms if self._items else 0,
                control={"action": "finished"},
            )
        )

    @staticmethod
    async def _first_of(*awaitables: Awaitable[object]) -> int:
        """Await whichever finishes first; returns its index and cancels the rest."""
        tasks = [asyncio.ensure_future(a) for a in awaitables]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        return min(tasks.index(t) for t in done)

    async def _do_seek(self, target_ms: int) -> None:
        await self.bus.publish(
            BusMessage(
                kind=MessageKind.CONTROL,
                match_id=self.match_id,
                match_ms=target_ms,
                control={"action": "reset", "to_ms": target_ms},
            )
        )
        self._pos = 0
        while self._pos < len(self._items) and self._items[self._pos].match_ms <= target_ms:
            item = self._items[self._pos]
            self._pos += 1
            if item.event is not None:
                await self._publish(item, catch_up=True)
        self._origin_ms, self._origin_wall = target_ms, self._clock()

    async def _publish(self, item: _Item, *, catch_up: bool) -> None:
        if item.event is not None:
            message = BusMessage(
                kind=MessageKind.EVENT,
                match_id=self.match_id,
                match_ms=item.match_ms,
                catch_up=catch_up,
                event=item.event,
            )
        else:
            assert self.tracking is not None and item.frame_index is not None
            now = self._clock()
            min_gap = max(TICK_MS / 1000 / self.speed, 1 / MAX_FRAME_MSGS_PER_SEC)
            if (
                self.thin_frames
                and self._last_frame_wall >= 0
                and now - self._last_frame_wall < min_gap * 0.999
            ):
                return
            self._last_frame_wall = now
            message = BusMessage(
                kind=MessageKind.FRAME,
                match_id=self.match_id,
                match_ms=item.match_ms,
                frame=self.tracking.frame(item.frame_index),
            )
        self.published += 1
        await self.bus.publish(message)
        # Yield every message: when the replay is behind schedule it never sleeps, and an in-memory publish
        # never suspends, so without this one fast replay would starve every other task on the loop.
        await asyncio.sleep(0)
