"""Rate-shift detection for count data: has a team's pressing (or any per-exposure rate) changed?

A sliding two-window generalised likelihood-ratio test. With counts c and exposure e in a reference
window A (e.g. the previous 20 minutes) and a recent window B (the last 10), the Poisson log-likelihood
ratio of "two different rates" against "one shared rate" is

    LLR = cA * ln(rA / p) + cB * ln(rB / p),   rA = cA / eA, rB = cB / eB, p = (cA + cB) / (eA + eB)

(the exposure terms cancel). We report a shift when the LLR is large and the rate ratio is meaningful.
Comparing adjacent windows of independent per-minute counts was far more reliable on our synthetic
league than CUSUM on rolling averages, whose overlapping windows are heavily autocorrelated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RateShift:
    direction: int  # +1 increase, -1 decrease
    before: float  # rate per unit exposure in the reference window
    after: float  # rate per unit exposure in the recent window
    llr: float


def poisson_llr(ca: float, ea: float, cb: float, eb: float) -> float:
    p = (ca + cb) / (ea + eb)
    llr = 0.0
    if ca:
        llr += ca * math.log((ca / ea) / p)
    if cb:
        llr += cb * math.log((cb / eb) / p)
    return llr


def rate_shift(
    ca: float,
    ea: float,
    cb: float,
    eb: float,
    *,
    min_llr: float = 3.0,
    min_ratio: float = 1.6,
    min_exposure: float = 15,
) -> RateShift | None:
    """Compare a reference window (a) with a recent window (b); None if there is no meaningful shift."""
    if min(ea, eb) < min_exposure or ca + cb == 0 or ca == 0:
        return None
    before, after = ca / ea, cb / eb
    llr = poisson_llr(ca, ea, cb, eb)
    ratio = after / before
    if llr < min_llr or 1 / min_ratio < ratio < min_ratio:
        return None
    return RateShift(1 if ratio > 1 else -1, before, after, llr)
