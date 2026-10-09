"""Pass quality model: distance, lines broken and a 0-100 difficulty rating (§8.1).

All geometry is in the passer's *attacking frame*: the passer attacks the goal at x = 105.
The simulator samples completions from this same model, which keeps the synthetic data and the
analytics consistent with each other.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pitchlens.schema.common import PITCH_LENGTH, PITCH_WIDTH

BOX_X = PITCH_LENGTH - 16.5
BOX_Y_MIN = PITCH_WIDTH / 2 - 20.16
BOX_Y_MAX = PITCH_WIDTH / 2 + 20.16

# Logistic coefficients for P(complete). Hand-set to give realistic completion rates
# (~80% overall, ~95% for short safe passes, ~45% for long balls); refit later on simulated data.
COEF = {
    "intercept": 3.0,
    "distance": -0.055,  # per metre
    "forward": -0.5,  # scaled by the forward component of the pass direction (0..1)
    "backward": 0.3,  # bonus for passes that go backwards (0..1)
    "under_pressure": -0.7,
    "lines_broken": -0.45,  # per opposition line crossed
    "aerial": -0.6,
    "into_box": -0.8,
}


@dataclass(frozen=True, slots=True)
class PassFeatures:
    distance: float
    forward: float  # dx / distance, in [-1, 1]
    under_pressure: bool
    lines_broken: int
    aerial: bool
    into_box: bool


def distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def in_box(x: float, y: float) -> bool:
    return x >= BOX_X and BOX_Y_MIN <= y <= BOX_Y_MAX


def lines_broken(start_x: float, end_x: float, line_xs: list[float]) -> int:
    """Number of opposition lines (mean x of each unit, attacking frame) the pass travels past."""
    lo, hi = sorted((start_x, end_x))
    if end_x <= start_x:
        return 0
    return sum(1 for lx in line_xs if lo < lx < hi)


def pass_features(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    under_pressure: bool,
    lines: list[float],
    aerial: bool,
) -> PassFeatures:
    d = distance(*start, *end)
    forward = (end[0] - start[0]) / d if d > 0 else 0.0
    return PassFeatures(
        distance=d,
        forward=forward,
        under_pressure=under_pressure,
        lines_broken=lines_broken(start[0], end[0], lines),
        aerial=aerial,
        into_box=in_box(*end) and not in_box(*start),
    )


def completion_logit(f: PassFeatures) -> float:
    return (
        COEF["intercept"]
        + COEF["distance"] * f.distance
        + COEF["forward"] * max(f.forward, 0.0)
        + COEF["backward"] * max(-f.forward, 0.0)
        + COEF["under_pressure"] * f.under_pressure
        + COEF["lines_broken"] * f.lines_broken
        + COEF["aerial"] * f.aerial
        + COEF["into_box"] * f.into_box
    )


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def completion_probability(f: PassFeatures) -> float:
    return sigmoid(completion_logit(f))


def difficulty(f: PassFeatures) -> float:
    """0 (trivial) to 100 (near impossible): 100 x (1 - P(complete))."""
    return round(100.0 * (1.0 - completion_probability(f)), 1)
