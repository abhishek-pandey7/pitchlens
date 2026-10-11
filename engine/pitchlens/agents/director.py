"""Director agent (§10A, T-125): curates what goes on air on the programme feed.

The programme is one channel per match, chosen in /studio. Its cues wait at the `ProgrammeGate`
(`render/programme.py`) and the Director decides each one with tools:

    list_pending_cues, get_match_phase      read the queue, the screen and the match
    schedule_cue, hold_cue, drop_cue        act, always within the cue scheduler's rules

In **auto** mode the actions apply at once. In **approve** mode they become proposals the producer
accepts or overrides in /studio (human in the loop). The Director never touches fan feeds, and goals
and dismissals never wait for it. If it fails in auto mode, fresh cues air by the scheduler's rules.

Runs are batched: one Director turn decides every cue waiting at that moment, at most one turn at a
time per match and `DIRECTOR_MIN_GAP_S` apart, so a free-tier model can keep up.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_framework import Agent, tool

from pitchlens.clock import minute_label
from pitchlens.narrate.provider import Tier
from pitchlens.render.programme import Action, Mode, ProgrammeGate

from .clients import chat_client
from .tools import Ledger, ToolCall

if TYPE_CHECKING:
    from pitchlens.live.session import MatchSession

DIRECTOR_MIN_GAP_S = float(os.environ.get("DIRECTOR_MIN_GAP_S", "6"))

DIRECTOR_INSTRUCTIONS = """You are the PitchLens Director, producing the programme feed of a live
football broadcast. Overlay cues (captions and stat cards) wait for you before they go on air.
Call list_pending_cues and get_match_phase, then decide EVERY pending cue with one call each:
- schedule_cue: air it now. For fresh, important stories when their screen region is free.
- hold_cue: keep it for a better moment (its region is busy, or a bigger story is on air). Held cues
  come back to you; they expire after three minutes of match time.
