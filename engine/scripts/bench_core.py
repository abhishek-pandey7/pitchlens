"""Time the tracking build, Python reference against the Rust core (docs/rust-core-plan.md §2).

    python scripts/bench_core.py                     # seed 7 comeback
    python scripts/bench_core.py --storyline red_card_turn --seed 3 --repeat 10

Logs, never asserts: machine speed varies. Needs the extension for the Rust timings.
"""

from __future__ import annotations

import argparse
import statistics
import time

from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture
from pitchlens.sim.tracking import _pack, generate_tracking_py


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--storyline", default="comeback")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--repeat", type=int, default=5, help="Rust runs (the best and median are shown)")
    args = parser.parse_args()

    league = generate_league()
    home, away = default_fixture(league, args.storyline)
    match = simulate(league, home, away, seed=args.seed, storyline=build(league, args.storyline, home, away))
    print(f"{args.storyline} seed {args.seed}: {len(match.events)} events")

    t0 = time.perf_counter()
    trk = generate_tracking_py(match.meta, match.events, league)
    py_s = time.perf_counter() - t0
    print(f"python  {py_s:8.3f} s   ({len(trk)} frames x {len(trk.player_ids)} players)")

    try:
        import pitchlens_core  # type: ignore[import-not-found]
    except ImportError:
        print("rust    not built (python scripts/build_core.py)")
        return 0
    if not hasattr(pitchlens_core, "build_tracking"):
        print("rust    this pitchlens_core has no build_tracking: rebuild it")
        return 0
    pack, total = [], []
    for _ in range(args.repeat):
        t0 = time.perf_counter()
        _, packed = _pack(match.meta, match.events, league)
        t1 = time.perf_counter()
        pitchlens_core.build_tracking(**packed)
        t2 = time.perf_counter()
        pack.append(t1 - t0)
        total.append(t2 - t0)
    best, med, pack_ms = min(total), statistics.median(total), min(pack) * 1000
    print(f"rust    {best:8.3f} s   best of {args.repeat} (median {med:.3f} s; packing {pack_ms:.1f} ms)")
    print(f"speed-up {py_s / best:.0f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
