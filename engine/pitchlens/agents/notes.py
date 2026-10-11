"""Analyst tactical notes (§10A, T-122): agent-authored insights in the live feed.

The rule detectors fire on clear, pre-defined signals. The Analyst agent looks for the pattern behind
them, and its note joins the live feed as a `tactical_note` insight (`author="agent:analyst"`), so it
is personalised, narrated in each fan's language and can go on air like any other insight.

Sequential pattern: trigger → Analyst (tools) → number check → Fact-Checker judge → publish.

Triggers: half-time, and high-salience tactical moments (momentum, pressing, control, xG and win
probability swings). To stay inside free-tier model limits, notes are spaced at least
`NOTE_MIN_MATCH_MIN` match minutes and `NOTE_MIN_GAP_S` real seconds apart, at most `NOTE_MAX` per
match and one at a time, and never while a seek is catching up. An unapproved note is dropped; the
trace records why.

Groq does not combine tool calls with structured output, so the Analyst replies with JSON text that is
parsed and validated here. The Fact-Checker has no tools and uses strict structured output.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agent_framework import Agent
from pydantic import BaseModel, Field, ValidationError

from pitchlens.clock import minute_label
from pitchlens.narrate.grounding import unsupported_numbers
from pitchlens.narrate.provider import Tier
from pitchlens.schema.events import EventType, MatchEvent
from pitchlens.schema.insights import Entities, Evidence, Insight, InsightType

from .clients import chat_client
from .factcheck import FACT_CHECKER_INSTRUCTIONS, fact_check
from .runtime import AgentRun, build_agent, run_agent, supported_numbers
from .tools import MatchTools, MatchView, ToolError

if TYPE_CHECKING:
    from pitchlens.live.session import MatchSession

NOTE_MAX = int(os.environ.get("NOTE_MAX", "5"))
NOTE_MIN_MATCH_MIN = float(os.environ.get("NOTE_MIN_MATCH_MIN", "10"))
NOTE_MIN_GAP_S = float(os.environ.get("NOTE_MIN_GAP_S", "45"))
MAX_REVISIONS = 1
NOTE_SALIENCE = 0.4  # tactical insights score 0.37-0.75; 0.4 keeps the meaningful ones
NOTE_KEY = "tactical_note.agent"
TRIGGER_TYPES = {
    InsightType.MOMENTUM_SHIFT,
    InsightType.PRESSURE_CHANGE,
    InsightType.CONTROL_CHAOS_FLIP,
    InsightType.XG_SWING,
    InsightType.WIN_PROB_SWING,
}

TACTICAL_INSTRUCTIONS = """You are the PitchLens Analyst writing one tactical note for a live broadcast.
Use the tools (compare_periods, get_window, get_metric, get_insights, get_match_state) to find ONE
pattern behind the current phase of play that viewers might miss: pressing, territory, control, tempo
or chance quality. Compare recent minutes with an earlier stretch.
Rules:
- Every number must come from a tool result. Name the metric behind it. Do not restate the score or
  describe goals: the broadcast already shows them.
