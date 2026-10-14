"""Physical metrics: the Rust tracker against the Python reference, and the bulk paths against per-frame.

Over two stored samples, every frame goes to both trackers. Per player, distance must agree within 0.1 %,
top speed within 0.05 km/h, sprint counts exactly, and the alerts must come in the same sequence (kind,
player, frame, value). In practice the two agree bit for bit (engine/core/src/physical.rs reproduces
CPython's `math.hypot` and `round`), which is far inside these tolerances.

The bulk paths (`apply_range`, `MatchState.apply_tracking`, a seek's catch-up in `MatchSession`) must give
the same results as applying `Tracking.frame(i)` one at a time; those checks run on either core.
"""

from __future__ import annotations

import dataclasses
import functools
import itertools
from pathlib import Path

import pytest

from pitchlens.explain.engine import explain_match
from pitchlens.ingest.bus import BusMessage, MessageKind
from pitchlens.interpret import physical
from pitchlens.interpret.physical import PhysicalTracker, RustPhysicalTracker
from pitchlens.interpret.state import timeline
from pitchlens.live.session import MatchSession
from pitchlens.live.store import MatchRecord, MatchStore
from pitchlens.schema.enriched import PhysicalAlert
from pitchlens.schema.events import TrackingFrame

SAMPLES = Path(__file__).resolve().parents[3] / "data" / "samples"
PARITY_SAMPLES = ("SYN-NHR-SVC-1000", "SYN-SGV-ELR-1004")
DISTANCE_REL = 1e-3
SPEED_ABS_KMH = 0.05
VALUE_ABS = 0.01  # alert values are rounded to 2 decimals

rust_only = pytest.mark.skipif(
    not physical.RUST, reason="Rust tracker off (no pitchlens_core, or PITCHLENS_CORE=python)"
)


@functools.cache
def sample(match_id: str) -> tuple[MatchRecord, tuple[TrackingFrame, ...]]:
    store = MatchStore([SAMPLES])
    record = store.get(match_id)
    assert record is not None and store.ensure_tracking(record) and record.tracking is not None
    return record, tuple(record.tracking.frame(i) for i in range(len(record.tracking)))


def team_of(record: MatchRecord) -> dict[str, str]:
    return {lp.player_id: club for club, lineup in record.meta.lineups.items() for lp in lineup}


def per_frame(tracker: physical.Tracker, record: MatchRecord, frames: tuple[TrackingFrame, ...]):
    assert record.tracking is not None
    alerts: list[PhysicalAlert] = []
    for frame, period in zip(frames, record.tracking.period.tolist(), strict=True):
        alerts += tracker.apply(frame, period)
    return alerts


def whole_range(tracker: physical.Tracker, record: MatchRecord) -> list[PhysicalAlert]:
    trk = record.tracking
    assert trk is not None
    return tracker.apply_range(trk.ms, trk.period, trk.ball, trk.xy, trk.player_ids, 0, len(trk))


def assert_same_metrics(a: physical.Tracker, b: physical.Tracker) -> dict[str, float]:
    pa, pb = a.players, b.players
    assert list(pa) == list(pb)  # same players, first seen in the same order
    worst = {"distance_rel": 0.0, "top_speed_kmh": 0.0}
    for pid, x in pa.items():
        y = pb[pid]
        assert x.team_id == y.team_id
        assert x.sprints == y.sprints, pid
        for field in ("distance_m", "hsr_m", "sprint_m"):
            ref = getattr(x, field)
            assert getattr(y, field) == pytest.approx(ref, rel=DISTANCE_REL, abs=1e-9), (pid, field)
        assert abs(x.top_speed_kmh - y.top_speed_kmh) <= SPEED_ABS_KMH, pid
        assert abs(x.speed_kmh - y.speed_kmh) <= SPEED_ABS_KMH, pid
        if x.distance_m:
            worst["distance_rel"] = max(
                worst["distance_rel"], abs(x.distance_m - y.distance_m) / x.distance_m
            )
        worst["top_speed_kmh"] = max(worst["top_speed_kmh"], abs(x.top_speed_kmh - y.top_speed_kmh))
    assert abs(a.max_ball_speed_kmh - b.max_ball_speed_kmh) <= SPEED_ABS_KMH
    return worst


def assert_same_alerts(a: list[PhysicalAlert], b: list[PhysicalAlert]) -> None:
    def key(x: PhysicalAlert) -> tuple[object, ...]:
        return x.kind, x.player_id, x.team_id, x.match_ms, x.threshold, x.unit, x.match_id

    assert [key(x) for x in a] == [key(x) for x in b]
    assert all(abs(x.value - y.value) <= VALUE_ABS for x, y in zip(a, b, strict=True))


@rust_only
def test_rust_constants_match_the_reference() -> None:
    from pitchlens import core

    assert core.native is not None
    assert core.native.physical_constants() == (
        physical.HSR_KMH,
        physical.SPRINT_KMH,
        physical.SPRINT_MIN_MS,
        physical.SPEED_WINDOW_MS,
        list(physical.TOP_SPEED_THRESHOLDS_KMH),
        list(physical.DISTANCE_THRESHOLDS_KM),
        list(physical.SPRINT_COUNT_THRESHOLDS),
    )


@rust_only
@pytest.mark.parametrize("match_id", PARITY_SAMPLES)
def test_rust_tracker_matches_python_frame_by_frame(match_id: str) -> None:
    record, frames = sample(match_id)
    py = PhysicalTracker(record.match_id, team_of(record))
    rs = RustPhysicalTracker(record.match_id, team_of(record))
    py_alerts, rs_alerts = per_frame(py, record, frames), per_frame(rs, record, frames)
    assert len(py.players) >= 22 and len(py_alerts) >= 22  # a real match: every starter passes 5 km
    assert_same_metrics(py, rs)
    assert_same_alerts(py_alerts, rs_alerts)


