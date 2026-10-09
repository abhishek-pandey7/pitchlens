"""Shot model: expected goals (§8.2). Geometry is in the shooter's attacking frame (goal at x = 105)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from pitchlens.schema.common import PITCH_LENGTH, PITCH_WIDTH

from .passes import sigmoid

GOAL_X = PITCH_LENGTH
GOAL_Y = PITCH_WIDTH / 2
GOAL_WIDTH = 7.32

COEF = {
    "intercept": -1.1,
    "distance": -0.11,  # per metre to the centre of the goal
    "angle": 1.6,  # per radian of goal-mouth angle
    "header": -0.8,
    "under_pressure": -0.5,
    "one_on_one": 1.1,
}
PENALTY_XG = 0.76


@dataclass(frozen=True, slots=True)
class ShotFeatures:
    distance: float
    angle: float  # radians subtended by the goal mouth
    header: bool
    under_pressure: bool
    one_on_one: bool
    penalty: bool = False


def goal_angle(x: float, y: float) -> float:
    """Angle (radians) subtended by the two posts as seen from (x, y)."""
    dx = GOAL_X - x
    dy = y - GOAL_Y
    half = GOAL_WIDTH / 2
    a = math.atan2(dy + half, dx)
    b = math.atan2(dy - half, dx)
    return abs(a - b)


def shot_features(
    x: float, y: float, *, header: bool, under_pressure: bool, one_on_one: bool, penalty: bool = False
) -> ShotFeatures:
    return ShotFeatures(
        distance=math.hypot(GOAL_X - x, GOAL_Y - y),
        angle=goal_angle(x, y),
        header=header,
        under_pressure=under_pressure,
        one_on_one=one_on_one,
        penalty=penalty,
    )


def xg_logit(f: ShotFeatures) -> float:
    return (
        COEF["intercept"]
        + COEF["distance"] * f.distance
        + COEF["angle"] * f.angle
        + COEF["header"] * f.header
        + COEF["under_pressure"] * f.under_pressure
        + COEF["one_on_one"] * f.one_on_one
    )


def xg(f: ShotFeatures) -> float:
    if f.penalty:
        return PENALTY_XG
    return round(sigmoid(xg_logit(f)), 3)
