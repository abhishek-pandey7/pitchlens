"""Ask the Match (§10A, T-127 with T-124): a fan's question answered by cooperating agents.

An Agent Framework workflow (WorkflowBuilder graph with conditional edges):

    guard ──► analyst ──► fact_checker ──approve──► companion ──► answer
                 ▲              │
                 └───revise─────┤ (one revision round)
                                └──reject / failure──► fallback ──► honest "couldn't verify"

- **guard**: fan text is data, never instructions. Length and character checks, then content safety
  (Azure AI Content Safety with Prompt Shields, or the local screen); the question only ever reaches a
  user message, never a system prompt. A blocked question gets a friendly message.
- **analyst**: Agent Framework agent with the analyst tools; answers in English with grounded numbers.
- **fact_checker**: rules first (every number backed by a tool result), then a model judge for claims
  (reflection: approve, revise or reject).
- **companion**: rewrites the approved answer for the fan's persona and language. No tools, no new
  facts. Its numbers are checked again; if they fail, the fan gets the verified English answer. The final
  answer is screened by content safety before the fan sees it.

Every step is recorded in the result's trace for the Agent Trace panel.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_framework import Agent, WorkflowBuilder, WorkflowContext, executor

from pitchlens.narrate.grounding import numbers_in, unsupported_numbers
from pitchlens.narrate.prompts import PERSONAS, glossary
from pitchlens.narrate.provider import Tier
from pitchlens.schema.profiles import Persona, Profile

from .clients import VERIFIED_MARKER, chat_client
from .factcheck import FACT_CHECKER_INSTRUCTIONS, Check, fact_check
from .runtime import ANALYST_INSTRUCTIONS, AgentRun, build_agent, run_agent, supported_numbers
from .safety import ContentSafety, blocked_message, default_safety
from .tools import MatchTools, MatchView

MAX_QUESTION_CHARS = 300
MAX_REVISIONS = 1

COULD_NOT_VERIFY = {
    "en": "Sorry, I couldn't verify an answer to that from the match data.",
    "es": "Lo siento, no he podido verificar una respuesta con los datos del partido.",
    "fr": "Désolé, je n'ai pas pu vérifier de réponse avec les données du match.",
    "pt": "Desculpe, não consegui verificar uma resposta com os dados da partida.",
    "ar": "عذرًا، لم أتمكن من التحقق من إجابة من بيانات المباراة.",
    "hi": "माफ़ कीजिए, मैच के डेटा से इसका सत्यापित जवाब नहीं मिल सका।",
}
NOT_A_QUESTION = {
    "en": "Please ask a short question about this match.",
    "es": "Haz una pregunta breve sobre este partido.",
    "fr": "Posez une courte question sur ce match.",
    "pt": "Faça uma pergunta curta sobre esta partida.",
    "ar": "يرجى طرح سؤال قصير عن هذه المباراة.",
    "hi": "कृपया इस मैच के बारे में एक छोटा सवाल पूछें।",
}

COMPANION_INSTRUCTIONS = """You are the PitchLens Companion, a friendly match companion for one fan.
Rewrite the VERIFIED ANSWER for this fan. Write in {language}. The fan is {persona}{club}
Rules:
- Use only facts from the VERIFIED ANSWER; add no new facts, numbers or names. Keep numbers exactly
  as given, except that 0..1 probabilities and shares are written as percentages (0.877 -> 88%).
  You may leave out numbers this fan does not need.
