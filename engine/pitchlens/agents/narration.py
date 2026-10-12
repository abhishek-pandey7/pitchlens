"""Narrator and Localization agents (§10A, T-123): live commentary, one line per fan group, in parallel.

When an insight lands, every distinct (persona, language, verbosity) group among the connected fans
needs its own line. An Agent Framework **concurrent workflow** (`ConcurrentBuilder`) fans out one
participant per group and gathers the lines back (fan-out / fan-in):

    insight ──► Narrator (casual · es) ─────────────────────────────┐
           ├──► Narrator (analyst · en) ─────────────────────────────┤──► lines by group
           └──► Narrator (casual · hi, pivot en) ──► Localization ───┘

- **Narrator**: writes the line from the evidence (the same prompt as the direct path: persona, glossary,
  ground rules, the template as a reference). One retry with the grounding validator's violations.
- **Localization** (pivot languages, `NARRATION_PIVOT_LANGS`, default Arabic and Hindi): the Narrator
  writes in English and the Localization agent transcreates it: idiomatic, glossary terms, numbers kept,
  names in Latin script. One retry with the violations, as above.
- Every line passes `grounding.validate` in its own language, or the fan keeps the template line.

The agents have no tools: the facts are the insight's evidence, nothing else.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_framework import (
    Agent,
    AgentExecutorRequest,
    AgentExecutorResponse,
    AgentResponse,
    Executor,
    Message,
    WorkflowContext,
    handler,
)
from agent_framework_orchestrations import ConcurrentBuilder
from pydantic import BaseModel, Field

from pitchlens.narrate.grounding import validate
from pitchlens.narrate.narrator import narration_id, template_narration
from pitchlens.narrate.prompts import PERSONAS, build_request, glossary, parse_response
from pitchlens.narrate.provider import Tier
from pitchlens.narrate.templates import NarrationContext, render
from pitchlens.schema.insights import Insight
from pitchlens.schema.profiles import Narration, Profile

from .clients import chat_client

PIVOT_LANGS = frozenset(x for x in os.environ.get("NARRATION_PIVOT_LANGS", "ar,hi").split(",") if x)
LIVE_WAIT_S = 2.0  # a live line is not worth waiting long for free-tier budget; the template stands

LOCALIZER_INSTRUCTIONS = """You are the PitchLens Localization agent. Transcreate the football commentary
LINE into {language} for {persona}
Rules:
- Natural, idiomatic {language} the way a broadcaster in that market would say it, not a word-for-word
  translation. Use these football terms where they fit: {terms}.
