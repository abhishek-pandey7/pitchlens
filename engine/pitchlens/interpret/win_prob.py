"""In-play win probability (§8.7).

Remaining goals for each side follow independent Poisson distributions. The scoring rate blends the
pre-match expectation with the live xG rate (the live rate earns more weight as the match goes on).
Red cards scale rates (x0.7 for the short-handed side, x1.2 for their opponents per dismissal).

`win_probability` runs in the Rust core when it is loaded (`pitchlens_core.win_probability`, a bit-for-bit
port; see engine/core/kernel/src/win_prob.rs) and here otherwise (`win_probability_py`, the reference).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pitchlens import core

MAX_GOALS = 10
LIVE_WEIGHT_AT_FULL_TIME = 0.5
RED_CARD_OWN = 0.7
RED_CARD_OPPONENT = 1.2
DEFAULT_ADDED_MINUTES = 5.0


@dataclass(frozen=True, slots=True)
class WinProbability:
    home: float
    draw: float
    away: float

    def for_team(self, is_home: bool) -> float:
        return self.home if is_home else self.away


def _poisson(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def scoring_rate_per_90(prior: float, xg: float, minutes_played: float) -> float:
    """Blend the pre-match xG/90 with the live xG/90 rate."""
    if minutes_played <= 1:
        return prior
    live = xg / minutes_played * 90
    # The live rate earns weight slowly: one early chance should not rewrite the match outlook.
    w = LIVE_WEIGHT_AT_FULL_TIME * min(minutes_played / 90, 1.0) ** 1.5
    return (1 - w) * prior + w * live


def remaining_minutes(clock_minute: float, period: int, added_minutes: float | None) -> float:
    """Minutes left including stoppage time (an estimate until the added time is announced)."""
    added = DEFAULT_ADDED_MINUTES if added_minutes is None else added_minutes
    if period == 1:
        return max(0.0, 45 - clock_minute) + 45 + added
    return max(0.0, 90 + added - clock_minute)


def win_probability(
    home_goals: int,
    away_goals: int,
    *,
    home_rate_90: float,
    away_rate_90: float,
    minutes_left: float,
    home_reds: int = 0,
    away_reds: int = 0,
) -> WinProbability:
    native = getattr(core.native, "win_probability", None)  # None without the core, or an older build
    if native is not None:
        return WinProbability(
            *native(home_goals, away_goals, home_rate_90, away_rate_90, minutes_left, home_reds, away_reds)
        )
    return win_probability_py(
        home_goals,
        away_goals,
        home_rate_90=home_rate_90,
        away_rate_90=away_rate_90,
        minutes_left=minutes_left,
        home_reds=home_reds,
        away_reds=away_reds,
    )


def win_probability_py(
    home_goals: int,
    away_goals: int,
    *,
    home_rate_90: float,
    away_rate_90: float,
    minutes_left: float,
    home_reds: int = 0,
    away_reds: int = 0,
) -> WinProbability:
    if minutes_left <= 0:
        diff = home_goals - away_goals
        return WinProbability(float(diff > 0), float(diff == 0), float(diff < 0))
    lam_h = home_rate_90 * RED_CARD_OWN**home_reds * RED_CARD_OPPONENT**away_reds * minutes_left / 90
    lam_a = away_rate_90 * RED_CARD_OWN**away_reds * RED_CARD_OPPONENT**home_reds * minutes_left / 90
    ph = [_poisson(k, lam_h) for k in range(MAX_GOALS + 1)]
    pa = [_poisson(k, lam_a) for k in range(MAX_GOALS + 1)]
    home = draw = away = 0.0
    for i, p_i in enumerate(ph):
        for j, p_j in enumerate(pa):
            diff = home_goals + i - (away_goals + j)
            p = p_i * p_j
            if diff > 0:
                home += p
            elif diff == 0:
                draw += p
            else:
                away += p
    total = home + draw + away
    return WinProbability(home / total, draw / total, away / total)
