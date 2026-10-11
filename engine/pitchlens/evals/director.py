"""Director eval: pacing and curation on a directed programme versus the scheduler's rules alone (§10A).

The second half of the comeback is replayed twice on the same programme feed (the tactician profile):

    rules     the gate open: cues air by the scheduler's rules (region, priority, rate limit)
    director  the Director in auto mode decides every non-urgent cue

Measured from what actually went on air:
- rate-limit compliance: the most cues in any 60 s of match time, against the profile's limit
- must-show delay: goals and red cards should air at the moment they happen
- back-to-back repeats: two aired cues of the same insight type within 3 match minutes
- the decision mix (aired, held, dropped, expired, refused) and the Director's turn latency and tokens
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter, deque
from itertools import pairwise
from typing import Any

from pitchlens.agents.director import Director
from pitchlens.live.hub import Hub
from pitchlens.live.store import MatchStore
from pitchlens.render.cues import ALWAYS_SHOW_PRIORITY, RATE_WINDOW_MS
from pitchlens.render.programme import Mode
from pitchlens.schema.overlays import OverlayCue

from .common import ProviderUnavailable, Results, provider_error

SUITE = "director"
PROFILE = "p-tactician-en"
FROM_MIN = 45
REPEAT_WINDOW_MS = 3 * 60_000


async def programme_run(
    mode: Mode, *, speed: float, from_min: int = FROM_MIN, client: Any = None, min_gap_s: float | None = None
) -> dict[str, Any]:
    """Replay the comeback from `from_min` with the programme in `mode`; return what aired and why."""
    hub = Hub(MatchStore(data_dirs=[]), narrate=False)
    traces: list[dict[str, Any]] = []
    director = Director(client=client, on_trace=lambda _match, trace: traces.append(trace))
    if min_gap_s is not None:
        director.min_gap_s = min_gap_s
    record = hub.store.generate(seed=7, storyline="comeback", tracking=False)
    aired: list[tuple[OverlayCue, int]] = []  # (cue, match_ms when it aired)
    try:
        session = hub.session(record.match_id)
        gate = director.configure(session, PROFILE, mode)
        gate.log = deque(maxlen=None)  # keep every decision for the eval
        emit = gate.emit

        def record_air(cue: OverlayCue) -> None:
            aired.append((cue, session.state.match_ms))
            emit(cue)

        gate.emit = record_air
        start = time.monotonic()
        await hub.start_replay(record.match_id, speed=speed, frames=False, from_ms=from_min * 60_000)
        while not session.state.finished and time.monotonic() - start < 3600:
            await asyncio.sleep(0.2)
        await session.drain_narrations()
        types = {i.insight_id: i.type.value for i in session.engine.insights}
        limit = gate.scheduler.max_per_min
        log = list(gate.log)
        wall = time.monotonic() - start
    finally:
        await hub.shutdown()

    # The rate limit governs everything but goals and red cards, which always air.
    starts = [cue.start_ms for cue, _ in aired if cue.priority < ALWAYS_SHOW_PRIORITY]
    peak = max((sum(1 for t in starts if s <= t < s + RATE_WINDOW_MS) for s in starts), default=0)
    urgent = [(c, at) for c, at in aired if c.priority >= ALWAYS_SHOW_PRIORITY]
    seq = [
        (types.get(c.insight_id or "", "?"), c.start_ms)
        for c, _ in aired
        if c.priority < ALWAYS_SHOW_PRIORITY
    ]
    repeats = sum(1 for (t1, s1), (t2, s2) in pairwise(seq) if t1 == t2 and s2 - s1 < REPEAT_WINDOW_MS)
    turn_ms = sorted(t["ms"] for t in traces)
    return {
        "mode": mode.value,
        "speed": speed,
        "from_min": from_min,
        "profile": PROFILE,
        "limit_per_min": limit,
        "aired": len(aired),
        "aired_types": dict(Counter(types.get(c.insight_id or "", "?") for c, _ in aired)),
        "peak_per_min": peak,
        "urgent": len(urgent),
        "urgent_max_delay_ms": max((at - c.start_ms for c, at in urgent), default=0),
        "repeats": repeats,
        "decisions": dict(Counter(f"{e['by']}:{e['result']}" for e in log)),
        "turns": len(traces),
        "turn_failures": sum(1 for t in traces if t["steps"][0]["error"]),
        "turn_ms_p50": turn_ms[len(turn_ms) // 2] if turn_ms else 0,
        "turn_ms_p95": turn_ms[max(0, int(len(turn_ms) * 0.95) - 1)] if turn_ms else 0,
        "tokens": sum(t["tokens"] for t in traces),
        "reasons": [e["reason"] for e in log if e["by"] == "director"][:12],
        "wall_s": round(wall),
        "provider_errors": [t["steps"][0]["error"] for t in traces if provider_error(t["steps"][0]["error"])],
    }


async def run(*, speed: float = 6.0, fresh: bool = False, client: Any = None) -> Results:
    results = Results(SUITE, fresh=fresh)
    for mode in (Mode.OFF, Mode.AUTO):
        case_id = f"{mode.value}-x{speed:g}"
        if case_id in results.done:
            continue
        row = await programme_run(mode, speed=speed, client=client)
        if row["provider_errors"]:  # a cap or outage mid-replay would score as Director failures
            raise ProviderUnavailable(row["provider_errors"][0])
        del row["provider_errors"]
        results.add({"case_id": case_id, **row})
    results.write_meta(match="comeback-7", profile=PROFILE, from_min=FROM_MIN)
    return results
