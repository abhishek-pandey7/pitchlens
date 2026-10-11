"""Resolve a match id to what the agents read: a live session, or a stored match analysed once."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pitchlens.explain.engine import explain_match
from pitchlens.live.hub import HubError

from .tools import MatchView, ToolError

if TYPE_CHECKING:
    from pitchlens.live.hub import Hub


class MatchViews:
    """Resolve a match_id to a MatchView: the live session if one is running, else the stored match
    analysed once (with tracking when available) and cached."""

    def __init__(self, hub: Callable[[], Hub]) -> None:
        self._hub = hub
        self._stored: dict[str, MatchView] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def view(self, match_id: str) -> Callable[[], MatchView]:
        hub = self._hub()
        session = hub.live_session(match_id)
        if session is not None:
            return lambda: MatchView(session.state, session.engine.insights)
        lock = self._locks.setdefault(match_id, asyncio.Lock())
        async with lock:
            if match_id not in self._stored:
                try:
                    record = hub.record(match_id)
                except HubError as exc:
                    raise ToolError(str(exc)) from None
                if not record.events:
                    raise ToolError(f"match {match_id!r} has no events yet")
                self._stored[match_id] = await asyncio.to_thread(self._analyse, record)
        stored = self._stored[match_id]
        return lambda: stored

    @staticmethod
    def _analyse(record: Any) -> MatchView:
        tracking = record.tracking
        frames = [tracking.frame(i) for i in range(len(tracking))] if tracking is not None else []
        state, insights = explain_match(record.meta, record.events, frames)
        return MatchView(state, insights)
