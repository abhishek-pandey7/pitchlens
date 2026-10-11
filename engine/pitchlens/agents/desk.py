"""The agent desk: what the API needs to run agents for many fans at once.

- one MatchViews cache shared by "Ask the Match" and the MCP server
- a concurrency limit, so a burst of questions queues instead of overrunning the model's rate limits
- a short log of recent agent runs per match, for the Agent Trace panel (T-129)
- recaps (T-126): the Editor's group chat runs once per match and stage; each fan's version (persona,
  language, club) is adapted from it once and cached
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pitchlens.schema.profiles import Profile

from .ask import AskResult, AskTheMatch
from .editor import EditorRoom, Master, RecapResult, adapt
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
        self._masters: dict[tuple[str, str], Master] = {}
        self._recaps: dict[tuple[Any, ...], RecapResult] = {}
        self._recap_locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
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

    @asynccontextmanager
    async def _slot(self) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(self._slots.acquire(), QUEUE_TIMEOUT_S)
        except TimeoutError:
            raise DeskBusy("the agents are busy; try again in a minute") from None
        try:
            yield
        finally:
            self._slots.release()

    async def ask(self, match_id: str, question: str, profile: Profile) -> AskResult:
        view = await self.views.view(match_id)  # ToolError for an unknown match
        async with self._slot():
            result = await AskTheMatch(view, profile).run(question)
        self.record(
            match_id, {"kind": "ask", "at": time.time(), "profile_id": profile.profile_id, **result.to_dict()}
        )
        return result

    async def recap(self, match_id: str, stage: str, profile: Profile) -> RecapResult:
        """The fan's half-time or full-time recap. ToolError for an unknown match, NotYet before the
        whistle. Only approved recaps are cached, so a failed group chat is retried next time."""
        view = await self.views.stage_view(match_id, stage)
        clubs = {cid: t.name for cid, t in view().state.teams.items()}
        fan = (match_id, stage, profile.language, profile.persona, tuple(profile.favorite_clubs),
               profile.verbosity)  # fmt: skip
        async with self._recap_locks[(match_id, stage)]:
            if fan in self._recaps:
                return replace(self._recaps[fan], cached=True)
            master = self._masters.get((match_id, stage))
            if master is None:
                async with self._slot():
                    master = await EditorRoom(view, stage).write()
                if master.approved:
                    self._masters[(match_id, stage)] = master
            async with self._slot():
                result = await adapt(master, profile, clubs)
            if master.approved:
                self._recaps[fan] = result
        trace = {"kind": "recap", "at": time.time(), "profile_id": profile.profile_id, **result.to_dict()}
        self.record(match_id, trace)
        return result

    def traces(self, match_id: str) -> list[dict[str, Any]]:
        return list(self._traces.get(match_id, ()))
