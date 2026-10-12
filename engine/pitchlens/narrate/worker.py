"""Narration worker (§10, T-55, T-123): LLM rewrites off the hot path.

Narration text depends only on (insight, persona, language, verbosity), so fans who share those settings
share one LLM call. Calls are cached, deduplicated while in flight, run with bounded concurrency, and
shed under load: when the queue is long, low-salience moments keep their template text.

`narrate_many` takes every fan an insight reaches at once. The groups still to write go to the narrator
together: with the agent narrator (`agents/narration.py`) that is one concurrent fan-out workflow.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol

from pitchlens.schema.insights import Insight
from pitchlens.schema.profiles import Narration, Persona, Profile, Verbosity

from .narrator import Narrator
from .templates import NarrationContext

CacheKey = tuple[str, Persona, str, Verbosity]


class GroupNarrator(Protocol):
    """A narrator that writes every group's line for one insight together (the agent fan-out)."""

    async def narrate_groups(
        self, insight: Insight, profiles: list[Profile], ctx: NarrationContext
    ) -> dict[Any, Narration]: ...


def cache_key(insight: Insight, profile: Profile) -> CacheKey:
    return (insight.insight_id, profile.persona, profile.language, profile.verbosity)


@dataclass
class WorkerStats:
    requested: int = 0
    cache_hits: int = 0
    deduplicated: int = 0
    shed: int = 0
    completed: int = 0


class NarrationWorker:
    def __init__(
        self, narrator: Narrator | GroupNarrator, *, concurrency: int = 4, max_pending: int = 64,
        shed_below: float = 0.5, cache_size: int = 5000,
    ) -> None:  # fmt: skip
        self.narrator = narrator
        self._semaphore = asyncio.Semaphore(concurrency)
        self.max_pending = max_pending
        self.shed_below = shed_below
        self._cache: OrderedDict[CacheKey, Narration] = OrderedDict()
        self._cache_size = cache_size
        self._inflight: dict[CacheKey, asyncio.Task[Narration]] = {}
        self.stats = WorkerStats()

    @property
    def pending(self) -> int:
        return len(self._inflight)

    async def narrate(
        self, insight: Insight, profile: Profile, ctx: NarrationContext, *, personal_salience: float
    ) -> Narration | None:
        """The LLM narration for this fan, or None if shed (the template text then stands)."""
        self.stats.requested += 1
        key = cache_key(insight, profile)
        if key in self._cache:
            self.stats.cache_hits += 1
            self._cache.move_to_end(key)
            return self._for_profile(self._cache[key], profile)
        task = self._inflight.get(key)
        if task is not None:
            self.stats.deduplicated += 1
        else:
            if self.pending >= self.max_pending and personal_salience < self.shed_below:
                self.stats.shed += 1
                return None
            task = asyncio.ensure_future(self._run(key, insight, profile, ctx))
            self._inflight[key] = task
        return self._for_profile(await asyncio.shield(task), profile)

    async def _run(
        self, key: CacheKey, insight: Insight, profile: Profile, ctx: NarrationContext
    ) -> Narration:
        try:
            async with self._semaphore:
                narration = await self.narrator.narrate(insight, profile, ctx)
            self._cache[key] = narration
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
            self.stats.completed += 1
            return narration
        finally:
            self._inflight.pop(key, None)

    async def narrate_many(
        self, insight: Insight, fans: list[tuple[Profile, float]], ctx: NarrationContext
    ) -> dict[str, Narration | None]:
        """Lines for every fan an insight reaches (profile_id -> narration, or None if shed). Groups that are
        neither cached nor in flight are written together, concurrently."""
        out: dict[str, Narration | None] = {}
        waiting: list[tuple[Profile, asyncio.Future[Narration]]] = []
        todo: dict[CacheKey, Profile] = {}
        for profile, salience in fans:
            self.stats.requested += 1
            key = cache_key(insight, profile)
            if key in self._cache:
                self.stats.cache_hits += 1
                self._cache.move_to_end(key)
                out[profile.profile_id] = self._for_profile(self._cache[key], profile)
            elif key in self._inflight:
                self.stats.deduplicated += 1
                waiting.append((profile, self._inflight[key]))
            elif key in todo:
                self.stats.deduplicated += 1
            elif self.pending + len(todo) >= self.max_pending and salience < self.shed_below:
                self.stats.shed += 1
                out[profile.profile_id] = None
            else:
                todo[key] = profile
        if todo:
            batch = asyncio.ensure_future(self._run_batch(insight, todo, ctx))
            for key in todo:
                self._inflight[key] = asyncio.ensure_future(self._pick(batch, key))
        for profile, _ in fans:
            key = cache_key(insight, profile)
            if profile.profile_id not in out and key in todo:
                waiting.append((profile, self._inflight[key]))
        for profile, future in waiting:
            out[profile.profile_id] = self._for_profile(await asyncio.shield(future), profile)
        return out

    async def _run_batch(
        self, insight: Insight, todo: dict[CacheKey, Profile], ctx: NarrationContext
    ) -> dict[CacheKey, Narration]:
        try:
            if hasattr(self.narrator, "narrate_groups"):
                lines = await self.narrator.narrate_groups(insight, list(todo.values()), ctx)
                # Group keys are (persona, language, verbosity), as in agents.narration.group_key.
                by_key = {k: lines[(p.persona.value, p.language, p.verbosity.value)] for k, p in todo.items()}
            else:

                async def one(profile: Profile) -> Narration:
                    async with self._semaphore:
                        return await self.narrator.narrate(insight, profile, ctx)

                written = await asyncio.gather(*(one(p) for p in todo.values()))
                by_key = dict(zip(todo, written, strict=True))
            for key, narration in by_key.items():
                self._cache[key] = narration
                if len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
                self.stats.completed += 1
            return by_key
        finally:
            for key in todo:
                self._inflight.pop(key, None)

    @staticmethod
    async def _pick(batch: asyncio.Future[dict[CacheKey, Narration]], key: CacheKey) -> Narration:
        return (await asyncio.shield(batch))[key]

    @staticmethod
    def _for_profile(narration: Narration, profile: Profile) -> Narration:
        if narration.profile_id == profile.profile_id:
            return narration
        return narration.model_copy(
            update={
                "profile_id": profile.profile_id,
                "narration_id": f"n-{narration.insight_id}-{profile.profile_id}",
            }
        )
