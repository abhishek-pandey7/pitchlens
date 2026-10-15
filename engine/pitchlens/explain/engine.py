"""The explain stage (§9, T-30/T-32): run detectors, apply cooldowns, score salience, emit Insights."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from pitchlens.interpret.state import MatchState, timeline, tracked_timeline
from pitchlens.schema.enriched import EnrichedEvent, PhysicalAlert
from pitchlens.schema.events import MatchEvent, MatchMeta, TrackingFrame
from pitchlens.schema.futures import Futures
from pitchlens.schema.insights import Entities, Insight

from .detectors import DEFAULT_DETECTORS, Candidate, Detector
from .salience import salience

if TYPE_CHECKING:
    from pitchlens.sim.tracking import Tracking


class ExplainEngine:
    def __init__(self, state: MatchState, detectors: Iterable[type[Detector]] = DEFAULT_DETECTORS) -> None:
        self.state = state
        self.detectors = [cls(state) for cls in detectors]
        self.insights: list[Insight] = []
        self._cooldowns: dict[str, int] = {}
        self._series_seen = 0

    def on_event(self, x: EnrichedEvent) -> list[Insight]:
        """Call after `MatchState.apply`; also runs tick detectors on any new 30 s samples."""
        out: list[Insight] = []
        for d in self.detectors:
            out += self._emit(d.on_event(x), x.event.match_ms, x.event.clock_ms, x.event.period)
        while self._series_seen < len(self.state.series):
            row = self.state.series[self._series_seen]
            self._series_seen += 1
            for d in self.detectors:
                out += self._emit(d.on_tick(row), row["match_ms"], row["clock_ms"], row["period"])
        return out

    def on_alert(self, alert: PhysicalAlert) -> list[Insight]:
        clock = self.state.clock_ms + max(0, alert.match_ms - self.state.match_ms)
        out: list[Insight] = []
        for d in self.detectors:
            out += self._emit(d.on_alert(alert), alert.match_ms, clock, self.state.period)
        return out

    def on_futures(self, futures: Futures) -> list[Insight]:
        """A futures run for the match as it stands (the live session runs them off the event loop)."""
        out: list[Insight] = []
        for d in self.detectors:
            out += self._emit(d.on_futures(futures), futures.match_ms, futures.clock_ms, futures.period)
        return out

    def _emit(self, candidates: list[Candidate], match_ms: int, clock_ms: int, period: int) -> list[Insight]:
        out = []
        home, away = self.state.score
        for c in candidates:
            at_ms = c.match_ms if c.match_ms is not None else match_ms
            key = c.cooldown_key or c.headline_key
            if c.cooldown_ms and at_ms - self._cooldowns.get(key, -(10**12)) < c.cooldown_ms:
                continue
            self._cooldowns[key] = at_ms
            clock = c.clock_ms if c.clock_ms is not None else clock_ms
            insight = Insight(
                insight_id=f"i-{len(self.insights):05d}",
                match_id=self.state.match_id,
                match_ms=at_ms,
                clock_ms=clock,
                period=c.period or period,
                type=c.type,
                salience=salience(
                    c.type,
                    wp_delta=c.wp_delta,
                    rarity=c.rarity,
                    clock_minute=clock / 60_000,
                    goal_difference=home - away,
                    weight_override=c.weight_override,
                ),
                entities=Entities(teams=[t for t in c.teams if t], players=[p for p in c.players if p]),
                headline_key=c.headline_key,
                evidence=c.evidence,
                why_tags=c.why_tags,
                related_events=c.related_events,
            )
            self.insights.append(insight)
            out.append(insight)
        return out


def explain_match(
    meta: MatchMeta,
    events: Iterable[MatchEvent],
    frames: Iterable[TrackingFrame] = (),
    *,
    tracking: Tracking | None = None,
    frames_end: int | None = None,
) -> tuple[MatchState, list[Insight]]:
    """Run interpret + explain over a stored match: the same code path as the live pipeline. Pass
    `tracking` (optionally only its frames `0..frames_end`) instead of `frames` for the bulk path, which
    gives the same results without building a frame object per tick."""
    state = MatchState(meta)
    engine = ExplainEngine(state)
    items = timeline(events, frames) if tracking is None else tracked_timeline(events, tracking, frames_end)
    for item in items:
        if isinstance(item, MatchEvent):
            engine.on_event(state.apply(item))
        else:
            if isinstance(item, range):
                assert tracking is not None
                alerts = state.apply_tracking(tracking, item.start, item.stop)
            else:
                alerts = state.apply_frame(item)
            for alert in alerts:
                engine.on_alert(alert)
    return state, engine.insights
