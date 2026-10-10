import asyncio

import pytest

from pitchlens.ingest.bus import BusMessage, MessageKind
from pitchlens.ingest.memory_bus import MemoryBus
from pitchlens.sim.replay import MAX_FRAME_MSGS_PER_SEC, Replay, ReplayState
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.tracking import generate_tracking

LEAGUE = generate_league()
MATCH = simulate(LEAGUE, "NHR", "SVC", seed=21)
TRACKING = generate_tracking(MATCH.meta, MATCH.events, LEAGUE)


class FakeClock:
    """Deterministic time: sleeping advances the clock instantly."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


async def collect(
    bus: MemoryBus, match_id: str, sink: list[tuple[float, BusMessage]], clock: FakeClock
) -> None:
    async with bus.subscribe(match_id) as sub:
        async for message in sub:
            sink.append((clock(), message))
            if message.kind is MessageKind.CONTROL and message.control == {"action": "finished"}:
                return


@pytest.mark.parametrize("speed", [1, 10, 60])
async def test_events_published_in_order_at_requested_speed(speed: int) -> None:
    bus, clock = MemoryBus(), FakeClock()
    got: list[tuple[float, BusMessage]] = []
    consumer = asyncio.create_task(collect(bus, MATCH.meta.match_id, got, clock))
    await asyncio.sleep(0)
    start = clock()
    await Replay(bus, MATCH.meta, MATCH.events, speed=speed, clock=clock, sleep=clock.sleep).run()
    await consumer
    events = [(t, m.event) for t, m in got if m.kind is MessageKind.EVENT]
    assert got[0][1].kind is MessageKind.META
    assert [e.seq for _, e in events] == [e.seq for e in MATCH.events]
    for t, e in events:
        assert t - start == pytest.approx(e.match_ms / 1000 / speed, abs=1e-6)


async def test_frames_are_thinned_but_events_never_dropped() -> None:
    bus, clock = MemoryBus(), FakeClock()
    got: list[tuple[float, BusMessage]] = []
    consumer = asyncio.create_task(collect(bus, MATCH.meta.match_id, got, clock))
    await asyncio.sleep(0)
    await Replay(bus, MATCH.meta, MATCH.events, TRACKING, speed=60, clock=clock, sleep=clock.sleep).run()
    await consumer
    frames = [t for t, m in got if m.kind is MessageKind.FRAME]
    assert sum(m.kind is MessageKind.EVENT for _, m in got) == len(MATCH.events)
    wall = got[-1][0] - got[0][0]
    assert len(frames) <= wall * MAX_FRAME_MSGS_PER_SEC + 2
    assert len(frames) > wall * MAX_FRAME_MSGS_PER_SEC * 0.8


async def test_pause_resume_and_seek() -> None:
    bus, clock = MemoryBus(), FakeClock()
    got: list[tuple[float, BusMessage]] = []
    consumer = asyncio.create_task(collect(bus, MATCH.meta.match_id, got, clock))
    await asyncio.sleep(0)
    replay = Replay(bus, MATCH.meta, MATCH.events, speed=10, clock=clock, sleep=clock.sleep)
    runner = asyncio.create_task(replay.run())
    while replay.match_ms < 600_000:  # let ten minutes of match time play
        await asyncio.sleep(0)
    replay.pause()
    assert replay.state is ReplayState.PAUSED
    paused_at = replay.match_ms
    before = replay.published
    for _ in range(50):
        await asyncio.sleep(0)
    assert replay.published == before  # nothing published while paused
    replay.seek(3_000_000)  # 50:00 of match time
    for _ in range(50):
        await asyncio.sleep(0)
    resets = [
        m for _, m in got if m.kind is MessageKind.CONTROL and m.control and m.control["action"] == "reset"
    ]
    assert len(resets) == 1
    catch_up = [m.event for _, m in got if m.catch_up and m.event]
    assert catch_up[0].seq == 0 and catch_up[-1].match_ms <= 3_000_000
    replay.resume()
    await runner
    await consumer
    assert paused_at >= 600_000
    live_after = [
        m.event
        for _, m in got[got.index(next(x for x in got if x[1] is resets[0])) :]
        if m.event and not m.catch_up
    ]
    assert live_after[0].match_ms > 3_000_000
    assert got[-1][1].control == {"action": "finished"}


async def test_memory_bus_fans_out_and_isolates_matches() -> None:
    bus = MemoryBus()
    a, b, other = bus.subscribe("m1"), bus.subscribe("m1"), bus.subscribe("m2")
    await bus.publish(BusMessage(kind=MessageKind.CONTROL, match_id="m1", match_ms=0, control={"x": 1}))
    assert a.queue.qsize() == b.queue.qsize() == 1 and other.queue.qsize() == 0
    a.close()
    assert bus.subscriber_count("m1") == 1
    await bus.close()
    remaining = [m async for m in b]  # queued message is still delivered, then iteration stops
    assert [m.control for m in remaining] == [{"x": 1}]
