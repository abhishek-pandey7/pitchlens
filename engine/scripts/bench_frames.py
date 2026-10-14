"""Benchmark tracking frames as JSON (docs/rust-core-plan.md §2, R-7): Pydantic path vs the Rust core.

    python scripts/bench_frames.py            # the first stored sample (tracking rebuilt once, then cached)

Prints the whole match as frame objects and as wire JSON, one live frame, and the goal clip endpoint
(the function and the HTTP route in-process). Logged, not asserted: machine speed varies.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable
from typing import Any

from pitchlens import core
from pitchlens.config import REPO_ROOT
from pitchlens.live.clips import goal_clip_json
from pitchlens.live.store import MatchStore


def timed(fn: Callable[[], Any], repeat: int = 1) -> float:
    """Median wall time of `fn` in seconds."""
    runs = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        runs.append(time.perf_counter() - start)
    return statistics.median(runs)


def with_core(native: Any, fn: Callable[[], Any]) -> Callable[[], Any]:
    def run() -> Any:
        saved, core.native = core.native, native
        try:
            return fn()
        finally:
            core.native = saved

    return run


def main() -> int:
    rust = core.native if getattr(core.native, "frames_json", None) is not None else None
    if rust is None:
        print("pitchlens_core with frames_json is not loaded: build it with scripts/build_core.py")
        return 1
    store = MatchStore([REPO_ROOT / "data" / "samples"])
    record = store.list()[0]
    store.ensure_tracking(record)
    trk = record.tracking
    assert trk is not None
    n = len(trk)
    print(f"{record.match_id}: {n} frames, {len(trk.player_ids)} tracked players")

    def dumps(payload: Any) -> str:
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    objects = timed(lambda: [trk.frame(i) for i in range(n)])
    pydantic = timed(lambda: [dumps(trk.frame(i).model_dump(mode="json")) for i in range(n)])
    native = timed(with_core(rust, lambda: trk.frames_json()), repeat=5)
    print(f"whole match, frame objects (Tracking.frame)      {objects:7.3f} s")
    print(f"whole match, wire JSON via Pydantic              {pydantic:7.3f} s")
    print(f"whole match, wire JSON via Rust (frames_json)    {native:7.3f} s   {pydantic / native:5.1f}x")

    idx = range(1000, 3000)
    live_py = timed(lambda: [(f := trk.frame(i), dumps(f.model_dump(mode="json"))) for i in idx], repeat=3)
    live_rs = timed(with_core(rust, lambda: [trk.frame_for_stream(i) for i in idx]), repeat=3)
    print(f"live frame + its JSON, Python                    {live_py / len(idx) * 1e6:7.1f} us")
    print(f"live frame + its JSON, Rust (frame_for_stream)   {live_rs / len(idx) * 1e6:7.1f} us")

    clip_py = timed(with_core(None, lambda: goal_clip_json(record)), repeat=20)
    clip_rs = timed(with_core(rust, lambda: goal_clip_json(record)), repeat=20)
    print(f"goal clip body (71 frames), Python               {clip_py * 1000:7.2f} ms")
    print(f"goal clip body (71 frames), Rust                 {clip_rs * 1000:7.2f} ms")

    from fastapi.testclient import TestClient

    from pitchlens.api.main import create_app
    from pitchlens.live.hub import Hub

    route = f"/api/v1/matches/{record.match_id}/clips/goal"
    with TestClient(create_app(hub=Hub(store))) as client:
        assert client.get(route).status_code == 200
        http_py = timed(with_core(None, lambda: client.get(route)), repeat=20)
        http_rs = timed(with_core(rust, lambda: client.get(route)), repeat=20)
    print(f"GET .../clips/goal in-process, Python            {http_py * 1000:7.2f} ms")
    print(f"GET .../clips/goal in-process, Rust              {http_rs * 1000:7.2f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