- drop_cue: not worth air time (stale, a repeat of a story just shown, or too minor at this point).
Pace the screen: respect the per-minute limit, avoid two similar stories back to back, prefer variety
(players, tactics, numbers) and higher priority. Every call needs a short reason the producer will read
(at most 12 words). If schedule_cue is refused, hold or drop that cue instead. When every cue has a
decision, reply "done"."""


class DirectorTools:
    """The Director's view of one programme, and its levers. Every call is logged for the trace."""

    def __init__(self, gate: ProgrammeGate, session: MatchSession) -> None:
        self.gate = gate
        self.session = session
        self.ledger = Ledger()

    def _record(self, name: str, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        self.ledger.calls.append(ToolCall(name, args, result, 0.0))
        return result

    def list_pending_cues(self) -> dict[str, Any]:
        """The cues waiting for a decision (headline, template, screen region, priority, how long they
        have waited in match seconds, whether you held them) and what is on screen now."""
        now = self.gate.now_ms
        pending = [p.view(now) for p in self.gate.undecided()]
        return self._record("list_pending_cues", {}, {"pending": pending, "on_air": self.gate.on_air()})

    def get_match_phase(self) -> dict[str, Any]:
        """Minute, score, phase of the match and how recently the last goal went in."""
        s = self.session.state
        home, away = s.teams[s.home_id].name, s.teams[s.away_id].name
        minute = s.clock_ms / 60_000
        if s.finished:
            phase = "full time"
        elif s.period >= 2:
            phase = "second half, final minutes" if minute >= 80 else "second half"
        else:
            phase = "first half"
        last_goal = round((s.match_ms - s.goals[-1].match_ms) / 60_000, 1) if s.goals else None
        result = {
            "minute": minute_label(s.clock_ms, s.period),
            "score": f"{home} {s.score[0]}-{s.score[1]} {away}",
            "phase": phase,
            "minutes_since_last_goal": last_goal,
        }
        return self._record("get_match_phase", {}, result)

    def _act(self, name: str, action: Action, cue_id: str, reason: str) -> dict[str, Any]:
        if self.gate.mode is Mode.APPROVE:
            outcome = self.gate.propose(cue_id, action, reason)
        else:
            outcome = self.gate.decide(cue_id, action, by="director", reason=reason)
        return self._record(name, {"cue_id": cue_id, "reason": reason}, outcome.to_dict())

    def schedule_cue(self, cue_id: str, reason: str) -> dict[str, Any]:
        """Put a pending cue on air now (refused if its region is busy or the per-minute limit is
        reached)."""
        return self._act("schedule_cue", Action.AIR, cue_id, reason)

    def hold_cue(self, cue_id: str, reason: str) -> dict[str, Any]:
        """Keep a pending cue for a better moment; it comes back to you until it expires."""
        return self._act("hold_cue", Action.HOLD, cue_id, reason)

    def drop_cue(self, cue_id: str, reason: str) -> dict[str, Any]:
        """Do not air a pending cue."""
        return self._act("drop_cue", Action.DROP, cue_id, reason)

    def functions(self) -> list[Callable[..., Any]]:
        return [self.list_pending_cues, self.get_match_phase, self.schedule_cue, self.hold_cue, self.drop_cue]


@dataclass
class _Programme:
    running: bool = False
    seen_offers: int = 0  # gate offers already shown to a Director turn
    last_wall: float = -(10**9)


class Director:
    """Runs Director turns for every directed programme (one per match)."""

    def __init__(
        self,
        *,
        client: Callable[[str, Tier], Any] | None = None,
        on_trace: Callable[[str, dict[str, Any]], None] | None = None,
        min_gap_s: float = DIRECTOR_MIN_GAP_S,
    ) -> None:
        self._client = client or (lambda role, tier: chat_client(tier, role=role))
        self.on_trace = on_trace
        self.min_gap_s = min_gap_s
        self._programmes: dict[str, _Programme] = {}

    def configure(self, session: MatchSession, profile_id: str, mode: Mode) -> ProgrammeGate:
        gate = session.programme(profile_id, mode)
        gate.on_offer = lambda: self.wake(session)
        self._programmes.setdefault(session.match_id, _Programme())
        if gate.pending:
            self.wake(session)
        return gate

    def wake(self, session: MatchSession) -> None:
        """A cue reached the gate: start a Director loop unless one is running."""
        gate = session.gate
        programme = self._programmes.setdefault(session.match_id, _Programme())
        if gate is None or gate.mode is Mode.OFF or programme.running:
            return
        programme.running = True
        session.track(self._loop(session, programme))

    async def _loop(self, session: MatchSession, programme: _Programme) -> None:
        try:
            while (gate := session.gate) is not None and gate.mode is not Mode.OFF:
                if gate.offers <= programme.seen_offers or not gate.undecided():
                    break  # nothing new since the last turn (held cues wait for the next arrival)
                wait = programme.last_wall + self.min_gap_s - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)  # batch: cues arriving meanwhile join this turn
                programme.seen_offers = gate.offers
                programme.last_wall = time.monotonic()
                await self.turn(session, gate)
        finally:
            programme.running = False

    async def turn(self, session: MatchSession, gate: ProgrammeGate) -> dict[str, Any]:
        """One Director turn over every undecided cue."""
        start = time.perf_counter()
        presented = [p.cue.cue_id for p in gate.undecided()]
        mode = gate.mode
        tools = DirectorTools(gate, session)
        agent = Agent(
            self._client("director", Tier.FAST),
            DIRECTOR_INSTRUCTIONS,
            name="director",
            tools=[
                tool(fn, name=fn.__name__, description=(fn.__doc__ or "").strip()) for fn in tools.functions()
            ],
        )
        error, tokens = None, 0
        try:
            response = await agent.run(f"{len(presented)} cue(s) are waiting. Decide each one.")
            usage = response.usage_details or {}
            tokens = int(usage.get("total_token_count") or 0) if isinstance(usage, dict) else 0
        except Exception as exc:  # provider down, budget spent, tool-call cap
            error = str(exc)[:300]
        # Cues the Director left undecided: in auto mode the rules air them; in approve mode they wait
        # for the producer.
        if mode is Mode.AUTO:
            if error:
                gate.fallback(f"director unavailable: {error[:80]}")
            else:
                for cue_id in presented:
                    item = gate.pending.get(cue_id)
                    if item is not None and not item.held and item.proposal is None:
                        gate.decide(cue_id, Action.AIR, by="rules", reason="the Director made no decision")
        profile_id = next((c.profile.profile_id for c in session.channels.values() if c.gate is gate), "")
        calls = [{"tool": c.tool, "args": c.args, "ms": 0.0, "error": None} for c in tools.ledger.calls]
        acts = [c for c in tools.ledger.calls if c.tool in ("schedule_cue", "hold_cue", "drop_cue")]
        summary = ", ".join(
            f"{c.tool.removesuffix('_cue')} {c.args['cue_id']}: {c.result['result']}" for c in acts
        )
        trace = {
            "kind": "director",
            "question": f"Programme ({mode.value}): {len(presented)} cue(s) waiting",
            "answer": summary or (error and f"failed: {error}") or "no decisions",
            "source": mode.value,
            "verdict": "none",
            "revisions": 0,
            "lang": "en",
            "profile_id": profile_id,
            "ms": round((time.perf_counter() - start) * 1000),
            "tokens": tokens,
            "at": time.time(),
            "steps": [{
                "agent": "director", "error": error, "ms": round((time.perf_counter() - start) * 1000),
                "tokens": tokens,
                "tool_calls": calls,
            }],
        }  # fmt: skip
        if self.on_trace is not None:
            self.on_trace(session.match_id, trace)
        return trace
