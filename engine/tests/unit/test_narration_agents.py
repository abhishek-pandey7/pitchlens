"""Narrator and Localization agents with the concurrent fan-out (T-123). Offline."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable
from typing import Any

import pytest
from agent_framework import ChatResponse

from pitchlens.agents.clients import OfflineChatClient
from pitchlens.agents.narration import AgentNarrator, group_key, narration_agents_enabled
from pitchlens.agents.tools import MatchView
from pitchlens.evals.common import match_view
from pitchlens.narrate.templates import NarrationContext, render
from pitchlens.narrate.worker import NarrationWorker
from pitchlens.personalize.profiles import PRESETS
from pitchlens.schema.insights import Insight
from pitchlens.schema.profiles import Persona, Profile


@pytest.fixture(scope="module")
def view() -> MatchView:
    return match_view("comeback", 7)


@pytest.fixture(scope="module")
def goal(view: MatchView) -> Insight:
    return next(i for i in view.insights if i.type.value == "goal")


@pytest.fixture(scope="module")
def ctx(view: MatchView) -> NarrationContext:
    return NarrationContext.from_state(view.state)


def line(text: str) -> str:
    return json.dumps({"text": text, "emphasis_entities": []})


def scripted(**scripts: list[Any]) -> Any:
    clients: dict[str, list[OfflineChatClient]] = {}

    def make(role: str, tier: Any) -> OfflineChatClient:
        script = [scripts[role].pop(0)] if scripts.get(role) else None
        client = OfflineChatClient(script, role=role)
        clients.setdefault(role, []).append(client)
        return client

    make.clients = clients  # type: ignore[attr-defined]
    return make


async def test_one_workflow_writes_every_group_and_localizes_pivot_languages(
    goal: Insight, ctx: NarrationContext
) -> None:
    make = scripted()
    narrator = AgentNarrator(client=make)
    lines = await narrator.narrate_groups(goal, list(PRESETS), ctx)
    assert set(lines) == {group_key(p) for p in PRESETS}
    assert narrator.stats.workflows == 1 and narrator.stats.last_fanout == len(lines)
    for profile in PRESETS:
        narration = lines[group_key(profile)]
        assert narration.source == "llm" and narration.grounding.passed and narration.lang == profile.language
        assert narration.text == render(goal, ctx, profile).text  # the offline agents echo the reference
    assert len(make.clients["localizer"]) == 2  # Hindi and Arabic go through the Localization agent
    assert narrator.stats.localized == 2


async def test_groups_are_written_concurrently(goal: Insight, ctx: NarrationContext) -> None:
    class Slow(OfflineChatClient):
        def _inner_get_response(self, **kwargs: Any) -> Awaitable[ChatResponse]:
            reply = super()._inner_get_response(**kwargs)

            async def later() -> ChatResponse:
                await asyncio.sleep(0.3)
                return await reply

            return later()

    narrator = AgentNarrator(client=lambda role, tier: Slow(role=role))
    fans = [Profile(profile_id=f"f{n}", persona=p, language=lang)
            for n, (p, lang) in enumerate([(Persona.CASUAL, "en"), (Persona.ANALYST, "en"),
                                           (Persona.CASUAL, "es"), (Persona.TACTICIAN, "fr")])]  # fmt: skip
    start = time.perf_counter()
    lines = await narrator.narrate_groups(goal, fans, ctx)
    assert len(lines) == 4 and all(n.source == "llm" for n in lines.values())
    assert time.perf_counter() - start < 0.3 * 4 * 0.75  # in parallel, not one after another


async def test_invented_numbers_get_one_retry_then_the_template(goal: Insight, ctx: NarrationContext) -> None:
    profile = Profile(profile_id="x", persona=Persona.CASUAL, language="en")
    fixed = render(goal, ctx, profile).text
    retried = AgentNarrator(client=scripted(narrator_live=[line("What a goal, his 41st!"), line(fixed)]))
    assert (await retried.narrate(goal, profile, ctx)).source == "llm" and retried.stats.retried == 1

    stubborn = AgentNarrator(client=scripted(narrator_live=[line("41 goals!"), line("Now 42!")]))
    narration = await stubborn.narrate(goal, profile, ctx)
    assert narration.source == "template" and narration.text == fixed
    assert stubborn.stats.template_fallback == 1


async def test_a_failing_localization_keeps_the_template_in_the_fans_language(
    goal: Insight, ctx: NarrationContext
) -> None:
    hindi = next(p for p in PRESETS if p.language == "hi")
    narrator = AgentNarrator(client=scripted(localizer=[line("गोल! 99 मिनट"), line("गोल! 98 मिनट")]))
    narration = await narrator.narrate(goal, hindi, ctx)
    assert narration.source == "template" and narration.lang == "hi"
    assert narration.text == render(goal, ctx, hindi).text


async def test_provider_errors_fall_back_quietly(goal: Insight, ctx: NarrationContext) -> None:
    narrator = AgentNarrator(client=lambda role, tier: OfflineChatClient([], role=role))  # every call fails
    narration = await narrator.narrate(goal, PRESETS[0], ctx)
    assert narration.source == "template" and narrator.stats.provider_errors == 1


async def test_worker_batches_dedupes_and_caches(goal: Insight, ctx: NarrationContext) -> None:
    narrator = AgentNarrator(client=scripted())
    worker = NarrationWorker(narrator)
    twins = [Profile(profile_id=f"es-{n}", persona=Persona.CASUAL, language="es") for n in range(3)]
    fans = [(p, 0.9) for p in [*twins, PRESETS[1]]]
    first = await worker.narrate_many(goal, fans, ctx)
    assert {k: v.profile_id for k, v in first.items() if v} == {p.profile_id: p.profile_id for p, _ in fans}
    assert narrator.stats.workflows == 1 and narrator.stats.last_fanout == 2  # three Spanish fans, one line
    assert worker.stats.deduplicated == 2
    again = await worker.narrate_many(goal, fans, ctx)
    assert worker.stats.cache_hits == 4 and narrator.stats.workflows == 1
    assert again["es-1"] is not None and again["es-1"].text == first["es-0"].text  # type: ignore[union-attr]


def test_mode_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARRATION_AGENTS", "auto")
    assert narration_agents_enabled("groq") and narration_agents_enabled("mock")
    assert not narration_agents_enabled("foundry_claude")  # Claude on Foundry keeps the direct path
    monkeypatch.setenv("NARRATION_AGENTS", "off")
    assert not narration_agents_enabled("groq")
