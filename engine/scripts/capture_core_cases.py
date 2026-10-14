"""Capture reference values from the Python implementation for the Rust core's cargo tests.

    python scripts/capture_core_cases.py

Writes, under engine/core/tests/data: shape_cases.txt (`pitch.team_shape` for every formation x
possession x press x width over several ball positions), ball_cases.txt (`BallPath.at` over real match
events plus interrupted flights and repeated timestamps) and hypot_cases.txt (`math.hypot` and
`np.hypot`). Floats are written with
`repr`, so the Rust side reads back exactly the same doubles. Rerun after changing either function.
"""

from __future__ import annotations

import itertools
import math
from pathlib import Path

import numpy as np

from pitchlens.schema.common import Point
from pitchlens.schema.events import EventType, Height, MatchEvent, Position
from pitchlens.sim.pitch import FORMATION_ANCHORS, _unit, team_shape
from pitchlens.sim.rosters import FORMATION_STARTERS, generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture
from pitchlens.sim.tracking import BallPath

OUT = Path(__file__).resolve().parents[1] / "core" / "tests" / "data"
UNIT_CODE = {"def": 1, "mid": 2, "fwd": 3}

PRESS = (0.0, 0.35, 1.0)
WIDTH = (0.2, 0.9)
BALLS = ((52.5, 34.0), (10.0, 5.0), (100.0, 60.0), (70.3, 22.1), (0.0, 68.0))


def unit_code(pos: Position) -> int:
    return 0 if pos is Position.GK else UNIT_CODE[_unit(pos)]


def shape_cases() -> list[str]:
    lines = [
        "# case <formation> <in_possession> <press> <width> <ball x> <ball y>",
        "# slot <anchor x> <anchor y> <unit: 0 GK, 1 def, 2 mid, 3 fwd> <expected x> <expected y>",
    ]
    grid = itertools.product(FORMATION_ANCHORS.items(), (True, False), PRESS, WIDTH, BALLS)
    for (formation, anchors), in_possession, press, width, (bx, by) in grid:
        slots = FORMATION_STARTERS[formation]
        shape = team_shape(anchors, slots, (bx, by), in_possession=in_possession, press=press, width=width)
        lines.append(f"case {formation} {int(in_possession)} {press!r} {width!r} {bx!r} {by!r}")
        for (ax, ay), pos, (x, y) in zip(anchors, slots, shape, strict=True):
            lines.append(f"slot {float(ax)!r} {float(ay)!r} {unit_code(pos)} {x!r} {y!r}")
    return lines


def synthetic_events() -> list[MatchEvent]:
    """Interrupted flights (high and low), repeated timestamps and an event without a location."""

    def ev(seq: int, ms: int, start, end=None, duration=None, height=None) -> MatchEvent:
        return MatchEvent(
            event_id=f"e{seq}",
            match_id="CASES",
            seq=seq,
            type=EventType.PASS,
            period=1,
            match_ms=ms,
            clock_ms=ms,
            start=Point(x=start[0], y=start[1]) if start else None,
            end=Point(x=end[0], y=end[1]) if end else None,
            duration_ms=duration,
            height=height,
        )

    return [
        ev(0, 0, (52.5, 34.0), (80.0, 10.0), 1800, Height.HIGH),
        ev(1, 1200, (70.0, 18.0), (60.0, 30.0), 700),  # interrupts the high ball after its apex
        ev(2, 1900, (60.0, 30.0)),  # same time as the previous arrival
        ev(3, 1900, (61.0, 31.0), (20.0, 60.0), 3000, Height.HIGH),
        ev(4, 2500, None),  # no location: ignored
        ev(5, 2600, (40.0, 44.0), (40.0, 44.0), 0),  # zero duration: no flight
        ev(6, 2600, (41.0, 45.0), (90.0, 2.0), 1001, Height.HIGH),  # odd duration: apex at a half ms
        ev(7, 3101, (66.0, 20.0)),  # lands exactly on that apex
    ]


def ball_cases() -> list[str]:
    league = generate_league()
    home, away = default_fixture(league, "comeback")
    match = simulate(league, home, away, seed=7, storyline=build(league, "comeback", home, away))
    sets = {"comeback-300": match.events[:300], "synthetic": synthetic_events()}
    lines = [
        "# set <name>",
        "# event <match_ms> <start x> <start y> <end x> <end y> <duration_ms> <high>  (nan = missing)",
        "# query <n>   then n lines: at <ms> <expected x> <expected y> <expected z>",
    ]
    for name, events in sets.items():
        lines.append(f"set {name}")
        for e in events:
            sx, sy = (e.start.x, e.start.y) if e.start else (float("nan"), float("nan"))
            ex, ey = (e.end.x, e.end.y) if e.end else (float("nan"), float("nan"))
            high = int(e.height is Height.HIGH)
            lines.append(f"event {e.match_ms} {sx!r} {sy!r} {ex!r} {ey!r} {e.duration_ms or 0} {high}")
        path = BallPath(events)
        last = int(path.t[-1])
        # More queries than sample points, and fewer (NumPy has taken different code paths for each).
        dense = np.arange(-1000, last + 1000, 250, dtype=np.float64)
        ends = np.array([-5.0, path.t[-1] + 5.0, (path.t[0] + path.t[-1]) / 2])
        sparse = np.concatenate([path.t[:3], ends])
        for query in (dense, sparse):
            x, y, z = path.at(query)
            lines.append(f"query {len(query)}")
            rows = zip(query, x, y, z, strict=True)
            lines += [f"at {float(q)!r} {float(a)!r} {float(b)!r} {float(c)!r}" for q, a, b, c in rows]
    return lines


def hypot_cases() -> list[str]:
    """`math.hypot` (CPython's own algorithm: the ball's apex height) and `np.hypot` (the C library's
    hypot: player steps) over pitch-sized and awkward inputs."""
    rng = np.random.default_rng(12)
    pairs = [(3.0, 4.0), (0.0, 0.0), (-0.0, 5e-324), (1e-310, 3e-310), (1e300, 1e300), (-7.5, 0.0)]
    pairs += [tuple(map(float, p)) for p in rng.uniform(-110, 110, size=(400, 2))]
    pairs += [tuple(map(float, p)) for p in rng.uniform(-1e-3, 1e-3, size=(100, 2))]
    lines = ["# hypot <x> <y> <math.hypot> <np.hypot>"]
    lines += [f"hypot {x!r} {y!r} {math.hypot(x, y)!r} {float(np.hypot(x, y))!r}" for x, y in pairs]
    return lines


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = (
        ("shape_cases.txt", shape_cases()),
        ("ball_cases.txt", ball_cases()),
        ("hypot_cases.txt", hypot_cases()),
    )
    for name, lines in files:
        (OUT / name).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print(OUT / name, len(lines), "lines")


if __name__ == "__main__":
    main()
