"""The programme gate (§10A Director, T-125): what goes on air on the directed feed.

Fan feeds stay fully deterministic: the cue scheduler airs cues the moment an insight lands. One channel
per match can be made the **programme**, the feed a studio puts on air. Its cues then pass through this
gate, where the Director agent (or the producer) decides:

    off      the gate is open: cues air through the scheduler as on any feed
    auto     cues wait for the Director; its decisions apply at once, within the scheduler's rules
    approve  the Director proposes; the producer airs, holds or drops (human in the loop)

Goals and dismissals (priority >= 90) never wait. A pending cue expires after `ttl_ms` of match time: a
story that missed its moment is not aired late. Every decision is logged for the studio.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pitchlens.clock import minute_label
from pitchlens.schema.overlays import OverlayCue, Region

from .cues import ALWAYS_SHOW_PRIORITY, RATE_WINDOW_MS, CueScheduler

PENDING_TTL_MS = 3 * 60_000
LOG_KEEP = 40


class Mode(StrEnum):
    OFF = "off"
    AUTO = "auto"
    APPROVE = "approve"


class Action(StrEnum):
    AIR = "air"
    HOLD = "hold"
    DROP = "drop"


@dataclass
class Pending:
    cue: OverlayCue
    offered_ms: int  # match time when the cue reached the gate
    held: bool = False
    proposal: Action | None = None  # the Director's proposal (approve mode)
    reason: str = ""

    def view(self, now_ms: int) -> dict[str, Any]:
        return {
            "cue_id": self.cue.cue_id,
            "insight_id": self.cue.insight_id,
            "template": self.cue.template.value,
            "region": self.cue.region.value,
            "priority": self.cue.priority,
            "headline": self.cue.text.headline,
            "waiting_s": round((now_ms - self.offered_ms) / 1000),
            "held": self.held,
            "proposal": self.proposal.value if self.proposal else None,
            "reason": self.reason,
        }


@dataclass
class Outcome:
    ok: bool
    result: str  # aired | refused | held | dropped | proposed | expired | unknown
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "result": self.result, "detail": self.detail}


@dataclass
class ProgrammeGate:
    """Pending cues for the programme feed, and the decisions taken on them."""

    scheduler: CueScheduler
    emit: Callable[[OverlayCue], None]  # sends an aired cue down the channel
    clock: Callable[[], tuple[int, int, int]]  # (match_ms, clock_ms, period) now
    mode: Mode = Mode.OFF
    ttl_ms: int = PENDING_TTL_MS
    on_offer: Callable[[], None] | None = None  # wakes the Director
    offers: int = 0  # cues that have waited at the gate so far (the Director batches by this)
    pending: dict[str, Pending] = field(default_factory=dict)
    log: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=LOG_KEEP))
    aired: deque[tuple[int, str]] = field(default_factory=lambda: deque(maxlen=20))  # (match_ms, headline)

    # ------------------------------------------------------------------ intake
    def offer(self, cue: OverlayCue) -> None:
        """A cue the scheduler's rules would consider. Open gate or must-show: air now. Else: wait."""
        if self.mode is Mode.OFF or cue.priority >= ALWAYS_SHOW_PRIORITY:
            outcome = self._air(cue)
            if self.mode is not Mode.OFF:
                self._log(cue, Action.AIR, "rules", "always on air", outcome)
            return
        self.expire()
        self.pending[cue.cue_id] = Pending(cue, self.now_ms)
        self.offers += 1
        if self.on_offer is not None:
            self.on_offer()

    def reset(self) -> None:
        """A seek: pending cues belong to a moment that no longer exists."""
        self.pending.clear()
        self.aired.clear()

    def set_mode(self, mode: Mode) -> None:
        self.mode = mode
        if mode is Mode.OFF:  # opening the gate releases what is still fresh
            for cue_id in list(self.pending):
                self.decide(cue_id, Action.AIR, by="rules", reason="director switched off")

    # ------------------------------------------------------------------ decisions
    def decide(self, cue_id: str, action: Action, *, by: str, reason: str = "") -> Outcome:
        """Apply a decision now (auto mode, the producer, or the rules)."""
        self.expire()
        item = self.pending.get(cue_id)
        if item is None:
            return Outcome(False, "unknown", f"no pending cue {cue_id!r} (aired, dropped or expired)")
        if action is Action.HOLD:
            item.held, item.reason = True, reason
            outcome = Outcome(True, "held", "stays pending until it airs, is dropped or expires")
        elif action is Action.DROP:
            del self.pending[cue_id]
            outcome = Outcome(True, "dropped")
        else:
            outcome = self._air(item.cue)
            if outcome.ok:
                del self.pending[cue_id]
        self._log(item.cue, action, by, reason, outcome)
        return outcome

    def propose(self, cue_id: str, action: Action, reason: str) -> Outcome:
        """Approve mode: the Director's decision waits for the producer."""
        self.expire()
        item = self.pending.get(cue_id)
        if item is None:
            return Outcome(False, "unknown", f"no pending cue {cue_id!r}")
        if item.proposal is not None:  # one proposal per cue: the producer is already looking at it
            detail = f"already proposed: {item.proposal.value}; the producer decides"
            return Outcome(False, "proposed", detail)
        item.proposal, item.reason = action, reason
        outcome = Outcome(True, "proposed", "the producer decides")
        self._log(item.cue, action, "director", reason, outcome)
        return outcome

    def approve(self, cue_id: str) -> Outcome:
        """The producer accepts the Director's proposal."""
        item = self.pending.get(cue_id)
        if item is None or item.proposal is None:
            return Outcome(False, "unknown", f"no proposal for {cue_id!r}")
        return self.decide(cue_id, item.proposal, by="producer", reason=f"approved: {item.reason}")

    def fallback(self, reason: str) -> None:
        """The Director could not decide: fresh cues go through the scheduler's rules."""
        for cue_id, item in list(self.pending.items()):
            if item.proposal is None and not item.held:
                self.decide(cue_id, Action.AIR, by="rules", reason=reason)

    def expire(self) -> None:
        now = self.now_ms
        for cue_id, item in list(self.pending.items()):
            if now - item.offered_ms > self.ttl_ms:
                del self.pending[cue_id]
                self._log(item.cue, Action.DROP, "rules", "missed its moment", Outcome(True, "expired"))

    # ------------------------------------------------------------------ views
    @property
    def now_ms(self) -> int:
        return self.clock()[0]

    def undecided(self) -> list[Pending]:
        """Cues the Director has not ruled on yet (held cues are re-offered to it)."""
        self.expire()
        return [p for p in self.pending.values() if p.proposal is None]

    def on_air(self) -> dict[str, Any]:
        """What the screen looks like now: busy regions, the rate budget and recent stories."""
        now = self.now_ms
        busy = {
            region.value: round((until - now) / 1000)
            for region, (until, _) in self.scheduler.regions.items()
            if until > now and region is not Region.PLAYER_ANCHOR
        }
        recent = sum(1 for t in self.scheduler.recent if now - t < RATE_WINDOW_MS)
        return {
            "busy_regions_s": busy,
            "cues_last_minute": recent,
            "max_per_minute": self.scheduler.max_per_min,
            "recently_aired": [
                {"minutes_ago": round((now - t) / 60_000, 1), "headline": h} for t, h in reversed(self.aired)
            ][:5],
        }

    def snapshot(self) -> dict[str, Any]:
        now = self.now_ms
        self.expire()
        return {
            "mode": self.mode.value,
            "pending": [p.view(now) for p in self.pending.values()],
            "on_air": self.on_air(),
            "log": list(self.log),
        }

    # ------------------------------------------------------------------ internals
    def _air(self, cue: OverlayCue) -> Outcome:
        match_ms = self.now_ms
        live = cue.model_copy(update={"start_ms": max(cue.start_ms, match_ms)})  # aired now, not back-dated
        if not self.scheduler.admit(live):
            return Outcome(False, "refused", self.scheduler.last_refusal)
        self.emit(live)
        self.aired.append((live.start_ms, live.text.headline))
        return Outcome(True, "aired")

    def _log(self, cue: OverlayCue, action: Action, by: str, reason: str, outcome: Outcome) -> None:
        _, clock_ms, period = self.clock()
        self.log.appendleft({
            "at": time.time(), "minute": minute_label(clock_ms, period), "cue_id": cue.cue_id,
            "headline": cue.text.headline, "action": action.value, "by": by, "reason": reason,
            **outcome.to_dict(),
        })  # fmt: skip
