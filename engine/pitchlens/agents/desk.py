"""The agent desk: what the API needs to run agents for many fans at once.

- one MatchViews cache shared by "Ask the Match" and the MCP server
- a concurrency limit, so a burst of questions queues instead of overrunning the model's rate limits
- a short log of recent agent runs per match, for the Agent Trace panel (T-129)
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pitchlens.schema.profiles import Profile

from .ask import AskResult, AskTheMatch
from .notes import TacticalNotes, note_enabled
from .views import MatchViews

if TYPE_CHECKING:
    from pitchlens.live.hub import Hub
    from pitchlens.live.session import MatchSession

TRACE_KEEP = 25
QUEUE_TIMEOUT_S = 120.0


class DeskBusy(RuntimeError):
    """Too many questions in flight for too long."""


class AgentDesk:
    def __init__(
        self,
        hub: Callable[[], Hub],
        *,
        concurrency: int | None = None,
        notes: TacticalNotes | bool | None = None,
    ) -> None:
        self.views = MatchViews(hub)
        self._slots = asyncio.Semaphore(concurrency or int(os.environ.get("ASK_CONCURRENCY", "2")))
        self._traces: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=TRACE_KEEP))
        if notes is None:
            notes = note_enabled()
        if notes is True:
            notes = TacticalNotes()
        self.notes: TacticalNotes | None = notes or None
        if self.notes is not None:
            self.notes.on_trace = self.record

    def attach(self, session: MatchSession) -> None:
        """Hub hook: let the Analyst watch each live session for tactical notes."""
        if self.notes is not None:
            session.observers.append(self.notes.observe)

    def record(self, match_id: str, trace: dict[str, Any]) -> None:
        self._traces[match_id].appendleft(trace)

    async def ask(self, match_id: str, question: str, profile: Profile) -> AskResult:
        view = await self.views.view(match_id)  # ToolError for an unknown match
        try:
            await asyncio.wait_for(self._slots.acquire(), QUEUE_TIMEOUT_S)
        except TimeoutError:
            raise DeskBusy("the match companion is busy; try again in a minute") from None
        try:
            result = await AskTheMatch(view, profile).run(question)
        finally:
            self._slots.release()
        self.record(
            match_id, {"kind": "ask", "at": time.time(), "profile_id": profile.profile_id, **result.to_dict()}
        )
        return result

    def traces(self, match_id: str) -> list[dict[str, Any]]:
        return list(self._traces.get(match_id, ()))