@rust_only
@pytest.mark.parametrize("match_id", PARITY_SAMPLES)
def test_rust_apply_range_equals_frame_by_frame(match_id: str) -> None:
    record, frames = sample(match_id)
    one = RustPhysicalTracker(record.match_id, team_of(record))
    bulk = RustPhysicalTracker(record.match_id, team_of(record))
    alerts = per_frame(one, record, frames)
    bulk_alerts = whole_range(bulk, record)
    assert_same_metrics(one, bulk)
    assert_same_alerts(alerts, bulk_alerts)
    # In pieces too, with the period given as one int per piece: same result.
    trk = record.tracking
    assert trk is not None
    pieces = RustPhysicalTracker(record.match_id, team_of(record))
    piece_alerts: list[PhysicalAlert] = []
    bounds = [0, 1, 777, 14_000, *range(14_001, len(trk), 3_001), len(trk)]
    for lo, hi in itertools.pairwise(bounds):
        for period in sorted(set(trk.period[lo:hi].tolist())):
            idx = [i for i in range(lo, hi) if trk.period[i] == period]
            piece_alerts += pieces.apply_range(
                trk.ms, period, trk.ball, trk.xy, trk.player_ids, idx[0], idx[-1] + 1
            )
    assert_same_metrics(one, pieces)
    assert_same_alerts(alerts, piece_alerts)


def test_python_apply_range_equals_frame_by_frame() -> None:
    record, frames = sample(PARITY_SAMPLES[0])
    one = PhysicalTracker(record.match_id, team_of(record))
    bulk = PhysicalTracker(record.match_id, team_of(record))
    alerts = per_frame(one, record, frames)
    bulk_alerts = whole_range(bulk, record)
    assert_same_metrics(one, bulk)
    assert_same_alerts(alerts, bulk_alerts)


def test_apply_range_checks_its_arguments() -> None:
    record, _ = sample(PARITY_SAMPLES[0])
    trk = record.tracking
    assert trk is not None
    tracker = physical.new_tracker(record.match_id, team_of(record))
    assert tracker.apply_range(trk.ms, trk.period, trk.ball, trk.xy, trk.player_ids, 5, 5) == []
    if physical.RUST:
        with pytest.raises(IndexError):
            tracker.apply_range(trk.ms, trk.period, trk.ball, trk.xy, trk.player_ids, 0, len(trk) + 1)
        with pytest.raises(ValueError):
            tracker.apply_range(trk.ms, trk.period, trk.ball, trk.xy, trk.player_ids[:-1], 0, 10)


def test_explain_match_on_tracking_equals_frame_objects() -> None:
    """The bulk path agents use (views.py): same state, same insights, same pass context from tracking."""
    record, frames = sample(PARITY_SAMPLES[0])
    assert record.tracking is not None
    end = record.tracking.index_at(60 * 60_000) + 1  # an hour in, as an analysis mid-match would be
    events = [e for e in record.events if e.match_ms <= int(record.tracking.ms[end - 1])]
    a_state, a_insights = explain_match(record.meta, events, frames[:end])
    b_state, b_insights = explain_match(record.meta, events, tracking=record.tracking, frames_end=end)
    assert [i.model_dump() for i in a_insights] == [i.model_dump() for i in b_insights]
    assert [dataclasses.asdict(p) for p in a_state.players.values()] == [
        dataclasses.asdict(p) for p in b_state.players.values()
    ]
    assert sum(p.lines_broken for p in b_state.players.values()) > 0  # pass context did use tracking
    assert_same_metrics(a_state.physical, b_state.physical)
    assert a_state.last_frame == b_state.last_frame


def test_seek_catch_up_equals_playing_from_kick_off() -> None:
    """A seek replays events only; the session applies the skipped frames in bulk. The result must be what
    the live pipeline builds by playing every event and frame up to the same moment."""
    record, frames = sample(PARITY_SAMPLES[0])
    trk = record.tracking
    assert trk is not None
    target = 80 * 60_000
    events = [e for e in record.events if e.match_ms <= target]
    shown = [f for f in frames if f.match_ms <= target]

    def msg(kind: MessageKind, ms: int, **kw: object) -> BusMessage:
        return BusMessage(kind=kind, match_id=record.match_id, match_ms=ms, **kw)  # type: ignore[arg-type]

    live = MatchSession(record.meta, bus=None)  # type: ignore[arg-type]
    for item in timeline(events, shown):
        if isinstance(item, TrackingFrame):
            live.handle(msg(MessageKind.FRAME, item.match_ms, frame=item))
        else:
            live.handle(msg(MessageKind.EVENT, item.match_ms, event=item))

    seek = MatchSession(record.meta, bus=None)  # type: ignore[arg-type]
    seek.tracking = trk
    seek.handle(msg(MessageKind.CONTROL, target, control={"action": "reset", "to_ms": target}))
    for e in events:
        seek.handle(msg(MessageKind.EVENT, e.match_ms, event=e, catch_up=True))
    seek.handle(msg(MessageKind.CONTROL, target, control={"action": "pause"}))  # live again: catch-up ends

    assert [i.model_dump() for i in live.engine.insights] == [i.model_dump() for i in seek.engine.insights]
    assert_same_metrics(live.state.physical, seek.state.physical)
    assert [dataclasses.asdict(p) for p in live.state.players.values()] == [
        dataclasses.asdict(p) for p in seek.state.players.values()
    ]
    assert seek.state.last_frame == live.state.last_frame
    assert (
        seek.state.physical.players and max(p.distance_m for p in seek.state.physical.players.values()) > 7000
    )
