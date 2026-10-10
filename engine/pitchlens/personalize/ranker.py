"""Per-fan ranking (§11, T-56): how much does this insight matter to *this* fan?

    personal = salience x persona_weight[type] x club x player x focus_metric x player_focus   (clipped to 1)

A fan sees an insight in their feed above the persona's feed threshold, and as an overlay above the
(higher) overlay threshold. Spoiler-safe fans still receive score-revealing moments, flagged so the
client can hide them until tapped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pitchlens.schema.insights import Insight, InsightType
from pitchlens.schema.profiles import FocusMetric, Persona, Profile

T = InsightType
PERSONA_WEIGHTS: dict[Persona, dict[InsightType, float]] = {
    Persona.CASUAL: {
        T.GOAL: 1.3, T.CARD: 1.0, T.BIG_CHANCE: 1.0, T.CAREER_MILESTONE: 1.0, T.SPRINT_THRESHOLD: 1.1,
        T.SHOT_SPEED_RECORD: 1.0, T.WIN_PROB_SWING: 0.8, T.PASS_OF_NOTE: 0.7, T.PLAYER_STREAK: 0.6,
        T.MOMENTUM_SHIFT: 0.6, T.PRESSURE_CHANGE: 0.4, T.CONTROL_CHAOS_FLIP: 0.4, T.XG_SWING: 0.4,
        T.RHYTHM_BREAK: 0.3, T.DISTANCE_MILESTONE: 0.5,
    },
    Persona.ANALYST: {T.PASS_OF_NOTE: 1.2, T.XG_SWING: 1.2, T.WIN_PROB_SWING: 1.1, T.DISTANCE_MILESTONE: 0.8},
    Persona.TACTICIAN: {
        T.PRESSURE_CHANGE: 1.4, T.CONTROL_CHAOS_FLIP: 1.3, T.MOMENTUM_SHIFT: 1.2, T.RHYTHM_BREAK: 1.2,
        T.TACTICAL_NOTE: 1.3, T.PASS_OF_NOTE: 1.1, T.XG_SWING: 1.1, T.SPRINT_THRESHOLD: 0.7,
        T.DISTANCE_MILESTONE: 0.5, T.PLAYER_STREAK: 0.8, T.CAREER_MILESTONE: 0.7,
    },
    Persona.KIDS: {
        T.GOAL: 1.4, T.SPRINT_THRESHOLD: 1.3, T.SHOT_SPEED_RECORD: 1.3, T.CAREER_MILESTONE: 1.1,
        T.PRESSURE_CHANGE: 0.2, T.CONTROL_CHAOS_FLIP: 0.2, T.RHYTHM_BREAK: 0.2, T.XG_SWING: 0.2,
    },
    Persona.FANTASY: {
        T.GOAL: 1.3, T.BIG_CHANCE: 1.1, T.CARD: 1.1, T.CAREER_MILESTONE: 1.1, T.PLAYER_STREAK: 1.0,
        T.PRESSURE_CHANGE: 0.5, T.CONTROL_CHAOS_FLIP: 0.4, T.RHYTHM_BREAK: 0.3,
    },
}  # fmt: skip
PERSONA_WEIGHTS[Persona.ACCESSIBILITY] = PERSONA_WEIGHTS[Persona.CASUAL]

FEED_THRESHOLD = {
    Persona.CASUAL: 0.4, Persona.ANALYST: 0.12, Persona.TACTICIAN: 0.2,
    Persona.KIDS: 0.45, Persona.FANTASY: 0.3, Persona.ACCESSIBILITY: 0.4,
}  # fmt: skip
OVERLAY_MARGIN = 0.1  # overlays need this much more than the feed

FOCUS_METRICS: dict[FocusMetric, set[str]] = {
    FocusMetric.PASS_DIFFICULTY: {"pass_difficulty", "assist_pass_difficulty"},
    FocusMetric.TOP_SPEED: {"top_speed", "sprint_count"},
    FocusMetric.XG: {"xg", "xg_difference"},
    FocusMetric.PPDA: {"ppda", "pressing_actions_per_10_opp_passes"},
    FocusMetric.CONTROL_CHAOS: {"control_index"},
}
SPOILER_TYPES = {T.GOAL, T.WIN_PROB_SWING}

CLUB_BOOST, PLAYER_BOOST, FOCUS_BOOST, PLAYER_FOCUS_BOOST = 1.6, 2.0, 1.5, 3.0


@dataclass(frozen=True)
class Decision:
    personal_salience: float
    feed: bool
    overlay: bool
    spoiler: bool = False
    reasons: list[str] = field(default_factory=list)


def rank(insight: Insight, profile: Profile) -> Decision:
    reasons = []
    score = insight.salience * PERSONA_WEIGHTS.get(profile.persona, {}).get(insight.type, 1.0)
    if set(insight.entities.teams) & set(profile.favorite_clubs):
        score *= CLUB_BOOST
        reasons.append("favorite_club")
    if set(insight.entities.players) & set(profile.favorite_players):
        score *= PLAYER_BOOST
        reasons.append("favorite_player")
    if profile.focus_metric and {e.metric for e in insight.evidence} & FOCUS_METRICS[profile.focus_metric]:
        score *= FOCUS_BOOST
        reasons.append("focus_metric")
    focus = profile.player_focus_mode
    if focus.enabled and focus.player_id and focus.player_id in insight.entities.players:
        score *= PLAYER_FOCUS_BOOST
        reasons.append("player_focus")
    score = round(min(score, 1.0), 3)
    threshold = FEED_THRESHOLD.get(profile.persona, 0.3)
    return Decision(
        personal_salience=score,
        feed=score >= threshold,
        overlay=score >= threshold + OVERLAY_MARGIN and profile.max_overlays_per_min > 0,
        spoiler=profile.spoiler_safe and insight.type in SPOILER_TYPES,
        reasons=reasons,
    )