- Keep every number exactly as in the LINE (digits, not words); you may drop a number, never add one.
- Keep player and team names exactly as written, in Latin script.
- REFERENCE is a correct line in {language} about the same moment: follow its terms and style if useful.
Reply in JSON only: {{"text": "<the line in {language}>"}}"""


class Line(BaseModel):
    text: str
    emphasis_entities: list[str] = Field(default_factory=list)


GroupKey = tuple[str, str, str]  # persona, language, verbosity


def group_key(profile: Profile) -> GroupKey:
    return (profile.persona.value, profile.language, profile.verbosity.value)


@dataclass
class NarrationStats:
    groups: int = 0
    llm_passed: int = 0
    retried: int = 0
    localized: int = 0
    template_fallback: int = 0
    provider_errors: int = 0
    workflows: int = 0
    last_fanout: int = 0


@dataclass
class _GroupResult:
    narration: Narration | None = None
    steps: list[str] = field(default_factory=list)


class AgentNarrator:
    """Same interface as `narrate.Narrator`, plus `narrate_groups` for the concurrent fan-out."""

    def __init__(
        self,
        *,
        client: Callable[[str, Tier], Any] | None = None,
        max_attempts: int = 2,
        pivot_langs: frozenset[str] = PIVOT_LANGS,
        concurrency: int = 4,
    ) -> None:
        self._make = client or (lambda role, tier: chat_client(tier, role=role, max_wait_s=LIVE_WAIT_S))
        self.max_attempts = max_attempts
        self.pivot_langs = pivot_langs
        self._slots = asyncio.Semaphore(concurrency)
        self.stats = NarrationStats()

    # ------------------------------------------------------------------ the Narrator protocol
    def template(self, insight: Insight, profile: Profile, ctx: NarrationContext) -> Narration:
        return template_narration(insight, profile, ctx)

    async def narrate(self, insight: Insight, profile: Profile, ctx: NarrationContext, **_: Any) -> Narration:
        return (await self.narrate_groups(insight, [profile], ctx))[group_key(profile)]

    async def narrate_groups(
        self, insight: Insight, profiles: list[Profile], ctx: NarrationContext
    ) -> dict[GroupKey, Narration]:
        """One line per distinct group among `profiles`, written concurrently by one Agent Framework
        concurrent workflow (fan-out to a participant per group, fan-in to the results)."""
        groups: dict[GroupKey, Profile] = {}
        for profile in profiles:
            groups.setdefault(group_key(profile), profile)
        seats = [
            _GroupSeat(f"narrator-{n}", self, insight, member, ctx)
            for n, member in enumerate(groups.values())
        ]
        self.stats.groups += len(seats)
        self.stats.last_fanout = len(seats)
        by_group: dict[GroupKey, _GroupResult] = {}
        try:
            if len(seats) == 1:  # nothing to fan out (a concurrent workflow needs two participants)
                by_group = {group_key(seats[0].profile): await self.line_for(insight, seats[0].profile, ctx)}
            else:
                workflow = (
                    ConcurrentBuilder(name="narration_fanout", participants=seats)
                    .with_aggregator(lambda _results: {group_key(s.profile): s.result for s in seats})
                    .build()
                )
                self.stats.workflows += 1
                outputs = (await workflow.run(insight.insight_id)).get_outputs()
                by_group = outputs[-1] if outputs else {}
        except Exception:  # the orchestration itself failed: every group keeps its template
            by_group = {}
        return {
            key: (by_group.get(key) or _GroupResult()).narration or self._fallback(insight, profile, ctx)
            for key, profile in groups.items()
        }

    # ------------------------------------------------------------------ one group
    async def line_for(self, insight: Insight, profile: Profile, ctx: NarrationContext) -> _GroupResult:
        start = time.perf_counter()
        result = _GroupResult()
        async with self._slots:
            if profile.language in self.pivot_langs and profile.language != "en":
                pivot = profile.model_copy(update={"language": "en"})
                english = await self._write(insight, pivot, ctx, result)
                text = await self._localize(english, insight, profile, ctx, result) if english else None
            else:
                text = await self._write(insight, profile, ctx, result)
        if text is not None:
            self.stats.llm_passed += 1
            result.narration = Narration(
                narration_id=narration_id(insight, profile),
                insight_id=insight.insight_id,
                profile_id=profile.profile_id,
                lang=profile.language,
                text=text,
                source="llm",
                grounding=validate(text, insight, ctx, profile.language),
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
        return result

    async def _write(
        self, insight: Insight, profile: Profile, ctx: NarrationContext, result: _GroupResult
    ) -> str | None:
        """The Narrator agent: the line in `profile.language`, grounded, or None."""
        feedback: list[str] | None = None
        for attempt in range(self.max_attempts):
            request = build_request(insight, ctx, profile, feedback=feedback)
            agent = Agent(self._make("narrator_live", Tier.FAST), request.system, name="narrator")
            text = await self._ask(agent, request.user, result, "narrator")
            if text is None:
                return None
            grounding = validate(text, insight, ctx, profile.language)
            if grounding.passed:
                self.stats.retried += attempt > 0
                return text
            feedback = grounding.violations
            result.steps.append(f"narrator: {'; '.join(feedback)}")
        return None

    async def _localize(
        self, english: str, insight: Insight, profile: Profile, ctx: NarrationContext, result: _GroupResult
    ) -> str | None:
        """The Localization agent: the English line transcreated into the fan's language, grounded."""
        language, terms = glossary(profile.language)
        instructions = LOCALIZER_INSTRUCTIONS.format(
            language=language, persona=PERSONAS[profile.persona],
            terms=", ".join(f"{k} = {v}" for k, v in list(terms.items())[:12]),
        )  # fmt: skip
        payload = {"LINE": english, "REFERENCE": render(insight, ctx, profile).text}
        feedback = ""
        for attempt in range(self.max_attempts):
            agent = Agent(self._make("localizer", Tier.FAST), instructions, name="localizer")
            prompt = json.dumps(payload, ensure_ascii=False) + feedback
            text = await self._ask(agent, prompt, result, "localizer")
            if text is None:
                return None
            grounding = validate(text, insight, ctx, profile.language)
            if grounding.passed:
                self.stats.localized += 1
                self.stats.retried += attempt > 0
                return text
            feedback = f"\n\nYour previous line broke the rules: {'; '.join(grounding.violations)}. Fix it."
            result.steps.append(f"localizer: {'; '.join(grounding.violations)}")
        return None

    async def _ask(self, agent: Agent, prompt: str, result: _GroupResult, who: str) -> str | None:
        try:
            response = await agent.run(prompt, options={"response_format": Line})
        except Exception as exc:  # provider down, budget spent: the template stands
            self.stats.provider_errors += 1
            result.steps.append(f"{who}: {str(exc)[:120]}")
            return None
        value = response.value if isinstance(getattr(response, "value", None), Line) else None
        text = value.text if value else parse_response(response.text or "")[0]
        return text.strip() or None

    def _fallback(self, insight: Insight, profile: Profile, ctx: NarrationContext) -> Narration:
        self.stats.template_fallback += 1
        return template_narration(insight, profile, ctx)


class _GroupSeat(Executor):
    """One participant of the concurrent workflow: the line for one persona-language group."""

    def __init__(
        self, seat_id: str, narrator: AgentNarrator, insight: Insight, profile: Profile, ctx: NarrationContext
    ) -> None:
        super().__init__(seat_id)
        self.narrator, self.insight, self.profile, self.ctx = narrator, insight, profile, ctx
        self.result = _GroupResult()

    @handler
    async def write(self, request: AgentExecutorRequest, ctx: WorkflowContext[AgentExecutorResponse]) -> None:
        self.result = await self.narrator.line_for(self.insight, self.profile, self.ctx)
        text = self.result.narration.text if self.result.narration else ""
        reply = Message("assistant", [text], author_name=self.id)
        await ctx.send_message(AgentExecutorResponse(self.id, AgentResponse(messages=[reply]), [reply]))


def narration_agents_enabled(provider: str | None = None) -> bool:
    """`NARRATION_AGENTS=auto|on|off`. Auto: on for providers the agent clients support (Groq, Azure
    OpenAI / Foundry, OpenAI-compatible, the offline mock); off for Claude on Foundry (direct path)."""
    mode = os.environ.get("NARRATION_AGENTS", "auto").lower()
    if mode in ("on", "1", "true"):
        return True
    if mode in ("off", "0", "false"):
        return False
    name = provider or os.environ.get("LLM_PROVIDER", "mock")
    return name in ("mock", "groq", "azure_openai", "openai_compat")
