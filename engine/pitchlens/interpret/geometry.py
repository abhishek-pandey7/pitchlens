"""Pitch geometry shared by the simulator and the analytics.

Absolute frame: origin bottom-left, the home team attacks +x in period 1 and -x in period 2.
Attacking frame: a team's own perspective, attacking the goal at x = 105.
"""

from __future__ import annotations

import math

from pitchlens.schema.common import PITCH_LENGTH, PITCH_WIDTH
from pitchlens.schema.events import Position

XY = tuple[float, float]

DEFENDERS = frozenset({Position.RB, Position.CB, Position.LB})
MIDFIELDERS = frozenset({Position.DM, Position.CM, Position.AM})
FORWARDS = frozenset({Position.RW, Position.LW, Position.ST})
UNITS = (DEFENDERS, MIDFIELDERS, FORWARDS)

FINAL_THIRD_X = PITCH_LENGTH * 2 / 3
OWN_DEFENSIVE_60_X = PITCH_LENGTH * 0.6  # PPDA counts opposition passes in their own 60%


def attacks_positive_x(is_home: bool, period: int) -> bool:
    return is_home == (period % 2 == 1)


def mirror(p: XY) -> XY:
    return (PITCH_LENGTH - p[0], PITCH_WIDTH - p[1])


def to_attacking(p: XY, *, is_home: bool, period: int) -> XY:
    """Absolute -> the team's attacking frame (and back: the transform is its own inverse)."""
    return p if attacks_positive_x(is_home, period) else mirror(p)


def distance(a: XY, b: XY) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def unit_lines(xs_by_position: list[tuple[float, Position]]) -> list[float]:
    """Mean x of each outfield unit (defence, midfield, attack) from (x, position) pairs."""
    lines = []
    for unit in UNITS:
        xs = [x for x, pos in xs_by_position if pos in unit]
        if xs:
            lines.append(sum(xs) / len(xs))
    return lines
