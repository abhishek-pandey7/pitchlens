"""Pitch geometry for the simulator: formation anchors, dynamic team shape and coordinate frames.

Two frames are used:
- **absolute**: the published event frame (home attacks +x in period 1, -x in period 2);
- **team frame**: a team's own perspective, own goal at x = 0, attacking towards x = 105.
Mirroring (x, y) -> (105 - x, 68 - y) converts between a team frame and the opponent's frame.
"""

from __future__ import annotations

from pitchlens.schema.common import PITCH_LENGTH, PITCH_WIDTH
from pitchlens.schema.events import Position

XY = tuple[float, float]

CENTRE: XY = (PITCH_LENGTH / 2, PITCH_WIDTH / 2)

# Mid-block anchors in team frame, aligned slot-by-slot with rosters.FORMATION_STARTERS.
# Facing +x, the right flank is low y.
FORMATION_ANCHORS: dict[str, list[XY]] = {
    "4-3-3": [(5, 34), (30, 8), (26, 25), (26, 43), (30, 60),
              (40, 34), (50, 22), (50, 46), (68, 10), (72, 34), (68, 58)],
    "4-2-3-1": [(5, 34), (30, 8), (26, 25), (26, 43), (30, 60),
                (42, 26), (42, 42), (62, 10), (60, 34), (62, 58), (74, 34)],
    "4-4-2": [(5, 34), (30, 8), (26, 25), (26, 43), (30, 60),
              (55, 10), (48, 26), (48, 42), (55, 58), (70, 28), (70, 40)],
    "3-5-2": [(5, 34), (26, 18), (24, 34), (26, 50), (48, 6),
              (40, 34), (52, 24), (52, 44), (48, 62), (72, 28), (72, 40)],
}  # fmt: skip

DEFENDERS = {Position.RB, Position.CB, Position.LB}
MIDFIELDERS = {Position.DM, Position.CM, Position.AM}
FORWARDS = {Position.RW, Position.LW, Position.ST}


# Units never push or drop beyond these lines (team frame): attackers stay roughly onside against a
# deep block, and a back four never sits on its own goal line.
POSSESSION_MAX_X = {"def": 72.0, "mid": 86.0, "fwd": 92.0}
DEFENDING_MIN_X = {"def": 9.0, "mid": 16.0, "fwd": 24.0}


def _unit(pos: Position) -> str:
    if pos in DEFENDERS:
        return "def"
    return "mid" if pos in MIDFIELDERS else "fwd"


def mirror(p: XY) -> XY:
    return (PITCH_LENGTH - p[0], PITCH_WIDTH - p[1])


def clamp(p: XY, margin: float = 0.5) -> XY:
    return (
        min(max(p[0], margin), PITCH_LENGTH - margin),
        min(max(p[1], margin), PITCH_WIDTH - margin),
    )


def team_shape(
    anchors: list[XY],
    positions: list[Position],
    ball: XY,
    *,
    in_possession: bool,
    press: float,
    width: float,
) -> list[XY]:
    """Where each player of a team stands, in that team's frame, given the ball (also team frame)."""
    bx, by = ball
    shape: list[XY] = []
    for (ax, ay), pos in zip(anchors, positions, strict=True):
        if pos is Position.GK:
            x = 5 + 0.12 * (bx - CENTRE[0])
            shape.append(clamp((min(max(x, 2.0), 20.0), CENTRE[1] + 0.15 * (by - CENTRE[1]))))
            continue
        if in_possession:
            x = ax + 0.55 * (bx - CENTRE[0]) + 6
            x = min(x, POSSESSION_MAX_X[_unit(pos)])
            y = CENTRE[1] + (ay - CENTRE[1]) * (0.85 + 0.3 * width) + 0.2 * (by - CENTRE[1])
        else:
            x = ax * 0.85 + 0.5 * (bx - CENTRE[0]) + 8 * press - 3
            x = max(x, DEFENDING_MIN_X[_unit(pos)])
            y = CENTRE[1] + (ay - CENTRE[1]) * 0.75 + 0.3 * (by - CENTRE[1])
        shape.append(clamp((x, y), margin=1.0))
    return shape


def unit_lines(shape: list[XY], positions: list[Position]) -> list[float]:
    """Mean x of each outfield unit (defence, midfield, attack) in the same frame as `shape`."""
    lines = []
    for unit in (DEFENDERS, MIDFIELDERS, FORWARDS):
        xs = [p[0] for p, pos in zip(shape, positions, strict=True) if pos in unit]
        if xs:
            lines.append(sum(xs) / len(xs))
    return lines
