"""Fact-Checker agent (§10A, T-124): the critic in a reflection loop.

Two stages, cheapest first:

1. **Rules.** Every number in the draft must be backed by a tool result (`run_agent`'s ledger check).
   An unsupported number means `revise`, with the numbers listed, and no model call is needed.
2. **Judge.** A model reads the draft beside the facts (the tool results) and decides whether each
   claim follows from them: the right team, player, metric, direction and moment. This catches what a
   number check cannot, such as a real number attached to the wrong fact ("Southvale scored 7" when 7
   is Northbridge's shots). The verdict is structured output: approve, revise (with problems) or reject.

A failed judge call never blocks an answer whose numbers all check out: the verdict falls back to the
rules result and says so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from agent_framework import Agent
from pydantic import BaseModel, Field

from .runtime import AgentRun, ledger_facts

FACT_CHECKER_INSTRUCTIONS = """You are the PitchLens Fact-Checker. You review a football analyst's draft
answer against FACTS, the exact tool results the analyst saw. Judge substance, not style.
- approve: every claim follows from FACTS. The right team, player, metric, direction (rose or fell),
  minute and comparison. Rounding and percentages of 0..1 values are fine.
- revise: some claims are wrong or unsupported, but a corrected answer is possible. List each problem
  precisely, e.g. "7 is Northbridge's shots, not Southvale's goals".
- reject: the draft mostly does not follow from FACTS, or answers a different question.
If FACTS is empty, the analyst consulted no tools: approve only if the draft makes no claim about the
match at all (including claims that data is missing); otherwise revise ("call the tools first").
Never use outside knowledge. Reply in JSON only."""


class Verdict(BaseModel):
    verdict: Literal["approve", "revise", "reject"]
    problems: list[str] = Field(default_factory=list)


@dataclass
class Check:
    verdict: str
    problems: list[str] = field(default_factory=list)
    by: str = "rules"  # rules | model | rules (judge unavailable)
    ms: float = 0.0
    tokens: int = 0

    @property
    def approved(self) -> bool:
        return self.verdict == "approve"

    def trace(self) -> dict[str, Any]:
        return {
            "agent": "fact_checker",
            "verdict": self.verdict,
            "problems": self.problems,
            "by": self.by,
            "ms": round(self.ms),
            "tokens": self.tokens,
        }


def facts_json(run: AgentRun) -> str:
    return ledger_facts(run.ledger)


def check_prompt(question: str, run: AgentRun) -> str:
    return f"QUESTION: {question}\n\nFACTS: {facts_json(run)}\n\nDRAFT: {run.text}"


async def fact_check(checker: Agent | None, question: str, run: AgentRun) -> Check:
    """Rules first; then the model judge when the numbers pass and a checker agent is available."""
    if run.error or not run.text:
        return Check("reject", [run.error or "empty answer"])
    if run.unsupported:
        numbers = ", ".join(run.unsupported)
        return Check("revise", [f"these numbers do not appear in any tool result: {numbers}"])
    if checker is None:
        return Check("approve", by="rules")

    start = time.perf_counter()
    try:
        response = await checker.run(check_prompt(question, run), options={"response_format": Verdict})
        verdict: Verdict = response.value  # type: ignore[assignment]
        if not isinstance(verdict, Verdict):
            verdict = Verdict.model_validate_json(response.text or "")
    except Exception as exc:  # judge down or malformed: the numbers already passed, so do not block
        return Check(
            "approve", [f"judge unavailable: {str(exc)[:120]}"], by="rules (judge unavailable)",
            ms=(time.perf_counter() - start) * 1000,
        )  # fmt: skip
    usage = response.usage_details or {}
    tokens = int(usage.get("total_token_count") or 0) if isinstance(usage, dict) else 0
    return Check(verdict.verdict, verdict.problems, "model", (time.perf_counter() - start) * 1000, tokens)
