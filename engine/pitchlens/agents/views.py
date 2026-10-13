"""Resolve a match id to what the agents read: a live session, or a stored match analysed once."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pitchlens.explain.engine import explain_match
from pitchlens.live.hub import HubError
from pitchlens.schema.events import EventType

from .tools import MatchView, ToolError

if TYPE_CHECKING:
    from pitchlens.live.hub import Hub


class NotYet(LookupError):
    """The match has not reached that stage yet (no half-time recap before half-time)."""


class MatchViews:
    """Resolve a match_id to a MatchView: the live session if one is running, else the stored match
    analysed once (with tracking when available) and cached."""

    def __init__(self, hub: Callable[[], Hub]) -> None:
        self._hub = hub
        self._stored: dict[str, MatchView] = {}
        self._stages: dict[tuple[str, str], MatchView] = {}
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

    async def stage_view(self, match_id: str, stage: str) -> Callable[[], MatchView]:
        """The match as it stood at the end of a stage: "ht" (the first half's final whistle) or "ft".

        Analysed from the stored events up to that whistle, so a half-time recap stays a half-time
        recap while the second half plays on. NotYet if a live replay has not reached the whistle.
        """
        hub = self._hub()
        try:
            record = hub.record(match_id)
        except HubError as exc:
            raise ToolError(str(exc)) from None
        events = record.events
        if stage == "ht":
            cut = next(
                (i for i, e in enumerate(events) if e.type == EventType.PERIOD_END and e.period == 1), None
            )
        else:
            cut = len(events) - 1 if events and events[-1].type == EventType.MATCH_END else None
        whistle = "half-time" if stage == "ht" else "full time"
        if cut is None:
            raise NotYet(f"match {match_id!r} has not reached {whistle}")
        session = hub.live_session(match_id)
        if session is not None and session.state.match_ms < events[cut].match_ms:
            raise NotYet(f"the live match has not reached {whistle} yet")
        key = (match_id, stage)
        lock = self._locks.setdefault(match_id, asyncio.Lock())
        async with lock:
            if key not in self._stages:
                self._stages[key] = await asyncio.to_thread(self._analyse, record, cut)
        cached = self._stages[key]
        return lambda: cached

    def _analyse(self, record: Any, cut: int | None = None) -> MatchView:
        """Analyse the whole match, or its events up to and including index `cut`."""
        self._hub().store.ensure_tracking(record)  # distance and speed need tracking
        events = record.events if cut is None else record.events[: cut + 1]
        tracking = record.tracking
        last = len(tracking) if tracking is not None else 0
        if tracking is not None and cut is not None:
            last = tracking.index_at(events[-1].match_ms) + 1
        frames = [tracking.frame(i) for i in range(last)] if tracking is not None else []
        state, insights = explain_match(record.meta, events, frames)
        return MatchView(state, insights)
