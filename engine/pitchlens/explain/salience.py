"""How much an insight matters (§9): type weight x win-probability impact x rarity x game state.

    salience = type_weight * (1 + 2 * |dWP|) * rarity_mult * game_state_mult, clipped to [0, 1]

- rarity in [0, 1] comes from the detector (e.g. how extreme a pass difficulty is) -> x0.75 .. x1.25
- game state: late (75'+) and close (goal difference <= 1) x1.25; a three-goal margin x0.75
Personalisation multiplies on top of this later (§11).
"""

from __future__ import annotations

from pitchlens.schema.insights import InsightType

TYPE_WEIGHTS: dict[InsightType, float] = {
    InsightType.GOAL: 0.5,
    InsightType.BIG_CHANCE: 0.55,
    InsightType.XG_SWING: 0.45,
    InsightType.WIN_PROB_SWING: 0.6,
    InsightType.MOMENTUM_SHIFT: 0.45,
    InsightType.PRESSURE_CHANGE: 0.5,
    InsightType.CONTROL_CHAOS_FLIP: 0.4,
    InsightType.RHYTHM_BREAK: 0.3,
    InsightType.PASS_OF_NOTE: 0.4,
    InsightType.SHOT_SPEED_RECORD: 0.35,
    InsightType.SPRINT_THRESHOLD: 0.35,
    InsightType.DISTANCE_MILESTONE: 0.2,
    InsightType.CAREER_MILESTONE: 0.6,
    InsightType.PLAYER_STREAK: 0.3,
    InsightType.TACTICAL_NOTE: 0.45,
    InsightType.FATIGUE_ALERT: 0.35,
    InsightType.DUEL: 0.3,
    InsightType.CARD: 0.3,
}


def game_state_multiplier(clock_minute: float, goal_difference: int) -> float:
    margin = abs(goal_difference)
    if margin >= 3:
        return 0.75
    if clock_minute >= 75 and margin <= 1:
        return 1.25
    return 1.0


def salience(
    insight_type: InsightType,
    *,
    wp_delta: float = 0.0,
    rarity: float = 0.5,
    clock_minute: float = 0.0,
    goal_difference: int = 0,
    weight_override: float | None = None,
) -> float:
    weight = TYPE_WEIGHTS[insight_type] if weight_override is None else weight_override
    rarity_mult = 0.75 + 0.5 * min(max(rarity, 0.0), 1.0)
    value = (
        weight * (1 + 2 * abs(wp_delta)) * rarity_mult * game_state_multiplier(clock_minute, goal_difference)
    )
    return round(min(max(value, 0.0), 1.0), 3)