- Write player and team names exactly as given, in Latin script.
- At most {words} words. Reply with the rewritten answer only."""


# ---------------------------------------------------------------- guard
_CONTROL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")


def guard(question: str) -> str | None:
    """A cleaned question, or None if it is not something we should send to a model."""
    cleaned = unicodedata.normalize("NFKC", _CONTROL.sub(" ", question or "")).strip()
    if not cleaned or len(cleaned) > MAX_QUESTION_CHARS:
        return None
    return cleaned


# ---------------------------------------------------------------- state carried through the workflow
@dataclass
class AskState:
    question: str
    profile: Profile
    analyst_prompt: str = ""
    run: AgentRun | None = None
    check: Check | None = None
    revisions: int = 0
    answer: str = ""
    source: str = ""  # companion | analyst | fallback | guard | safety
    next: str = ""  # routing decided by the last step: analyst | fact_checker | companion | fallback
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AskResult:
    question: str
    answer: str
    lang: str
    source: str
    verdict: str
    revisions: int
    ms: float
    tokens: int
    steps: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "lang": self.lang,
            "source": self.source,
            "verdict": self.verdict,
            "revisions": self.revisions,
            "ms": round(self.ms),
            "tokens": self.tokens,
            "steps": self.steps,
        }


def companion_instructions(profile: Profile, club_names: dict[str, str]) -> str:
    language, _ = glossary(profile.language)
    clubs = [club_names[c] for c in profile.favorite_clubs if c in club_names]
    club = f" and supports {clubs[0]}." if clubs else "."
    words = {"short": 35, "medium": 60, "long": 90}[profile.verbosity.value]
    return COMPANION_INSTRUCTIONS.format(
        language=language, persona=PERSONAS[profile.persona], club=club, words=words
    )


# ---------------------------------------------------------------- the workflow
class AskTheMatch:
    """Build once per question (agents are cheap); `run` executes the workflow."""

    def __init__(
        self,
        view: Callable[[], MatchView],
        profile: Profile,
        *,
        client: Callable[[str, Tier], Any] | None = None,
        safety: ContentSafety | None = None,
    ) -> None:
        make = client or (lambda role, tier: chat_client(tier, role=role))
        self.safety = safety or default_safety()
        self.tools = MatchTools(view)
        self.profile = profile
        state = view().state
        clubs = {cid: t.name for cid, t in state.teams.items()}
        self.analyst: Agent = build_agent(
            make("analyst", Tier.FAST), self.tools, name="analyst", instructions=ANALYST_INSTRUCTIONS
        )
        self.checker = Agent(make("checker", Tier.FAST), FACT_CHECKER_INSTRUCTIONS, name="fact_checker")
        self.companion = Agent(
            make("companion", Tier.FAST), companion_instructions(profile, clubs), name="companion"
        )
        self.workflow = self._build()

    def _build(self) -> Any:
        @executor(id="guard")
        async def guard_step(s: AskState, ctx: WorkflowContext[AskState]) -> None:
            cleaned = guard(s.question)
            s.steps.append({"agent": "guard", "passed": cleaned is not None})
            verdict = await self.safety.screen_input(cleaned) if cleaned is not None else None
            if verdict is not None:
                s.steps.append(verdict.trace("input"))
            if cleaned is None:
                s.answer = NOT_A_QUESTION.get(s.profile.language, NOT_A_QUESTION["en"])
                s.source, s.next = "guard", "fallback"
            elif verdict is not None and not verdict.allowed:
                s.answer = blocked_message(s.profile.language)
                s.source, s.next = "safety", "fallback"
            else:
                s.question = s.analyst_prompt = cleaned
                s.next = "analyst"
            await ctx.send_message(s)

        @executor(id="analyst")
        async def analyst_step(s: AskState, ctx: WorkflowContext[AskState]) -> None:
            s.run = await run_agent(self.analyst, self.tools, s.analyst_prompt, keep_ledger=s.revisions > 0)
            s.steps.append({**s.run.trace(), "round": s.revisions + 1})
            await ctx.send_message(s)

        @executor(id="fact_checker")
        async def checker_step(s: AskState, ctx: WorkflowContext[AskState]) -> None:
            assert s.run is not None
            s.check = await fact_check(self.checker, s.question, s.run)
            s.steps.append(s.check.trace())
            if s.check.approved:
                s.next = "companion"
            elif s.check.verdict == "revise" and s.revisions < MAX_REVISIONS:
                s.revisions += 1
                s.analyst_prompt = (
                    f"{s.question}\n\nYour previous answer was: {s.run.text}\nThe fact-checker found these "
                    f"problems: {'; '.join(s.check.problems)}. Answer again, using tool results only."
                )
                s.next = "analyst"
            else:
                s.next = "fallback"
            await ctx.send_message(s)

        @executor(id="companion")
        async def companion_step(s: AskState, ctx: WorkflowContext[AskState, AskState]) -> None:
            assert s.run is not None
            verified = s.run.text
            if self._needs_rewrite():
                start = time.perf_counter()
                try:
                    response = await self.companion.run(
                        f"QUESTION: {s.question}\n\n{VERIFIED_MARKER}\n{verified}"
                    )
                    text = (response.text or "").strip()
                    allowed = supported_numbers(s.run.ledger, s.question) | set(numbers_in(verified))
                    bad = unsupported_numbers(text, allowed)
                    error = None
                except Exception as exc:
                    text, bad, error = "", [], str(exc)[:200]
                grounded = bool(text) and not bad and error is None
                ok = grounded and text != verified
                ms = round((time.perf_counter() - start) * 1000)
                s.steps.append({
                    "agent": "companion", "lang": self.profile.language, "grounded": grounded,
                    "unchanged": grounded and text == verified,
                    "unsupported_numbers": bad, "error": error, "ms": ms,
                })  # fmt: skip
                s.answer, s.source = (text, "companion") if ok else (verified, "analyst")
            else:
                s.answer, s.source = verified, "analyst"
            screened = await self.safety.screen_output(s.answer)
            s.steps.append(screened.trace("output"))
            if not screened.allowed:
                s.answer = COULD_NOT_VERIFY.get(self.profile.language, COULD_NOT_VERIFY["en"])
                s.source = "safety"
            await ctx.yield_output(s)

        @executor(id="fallback")
        async def fallback_step(s: AskState, ctx: WorkflowContext[AskState, AskState]) -> None:
            if s.source not in ("guard", "safety"):
                s.answer = COULD_NOT_VERIFY.get(self.profile.language, COULD_NOT_VERIFY["en"])
                s.source = "fallback"
            await ctx.yield_output(s)

        def to(step: str) -> Callable[[AskState], bool]:
            return lambda s: s.next == step

        return (
            WorkflowBuilder(start_executor=guard_step, name="ask_the_match")
            .add_edge(guard_step, analyst_step, condition=to("analyst"))
            .add_edge(guard_step, fallback_step, condition=to("fallback"))
            .add_edge(analyst_step, checker_step)
            .add_edge(checker_step, companion_step, condition=to("companion"))
            .add_edge(checker_step, analyst_step, condition=to("analyst"))
            .add_edge(checker_step, fallback_step, condition=to("fallback"))
            .build()
        )

    def _needs_rewrite(self) -> bool:
        p = self.profile
        return not (p.language == "en" and p.persona is Persona.ANALYST)

    async def run(self, question: str) -> AskResult:
        start = time.perf_counter()
        result = await self.workflow.run(AskState(question=question, profile=self.profile))
        state: AskState = result.get_outputs()[-1]
        tokens = sum(int(step.get("tokens") or 0) for step in state.steps)
        return AskResult(
            question=state.question,
            answer=state.answer,
            lang=self.profile.language if state.source != "analyst" else "en",
            source=state.source,
            verdict=state.check.verdict if state.check else "none",
            revisions=state.revisions,
            ms=(time.perf_counter() - start) * 1000,
            tokens=tokens,
            steps=state.steps,
        )
