"""In-process bus: every subscriber of a match gets its own unbounded queue (no message is ever dropped)."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from .bus import BusMessage

_CLOSED = None


class Subscription:
    """Async iterator over one match's messages. Registered on creation, so nothing published after
    `subscribe()` returns can be missed."""

    def __init__(self, bus: MemoryBus, match_id: str) -> None:
        self._bus = bus
        self.match_id = match_id
        self.queue: asyncio.Queue[BusMessage | None] = asyncio.Queue()

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> BusMessage:
        message = await self.queue.get()
        if message is _CLOSED:
            raise StopAsyncIteration
        return message

    def close(self) -> None:
        self._bus._unsubscribe(self)
        self.queue.put_nowait(_CLOSED)

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()


class MemoryBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Subscription]] = defaultdict(list)

    async def publish(self, message: BusMessage) -> None:
        for sub in list(self._subs.get(message.match_id, ())):
            sub.queue.put_nowait(message)

    def subscribe(self, match_id: str) -> Subscription:
        sub = Subscription(self, match_id)
        self._subs[match_id].append(sub)
        return sub

    def subscriber_count(self, match_id: str) -> int:
        return len(self._subs.get(match_id, ()))

    def _unsubscribe(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.match_id, [])
        if sub in subs:
            subs.remove(sub)

    async def close(self) -> None:
        for subs in list(self._subs.values()):
            for sub in list(subs):
                sub.close()
