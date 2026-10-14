"""Overlay cues (§12, T-60): turn a ranked insight into a timed, machine-readable instruction for a
broadcast renderer, and schedule cues per fan so the screen never piles up.

Scheduling rules (all in match time, so a renderer can align cues with the video clock):
- one cue per region at a time; a higher-priority cue preempts a lower one, otherwise it is dropped
- at most `max_overlays_per_min` cues in any 60 s of match time, except priority >= 90 (goals, red cards)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from pitchlens.schema.insights import Insight, InsightType
from pitchlens.schema.overlays import CueAnchor, CueTemplate, CueText, OverlayCue, Region
from pitchlens.schema.profiles import Profile

T = InsightType
ALWAYS_SHOW_PRIORITY = 90
RATE_WINDOW_MS = 60_000


@dataclass(frozen=True)
class CueSpec:
    template: CueTemplate
    region: Region
    priority: int
    duration_ms: int
    anchored: bool = False


SPECS: dict[InsightType, CueSpec] = {
    T.GOAL: CueSpec(CueTemplate.LOWER_THIRD, Region.LOWER_THIRD, 100, 9000),
    T.CARD: CueSpec(CueTemplate.LOWER_THIRD, Region.LOWER_THIRD, 75, 6000),
    T.BIG_CHANCE: CueSpec(CueTemplate.LOWER_THIRD, Region.LOWER_THIRD, 65, 6000),
    T.CAREER_MILESTONE: CueSpec(CueTemplate.LOWER_THIRD, Region.LOWER_THIRD, 60, 7000),
    T.WIN_PROB_SWING: CueSpec(CueTemplate.WP_SWING, Region.TOP_LEFT, 70, 7000),
    T.PASS_OF_NOTE: CueSpec(CueTemplate.PASS_CARD, Region.TOP_RIGHT, 60, 6000),
    T.SHOT_SPEED_RECORD: CueSpec(CueTemplate.SHOT_SPEED, Region.TOP_RIGHT, 55, 5000),
    T.SPRINT_THRESHOLD: CueSpec(CueTemplate.PLAYER_TAG, Region.PLAYER_ANCHOR, 50, 5000, anchored=True),
    T.DISTANCE_MILESTONE: CueSpec(CueTemplate.PLAYER_TAG, Region.PLAYER_ANCHOR, 30, 4000, anchored=True),
    T.PLAYER_STREAK: CueSpec(CueTemplate.PLAYER_TAG, Region.PLAYER_ANCHOR, 35, 5000, anchored=True),
    T.MOMENTUM_SHIFT: CueSpec(CueTemplate.MOMENTUM_BAR, Region.FULL_BAR, 45, 8000),
    T.PRESSURE_CHANGE: CueSpec(CueTemplate.LOWER_THIRD, Region.LOWER_THIRD, 50, 7000),
    T.CONTROL_CHAOS_FLIP: CueSpec(CueTemplate.CHAOS_METER, Region.TOP_LEFT, 40, 7000),
    T.XG_SWING: CueSpec(CueTemplate.LOWER_THIRD, Region.TOP_LEFT, 40, 6000),
    T.RHYTHM_BREAK: CueSpec(CueTemplate.LOWER_THIRD, Region.LOWER_THIRD, 35, 5000),
    T.TACTICAL_NOTE: CueSpec(CueTemplate.LOWER_THIRD, Region.LOWER_THIRD, 45, 7000),
    T.FATIGUE_ALERT: CueSpec(CueTemplate.PLAYER_TAG, Region.PLAYER_ANCHOR, 40, 5000, anchored=True),
    T.DUEL: CueSpec(CueTemplate.LOWER_THIRD, Region.LOWER_THIRD, 35, 5000),
    T.FUTURES_SWING: CueSpec(CueTemplate.FUTURES_BAR, Region.FULL_BAR, 55, 8000),
}
DEFAULT_SPEC = CueSpec(CueTemplate.LOWER_THIRD, Region.LOWER_THIRD, 30, 5000)


def cue_data(insight: Insight) -> dict[str, Any]:
    """Evidence as a flat dict for the renderer: value, or {from, to} for changes."""
    data: dict[str, Any] = {}
    for e in insight.evidence:
        key = f"{e.metric}:{e.team}" if e.team and e.metric in data else e.metric
        if e.from_ is not None or e.to is not None:
            data[key] = {"from": e.from_, "to": e.to}
        elif e.value is not None:
            data[key] = e.value
    return data


def build_cue(
    insight: Insight, profile: Profile, *, headline: str, body: str = "", source: str = "template"
) -> OverlayCue:
    spec = SPECS.get(insight.type, DEFAULT_SPEC)
    if insight.type is T.CARD and insight.headline_key != "card.yellow":
        spec = CueSpec(spec.template, spec.region, 95, 8000)  # dismissals always make air
    anchor = None
    region = spec.region
    if spec.anchored:
        if insight.entities.players:
            anchor = CueAnchor(player_id=insight.entities.players[0])
        else:
            region = Region.LOWER_THIRD  # nobody to anchor to: show it as a caption instead
    return OverlayCue(
        cue_id=f"c-{insight.insight_id}-{profile.profile_id}",
        insight_id=insight.insight_id,
        profile_id=profile.profile_id,
        match_id=insight.match_id,
        start_ms=insight.match_ms,
        duration_ms=spec.duration_ms,
        priority=spec.priority,
        region=region,
        template=spec.template,
        anchor=anchor,
        data=cue_data(insight),
        text=CueText(lang=profile.language, headline=headline, body=body, source=source),
    )


class CueScheduler:
    """Per-fan screen manager: region occupancy, preemption and rate limiting in match time."""

    def __init__(self, max_per_min: int) -> None:
        self.max_per_min = max_per_min
        self.regions: dict[Region, tuple[int, int]] = {}  # region -> (busy until ms, priority)
        self.recent: deque[int] = deque()
        self.dropped = 0
        self.last_refusal = ""

    def admit(self, cue: OverlayCue) -> bool:
        now = cue.start_ms
        while self.recent and now - self.recent[0] >= RATE_WINDOW_MS:
            self.recent.popleft()
        forced = cue.priority >= ALWAYS_SHOW_PRIORITY
        if not forced and len(self.recent) >= self.max_per_min:
            self.dropped += 1
            self.last_refusal = f"rate limit: {self.max_per_min} cues a minute already on air"
            return False
        busy_until, busy_priority = self.regions.get(cue.region, (0, 0))
        if cue.region is not Region.PLAYER_ANCHOR and now < busy_until and cue.priority <= busy_priority:
            self.dropped += 1
            self.last_refusal = (
                f"{cue.region.value} busy for {round((busy_until - now) / 1000)} more seconds "
                f"with a cue of priority {busy_priority}"
            )
            return False
        self.regions[cue.region] = (now + cue.duration_ms, cue.priority)
        self.recent.append(now)
        self.last_refusal = ""
        return True