- If no pattern is clearly supported by the tools, set "found" to false.
- Write probabilities and shares as percentages.
Reply with JSON only, no other text:
{"found": true, "team": "<team name the note is about>", "note": "<at most two sentences>"}"""


class TacticalNote(BaseModel):
    found: bool
    team: str = ""
    note: str = Field(default="", max_length=400)


_JSON = re.compile(r"\{.*\}", re.DOTALL)


def parse_note(text: str) -> TacticalNote | None:
    """The JSON note; a plain-prose reply counts as the note itself (it is still checked and judged)."""
    text = (text or "").strip()
    match = _JSON.search(text)
    if match:
        try:
            return TacticalNote.model_validate(json.loads(match.group(0)))
        except (ValueError, ValidationError):
            return None
    if len(text) >= 20:
        return TacticalNote(found=True, note=text[:400])
    return None


def note_enabled() -> bool:
    mode = os.environ.get("AGENT_NOTES", "auto").lower()
    if mode in ("on", "1", "true"):
        return True
    if mode in ("off", "0", "false"):
        return False
    provider = os.environ.get("LLM_PROVIDER", "mock")
    return provider != "mock" and bool(
        os.environ.get("GROQ_API_KEY")
        or os.environ.get("AZURE_OPENAI_ENDPOINT")
        or os.environ.get("OPENAI_COMPAT_BASE_URL")
    )


@dataclass
class _MatchNotes:
    count: int = 0
    last_match_ms: int = -(10**9)
    last_wall: float = -(10**9)
    running: bool = False


@dataclass
class NoteOutcome:
    published: Insight | None
    trace: dict[str, Any] = field(default_factory=dict)


class TacticalNotes:
    """Watches live sessions and adds agent-authored tactical notes to their feeds."""

    def __init__(
        self,
        *,
        client: Callable[[str, Tier], Any] | None = None,
        on_trace: Callable[[str, dict[str, Any]], None] | None = None,
        min_gap_s: float = NOTE_MIN_GAP_S,
        min_match_min: float = NOTE_MIN_MATCH_MIN,
        max_notes: int = NOTE_MAX,
    ) -> None:
        self._client = client or (lambda role, tier: chat_client(tier, role=role))
        self.on_trace = on_trace
        self.min_gap_s, self.min_match_min, self.max_notes = min_gap_s, min_match_min, max_notes
        self._matches: dict[str, _MatchNotes] = {}

    # ------------------------------------------------------------------ triggers
    def trigger(self, session: MatchSession, insights: list[Insight], event: MatchEvent | None) -> str | None:
        """Why a note should be written now, or None (cheap; called for every live event)."""
        notes = self._matches.setdefault(session.match_id, _MatchNotes())
        state = session.state
        if notes.running or notes.count >= self.max_notes or state.finished:
            return None
        if state.match_ms - notes.last_match_ms < self.min_match_min * 60_000:
            return None
        if time.monotonic() - notes.last_wall < self.min_gap_s:
            return None
        if event is not None and event.type == EventType.PERIOD_END and event.period == 1:
            return f"half-time ({minute_label(state.clock_ms, state.period)})"
        for insight in insights:
            if insight.type in TRIGGER_TYPES and insight.salience >= NOTE_SALIENCE:
                return f"{insight.headline_key} at {minute_label(insight.clock_ms, insight.period)}"
        return None

    def observe(self, session: MatchSession, insights: list[Insight], event: MatchEvent | None) -> None:
        reason = self.trigger(session, insights, event)
        if reason is None:
            return
        notes = self._matches[session.match_id]
        notes.running = True
        notes.last_match_ms = session.state.match_ms
        notes.last_wall = time.monotonic()
        session.track(self._run(session, reason))

    # ------------------------------------------------------------------ the agents
    async def _run(self, session: MatchSession, reason: str) -> None:
        notes = self._matches[session.match_id]
        try:
            outcome = await self.write(
                lambda: MatchView(session.state, session.engine.insights), reason, number=notes.count + 1
            )
            if outcome.published is not None:
                notes.count += 1
                session.add_insight(outcome.published)
            if self.on_trace:
                self.on_trace(session.match_id, outcome.trace)
        finally:
            notes.running = False

    async def write(self, view: Callable[[], MatchView], reason: str, *, number: int = 1) -> NoteOutcome:
        """One note: Analyst with tools → number check → Fact-Checker. Returns the insight if approved."""
        start = time.perf_counter()
        tools = MatchTools(view)
        state = view().state
        minute = minute_label(state.clock_ms, state.period)
        analyst = build_agent(
            self._client("tactical", Tier.FAST), tools, name="analyst", instructions=TACTICAL_INSTRUCTIONS
        )
        prompt = f"It is {minute}. Trigger: {reason}. Find one tactical pattern for the broadcast."
        run = await run_agent(analyst, tools, prompt)
        steps: list[dict[str, Any]] = [{**run.trace(), "agent": "analyst"}]
        trace: dict[str, Any] = {
            "kind": "tactical_note",
            "question": f"Tactical note: {reason}",
            "steps": steps,
        }
        revisions = 0

        def done(answer: str, source: str, verdict: str, published: Insight | None = None) -> NoteOutcome:
            tokens = sum(int(s.get("tokens") or 0) for s in steps)
            trace.update(
                answer=answer, source=source, verdict=verdict, revisions=revisions, lang="en",
                ms=round((time.perf_counter() - start) * 1000), tokens=tokens, at=time.time(),
                profile_id="broadcast",
            )  # fmt: skip
            return NoteOutcome(published, trace)

        checker = Agent(self._client("checker", Tier.FAST), FACT_CHECKER_INSTRUCTIONS, name="fact_checker")
        while True:
            note = parse_note(run.text)
            if run.error or note is None:
                return done(run.error or f"unreadable reply: {run.text[:160]!r}", "dropped", "none")
            if not note.found or not note.note.strip():
                return done("no clear pattern right now", "dropped", "none")
            # Check the note itself (not the JSON around it) against the tool results.
            checked = AgentRun("analyst", prompt, note.note.strip(), run.ledger, run.ms, run.tokens)
            checked.unsupported = _unsupported(checked)
            check = await fact_check(checker, prompt, checked)
            steps.append(check.trace())
            if check.approved:
                return done(checked.text, "published", "approve", self._insight(tools, note, number))
            if check.verdict != "revise" or revisions >= MAX_REVISIONS:
                return done(checked.text, "dropped", check.verdict)
            # Reflection: one more round with the Fact-Checker's problems, keeping the facts gathered.
            revisions += 1
            retry = (
                f"{prompt}\n\nYour previous note was: {checked.text}\nThe fact-checker found these problems: "
                f"{'; '.join(check.problems)}. Write a corrected note, using tool results only."
            )
            run = await run_agent(analyst, tools, retry, keep_ledger=True)
            steps.append({**run.trace(), "agent": "analyst", "round": revisions + 1})

    def _insight(self, tools: MatchTools, note: TacticalNote, number: int) -> Insight:
        state = tools.state  # the state now, when the note is published
        try:
            teams = [tools.resolve_team(note.team)]
        except ToolError:
            teams = []
        text = note.note.strip()
        players = [pid for pid, p in state.players.items() if p.name in text]
        return Insight(
            insight_id=f"{state.match_id}-note-{number}",
            match_id=state.match_id,
            match_ms=state.match_ms,
            clock_ms=state.clock_ms,
            period=state.period,
            type=InsightType.TACTICAL_NOTE,
            salience=0.6,
            entities=Entities(teams=teams, players=players[:3]),
            headline_key=NOTE_KEY,
            evidence=[
                Evidence(metric="note", value=text, team=teams[0] if teams else None, source="agent:analyst")
            ],
            why_tags=["agent_analysis"],
            author="agent:analyst",
        )


def _unsupported(run: AgentRun) -> list[str]:
    return unsupported_numbers(run.text, supported_numbers(run.ledger, run.question))
