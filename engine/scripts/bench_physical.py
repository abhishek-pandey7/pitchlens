"""Benchmark the physical metrics: Python reference against the Rust core, and a seek's catch-up.

    python scripts/bench_physical.py                 # seed 7 comeback, seek to 80'
    python scripts/bench_physical.py --seed 3 --storyline star_player --seek-min 60

Times are printed, not asserted: they depend on the machine (see docs/rust-core-plan.md §2 for targets).
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Callable
from typing import Any

from pitchlens.ingest.bus import BusMessage, MessageKind
from pitchlens.interpret import physical
from pitchlens.live.session import MatchSession
from pitchlens.live.store import MatchRecord, MatchStore


def best_of(n: int, run: Callable[[], Any]) -> float:
    times = []
    for _ in range(n):
        start = time.perf_counter()
        run()
        times.append(time.perf_counter() - start)
    return min(times)


def seek(record: MatchRecord, to_ms: int, *, frames: bool = True) -> tuple[float, float]:
    """Seconds for a session to catch up to `to_ms`, in total and in the bulk frame step."""
    assert record.tracking is not None
    session = MatchSession(record.meta, bus=None)  # type: ignore[arg-type]
    session.tracking = record.tracking if frames else None
    mid = record.match_id
    messages = [BusMessage(kind=MessageKind.CONTROL, match_id=mid, match_ms=to_ms,
                           control={"action": "reset", "to_ms": to_ms})]  # fmt: skip
    messages += [
        BusMessage(kind=MessageKind.EVENT, match_id=mid, match_ms=e.match_ms, event=e, catch_up=True)
        for e in record.events
        if e.match_ms <= to_ms
    ]
    messages.append(
        BusMessage(kind=MessageKind.CONTROL, match_id=mid, match_ms=to_ms, control={"action": "x"})
    )
    start = time.perf_counter()
    for m in messages:
        session.handle(m)
    total = time.perf_counter() - start
    frames_ms = sum(session.latency._samples.get("catch_up_frames", []))
    return total, frames_ms / 1000


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--storyline", default="comeback")
    parser.add_argument("--seek-min", type=float, default=80.0)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()

    store = MatchStore(data_dirs=[])
    start = time.perf_counter()
    record = store.generate(seed=args.seed, storyline=args.storyline)
    trk = record.tracking
    assert trk is not None
    built = time.perf_counter() - start
    print(f"match {record.match_id}: {len(record.events)} events, {len(trk)} frames, ", end="")
    print(f"{len(trk.player_ids)} players (simulated + tracked in {built:.2f} s)")
    team_of = {lp.player_id: club for club, lineup in record.meta.lineups.items() for lp in lineup}
    start = time.perf_counter()
    frames = [trk.frame(i) for i in range(len(trk))]
    print(f"Tracking.frame for every frame: {time.perf_counter() - start:.2f} s (not counted below)")
    periods = trk.period.tolist()

    def per_frame(cls: type) -> Callable[[], None]:
        def run() -> None:
            tracker = cls(record.match_id, team_of)
            for f, p in zip(frames, periods, strict=True):
                tracker.apply(f, p)

        return run

    def whole(cls: type) -> Callable[[], None]:
        def run() -> None:
            cls(record.match_id, team_of).apply_range(
                trk.ms, trk.period, trk.ball, trk.xy, trk.player_ids, 0, len(trk)
            )

        return run

    rows = [("python", "apply per frame", per_frame(physical.PhysicalTracker)),
            ("python", "apply_range, whole match", whole(physical.PhysicalTracker))]  # fmt: skip
    if physical.RUST:
        rows += [("rust", "apply per frame", per_frame(physical.RustPhysicalTracker)),
                 ("rust", "apply_range, whole match", whole(physical.RustPhysicalTracker))]  # fmt: skip
    else:
        print("pitchlens_core has no PhysicalTracker (or PITCHLENS_CORE=python): Python only")
    print(f"\nphysical metrics over the whole match (best of {args.repeat})")
    for core, what, run in rows:
        print(f"  {core:<7}{what:<28}{best_of(args.repeat, run) * 1000:9.1f} ms")

    to_ms = int(args.seek_min * 60_000)
    print(f"\nseek to {args.seek_min:g}' (session catch-up: events, then the skipped frames in bulk)")
    cores = [("rust", True), ("python", False)] if physical.RUST else [("python", False)]
    for name, rust in cores:
        saved = physical.RUST
        physical.RUST = rust
        try:
            results = [seek(record, to_ms) for _ in range(args.repeat)]
        finally:
            physical.RUST = saved
        total = statistics.median(r[0] for r in results)
        frames_s = statistics.median(r[1] for r in results)
        print(f"  {name:<7}total {total * 1000:8.1f} ms, of which frames {frames_s * 1000:7.1f} ms")
    events_only = statistics.median(seek(record, to_ms, frames=False)[0] for _ in range(args.repeat))
    print(f"  events only, no tracking (what a seek did before R-6): {events_only * 1000:.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
