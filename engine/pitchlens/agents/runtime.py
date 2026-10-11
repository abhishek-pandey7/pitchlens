"""Building and running Agent Framework agents over the analyst tools (§10A, T-120).

`run_agent` executes one agent turn, records every tool call in a Ledger, and checks the answer: each
number it states must be backed by a tool result (or the question itself). This is the agent-side
version of the narration grounding validator, which the Fact-Checker agent builds on.
"""

from __future__ import annotations

import functools
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_framework import Agent, tool

from pitchlens.narrate.grounding import numbers_in, unsupported_numbers

from .tools import Ledger, MatchTools, ToolError

ANALYST_INSTRUCTIONS = """You are the PitchLens Analyst, a football data analyst for a live match.
Rules:
- Every number you state must come from a tool result in this conversation. Call tools first; never
  guess or use outside knowledge. If the tools cannot answer, say so.
- Probabilities and shares are given as 0..1; write them as percentages (0.62 -> 62%).
- For pressing intensity use press_rate (higher = more pressing); PPDA (lower = more pressing) is
  noisy over short windows. Momentum is home minus away, from -1 to 1.
- Call each tool at most once with the same arguments. If a tool reports data as unavailable, say so
  plainly instead of retrying.
- Be concise: at most three sentences, naming the metric behind each claim."""


def _soft(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Return bad arguments or missing data as a normal result. If the call raised instead, models
    tend to retry the same call until the framework disables tools, and some providers then reject
    the request."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ToolError as exc:
            return {"unavailable": str(exc)}

    return wrapper


def agent_tools(tools: MatchTools) -> list[Any]:
    """Wrap the analyst tools as Agent Framework function tools (schemas from the type hints)."""
    return [
        tool(_soft(fn), name=fn.__name__, description=(fn.__doc__ or "").strip()) for fn in tools.functions()
    ]


def build_agent(client: Any, tools: MatchTools, *, name: str, instructions: str) -> Agent:
    return Agent(client, instructions, name=name, tools=agent_tools(tools))


@dataclass
class AgentRun:
    agent: str
    question: str
    text: str
    ledger: Ledger
    ms: float
    tokens: int = 0
    unsupported: list[str] = field(default_factory=list)
    error: str | None = None
    closed: bool = False  # answered by the no-tools closing call after the tool loop failed

    @property
    def grounded(self) -> bool:
        return self.error is None and bool(self.text) and not self.unsupported

    def trace(self) -> dict[str, Any]:
        """A JSON-able record of the run for the Agent Trace panel and telemetry."""
        return {
            "agent": self.agent,
            "question": self.question,
            "answer": self.text,
            "grounded": self.grounded,
            "unsupported_numbers": self.unsupported,
            "error": self.error,
            "closed": self.closed,
            "ms": round(self.ms),
            "tokens": self.tokens,
            "tool_calls": [
                {"tool": c.tool, "args": c.args, "ms": round(c.ms, 1), "error": c.error}
                for c in self.ledger.calls
            ],
        }


# Some models append citation markers such as "【compare_periods】"; the trace already shows the tools.
_CITATION = re.compile(r"\s*【[^】]*】")


def _collect(value: Any, out: set[float]) -> None:
    """Numbers in a tool result. Sibling values in one object also allow their differences
    ("press rate rose 0.66" from start/end), but unrelated numbers never combine."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        out.add(float(value))
        if -1 <= value <= 1:  # shares and probabilities are written as percentages
            out.add(round(float(value) * 100, 6))
    elif isinstance(value, str):  # scorelines "2-1", minutes "90+4'", evidence "0.052→0.963"
        for n in numbers_in(value):
            _collect(n, out)
    elif isinstance(value, dict):
        siblings = [
            float(v) for v in value.values() if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        for i, a in enumerate(siblings):
            for b in siblings[i + 1 :]:
                diff = round(abs(a - b), 6)
                out.add(diff)
                if abs(a) <= 1 and abs(b) <= 1:
                    out.add(round(diff * 100, 6))
        for v in value.values():
            _collect(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect(v, out)


def supported_numbers(ledger: Ledger, question: str = "") -> set[float]:
    """Every number an answer may state: values from tool results and numbers the fan asked about."""
    allowed: set[float] = set(numbers_in(question))
    for result in ledger.results():
        _collect(result, allowed)
    return allowed


async def run_agent(agent: Agent, tools: MatchTools, question: str, *, keep_ledger: bool = False) -> AgentRun:
    """One agent turn; the answer is checked against what the tools returned. The ledger starts empty
    unless `keep_ledger` (a revision round may cite facts it gathered in the first round)."""
    if not keep_ledger:
        tools.ledger.calls.clear()
    start = time.perf_counter()
    closed = False
    try:
        response = await agent.run(question)
    except Exception as exc:  # provider down, budget spent, tool-call cap hit, malformed tool call
        error = str(exc)[:300]
        response = None
        if tools.ledger.results():
            # The facts gathered so far are still good. Ask once more with no tools attached, so the
            # model must answer from them (forcing tool_choice=none is rejected by some providers
            # when the model calls a tool anyway).
            try:
                closer = Agent(agent.client, CLOSING_INSTRUCTIONS, name=agent.name)
                response = await closer.run(f"QUESTION: {question}\n\nFACTS: {ledger_facts(tools.ledger)}")
                closed = True
            except Exception as closing_exc:
                error = f"{error} | closing call: {str(closing_exc)[:150]}"
        if response is None:
            ms = (time.perf_counter() - start) * 1000
            return AgentRun(
                agent.name or "agent", question, "", Ledger(list(tools.ledger.calls)), ms, error=error
            )
    ms = (time.perf_counter() - start) * 1000
    text = _CITATION.sub("", response.text or "").strip()
    usage = response.usage_details or {}
    tokens = int(usage.get("total_token_count") or 0) if isinstance(usage, dict) else 0
    run = AgentRun(agent.name or "agent", question, text, Ledger(list(tools.ledger.calls)), ms, tokens)
    run.closed = closed
    run.unsupported = unsupported_numbers(text, supported_numbers(run.ledger, question))
    return run


CLOSING_INSTRUCTIONS = """You are the PitchLens Analyst. Answer the QUESTION using only FACTS, the tool
results already gathered. Every number must come from FACTS; 0..1 values may be written as
percentages. If FACTS cannot answer it, say so. At most three sentences."""

FACTS_MAX_CHARS = 6000  # free-tier friendly; tool results are compact already


def ledger_facts(ledger: Ledger, max_chars: int = FACTS_MAX_CHARS) -> str:
    """The successful tool calls of a run as compact JSON (truncated for free tiers)."""
    facts = [{"tool": c.tool, "args": c.args, "result": c.result} for c in ledger.calls if c.error is None]
    text = json.dumps(facts, ensure_ascii=False, default=str, separators=(",", ":"))
    return text if len(text) <= max_chars else text[:max_chars] + "…(truncated)"
