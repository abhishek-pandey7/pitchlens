"""Full tracking parity check: the Rust core against the Python reference (docs/rust-core-plan.md §5).

    python scripts/parity_core.py                       # stored samples + 20 seeds x 5 storylines
    python scripts/parity_core.py --seeds 4             # a quicker run
    python scripts/parity_core.py --data ../data/matches --data ../data/samples

For every match it builds tracking both ways and checks identical `ms`, `period` and `player_ids`, the
same NaN pattern in `xy` (who is on the pitch) and max abs difference <= 1e-3 m for ball and players.
It prints one line per match (max diffs, whether the floats are bit-identical, both timings) and a
summary, and exits non-zero on any mismatch. Needs the extension (`python scripts/build_core.py`).
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pitchlens.live.store import load_match_dir
from pitchlens.schema.events import MatchEvent, MatchMeta
from pitchlens.sim.rosters import League, generate_league, load_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import STORYLINES, build, default_fixture
from pitchlens.sim.tracking import Tracking, _pack, generate_tracking_py

REPO = Path(__file__).resolve().parents[2]
TOLERANCE = 1e-3


@dataclass
class Result:
    name: str
    ok: bool
    problem: str
    ball_diff: float
    xy_diff: float
    exact: bool
    py_s: float
    rs_s: float
    frames: int


def build_rust(meta: MatchMeta, events: list[MatchEvent], league: League) -> Tracking:
    import pitchlens_core  # type: ignore[import-not-found]

    player_ids, packed = _pack(meta, events, league)
    ms, period, ball, xy = pitchlens_core.build_tracking(**packed)
    return Tracking(match_id=meta.match_id, ms=ms, period=period, ball=ball, player_ids=player_ids, xy=xy)


def compare(name: str, meta: MatchMeta, events: list[MatchEvent], league: League) -> Result:
    t0 = time.perf_counter()
    py = generate_tracking_py(meta, events, league)
    t1 = time.perf_counter()
    rs = build_rust(meta, events, league)
    t2 = time.perf_counter()
    problems = []
    if rs.player_ids != py.player_ids:
        problems.append("player_ids")
    for field in ("ms", "period", "ball", "xy"):
        a, b = getattr(py, field), getattr(rs, field)
        if (a.dtype, a.shape) != (b.dtype, b.shape):
            problems.append(f"{field} dtype/shape {b.dtype}{b.shape} != {a.dtype}{a.shape}")
    ball_diff = xy_diff = float("inf")
    exact = False
    if not problems:
        if not np.array_equal(rs.ms, py.ms):
            problems.append("ms")
        if not np.array_equal(rs.period, py.period):
            problems.append("period")
        off = np.isnan(py.xy)
        if not np.array_equal(np.isnan(rs.xy), off):
            problems.append("NaN mask")
        ball_diff = float(np.abs(rs.ball - py.ball).max(initial=0.0))
        xy_diff = (
            float(np.abs(rs.xy[~off] - py.xy[~off]).max(initial=0.0))
            if "NaN mask" not in problems
            else xy_diff
        )
        exact = np.array_equal(rs.ball, py.ball) and np.array_equal(rs.xy, py.xy, equal_nan=True)
        if ball_diff > TOLERANCE:
            problems.append(f"ball diff {ball_diff:.3g}")
        if xy_diff > TOLERANCE:
            problems.append(f"xy diff {xy_diff:.3g}")
    return Result(
        name, not problems, ", ".join(problems), ball_diff, xy_diff, exact, t1 - t0, t2 - t1, len(py)
    )


def stored_matches(dirs: list[Path]):
    league = load_league()
    seen: set[str] = set()
    for root in dirs:
        for match_dir in sorted(p for p in root.glob("*") if p.is_dir()):
            record = load_match_dir(match_dir)
            if record is None or record.match_id in seen:
                continue
            seen.add(record.match_id)
            yield f"stored {record.match_id}", record.meta, record.events, league


def generated_matches(seeds: int):
    league = generate_league()
    for storyline in STORYLINES:
        home, away = default_fixture(league, storyline)
        spec = build(league, storyline, home, away)
        for k in range(seeds):
            seed = 1 + k * 7  # spread out, and includes 1, 8, 15, ...
            match = simulate(league, home, away, seed=seed, storyline=spec)
            yield f"{storyline} seed {seed}", match.meta, match.events, league


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data", type=Path, action="append", help="stored match directories (repeatable)")
    parser.add_argument("--seeds", type=int, default=20, help="generated seeds per storyline (default 20)")
    args = parser.parse_args()
    dirs = args.data or [REPO / "data" / "matches", REPO / "data" / "samples"]

    results: list[Result] = []
    header = ("match", "ok", "frames", "ball max", "xy max", "exact", "python", "rust")
    print("{:34} {:3} {:>6} {:>9} {:>9} {:5} {:>7} {:>7}".format(*header))
    for name, meta, events, league in [*stored_matches(dirs), *generated_matches(args.seeds)]:
        try:
            r = compare(name, meta, events, league)
        except (StopIteration, KeyError, ValueError) as exc:  # clubs or players outside this league
            print(f"{name:34} skipped: {type(exc).__name__} {exc}")
            continue
        results.append(r)
        print(
            f"{r.name:34} {'ok' if r.ok else 'NO':3} {r.frames:6d} {r.ball_diff:9.2g} {r.xy_diff:9.2g} "
            f"{'yes' if r.exact else 'no':5} {r.py_s:6.2f}s {r.rs_s * 1000:5.1f}ms"
            + (f"  {r.problem}" if r.problem else ""),
            flush=True,
        )

    bad = [r for r in results if not r.ok]
    if not results:
        print("no matches checked")
        return 1
    n_stored = sum(r.name.startswith("stored") for r in results)
    n_exact = sum(r.exact for r in results)
    ball_max, xy_max = max(r.ball_diff for r in results), max(r.xy_diff for r in results)
    py_t, rs_t = [r.py_s for r in results], [r.rs_s for r in results]
    speedup = statistics.median(p / r for p, r in zip(py_t, rs_t, strict=True))
    print()
    print(f"matches: {len(results)} ({n_stored} stored), mismatches: {len(bad)}, bit-identical: {n_exact}")
    print(f"max diff: ball {ball_max:.3g} m, xy {xy_max:.3g} m (tolerance {TOLERANCE:g} m)")
    print(f"python: median {statistics.median(py_t):.2f} s, total {sum(py_t):.1f} s")
    rs_med, rs_max = statistics.median(rs_t) * 1000, max(rs_t) * 1000
    print(f"rust:   median {rs_med:.1f} ms, max {rs_max:.1f} ms, total {sum(rs_t):.2f} s (packing included)")
    print(f"speed-up: median {speedup:.0f}x")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
