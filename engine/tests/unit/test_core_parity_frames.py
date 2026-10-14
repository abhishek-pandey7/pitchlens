"""Frames parity (docs/rust-core-plan.md §5): the Rust `frames_json` writes exactly the text the Pydantic
path produces, `json.dumps(Tracking.frame(i).model_dump(mode="json"), separators=(",", ":"),
ensure_ascii=False)`, for every frame of a stored sample and for the awkward values, and the live stream and
the goal clip endpoint send the same bytes as before.

Each test switches `core.native` itself, so the comparison is Rust against Python in either
`PITCHLENS_CORE` mode. Skipped when the extension (or this function) isn't built.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest

from pitchlens import core
from pitchlens.config import REPO_ROOT
from pitchlens.live.store import MatchRecord, MatchStore
from pitchlens.sim.tracking import Tracking

pitchlens_core = pytest.importorskip("pitchlens_core")
if not hasattr(pitchlens_core, "frames_json"):
    pytest.skip("pitchlens_core is built without frames_json", allow_module_level=True)


def dumps(payload: Any) -> str:
    """How the engine writes JSON on the wire (`live.session._json`, FastAPI's JSONResponse)."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def reference(trk: Tracking, start: int = 0, end: int | None = None) -> list[str]:
    end = len(trk) if end is None else end
    return [dumps(trk.frame(i).model_dump(mode="json")) for i in range(start, end)]


def using(native: Any, fn: Any, *args: Any) -> Any:
    saved = core.native
    core.native = native
    try:
        return fn(*args)
    finally:
        core.native = saved


def rust(trk: Tracking, *args: Any) -> list[str]:
    return using(pitchlens_core, trk.frames_json, *args)


def python(trk: Tracking, *args: Any) -> list[str]:
    return using(None, trk.frames_json, *args)


def synthetic(
    ball: list[list[float]], xy: np.ndarray, ids: list[str] | None = None, match_id: str = "M"
) -> Tracking:
    t = len(ball)
    ids = ids if ids is not None else [f"P-{p:02d}" for p in range(xy.shape[1])]
    return Tracking(
        match_id,
        np.arange(t, dtype=np.int64) * 200,
        np.ones(t, dtype=np.int8),
        np.asarray(ball, dtype=np.float32).reshape(t, 3),
        ids,
        np.asarray(xy, dtype=np.float32).reshape(t, len(ids), 2),
    )


@pytest.fixture(scope="module")
def sample() -> MatchRecord:
    """The first stored sample with its tracking (rebuilt from events once, then read from the cache)."""
    store = MatchStore([REPO_ROOT / "data" / "samples"])
    record = store.list()[0]
    assert store.ensure_tracking(record) and record.tracking is not None
    return record


# ---------------------------------------------------------------------------------------- a whole match
def test_every_frame_of_a_stored_sample(sample: MatchRecord) -> None:
    trk = sample.tracking
    assert trk is not None and len(trk) > 20_000
    expected = reference(trk)
    got = rust(trk)
    assert len(got) == len(expected)
    mismatches = [i for i, (a, b) in enumerate(zip(got, expected, strict=True)) if a != b]
    assert not mismatches, f"{len(mismatches)} frames differ, first {mismatches[0]}: {got[mismatches[0]]}"
    assert python(trk, 0, 500) == expected[:500]  # the fallback is the reference itself
    for i in range(0, len(trk), 97):  # and the JSON-ready dicts are the Pydantic dump
        assert json.loads(got[i]) == trk.frame(i).model_dump(mode="json")


def test_slices_of_a_stored_sample(sample: MatchRecord) -> None:
    trk = sample.tracking
    assert trk is not None
    n = len(trk)
    for start, end in [(0, 1), (1000, 1071), (n - 3, n), (n - 3, n + 50), (n, n + 5), (500, 500), (9, 3)]:
        assert (
            rust(trk, start, end)
            == python(trk, start, end)
            == reference(trk, min(start, n), min(max(start, end), n))
        )


# ---------------------------------------------------------------------------------------- rounding
def tricky_values() -> np.ndarray:
    """float32 values where rounding to 2 decimals is easy to get wrong."""
    base = [
        0.0,
        -0.0,
        0.004,
        -0.004,
        0.005,
        -0.005,
        0.006,
        -0.006,
        0.125,
        0.375,
        0.625,
        0.875,
        1.005,
        2.675,
        10.115,
        52.345,
        67.995,
        68.005,
        104.995,
        105.0049,
        -1.115,
        1e-30,
        -1e-30,
        3.0,
        99.99,
    ]
    vals = np.array(base, dtype=np.float32)
    # x.xx5 for every hundredth on the pitch, as float32, plus its neighbours either side
    ties = (np.arange(0, 11_000, dtype=np.float64) / 100 + 0.005).astype(np.float32)
    # exact binary ties at the third decimal: Python rounds them half to even
    quarters = (
        (np.arange(0, 110)[:, None] + np.array([0.125, 0.375, 0.625, 0.875])).ravel().astype(np.float32)
    )
    rng = np.random.default_rng(7)
    noise = rng.uniform(-10, 115, 100_000).astype(np.float32)
    large = rng.uniform(-2e7, 2e7, 20_000).astype(np.float32)  # either side of the integer-cents path
    below, above = np.nextafter(ties, np.float32(0)), np.nextafter(ties, np.float32(200))
    return np.concatenate([vals, -vals, ties, below, above, -ties, quarters, -quarters, noise, large])


def test_rounding_matches_python_round() -> None:
    vals = tricky_values()
    players = 25
    vals = np.concatenate([vals, np.zeros((-len(vals)) % (players * 2), dtype=np.float32)])
    xy = vals.reshape(-1, players, 2)
    ball = [[52.5, 34.0, 0.0]] * len(xy)
    trk = synthetic(ball, xy)
    assert rust(trk) == reference(trk)


def test_ball_rounding_at_the_edges_of_the_pitch() -> None:
    ball = [
        [-0.0, -0.0, -0.0],
        [-0.004, 0.004, 0.0],
        [104.995, 67.995, 0.125],
        [105.0049, 68.0049, 30.0],
        [0.125, 0.375, 1e-30],
        [2.675, 1.005, 0.0],
    ]
    trk = synthetic(ball, np.zeros((len(ball), 0, 2)))
    got = rust(trk)
    assert got == reference(trk)
    assert got[0] == '{"match_id":"M","match_ms":0,"ball":{"x":-0.0,"y":-0.0,"z":-0.0},"players":[]}'


def test_non_finite_and_huge_values_are_written_like_json_dumps() -> None:
    inf, nan = float("inf"), float("nan")
    xy = np.array(
        [[[1.0, nan], [inf, 2.0], [-inf, -inf], [nan, 5.0], [1e16, 3.4e38], [123456.789, -1.5e13]]],
        dtype=np.float32,
    )
    trk = synthetic([[1.0, 2.0, inf]], xy)  # Point3 has no upper bound on z
    got = rust(trk)
    assert got == reference(trk)
    assert '"y":NaN' in got[0] and '"x":Infinity' in got[0] and "P-03" not in got[0]


@pytest.mark.parametrize(
    ("ball", "ms"),
    [
        ([105.01, 34.0, 0.0], 0),
        ([50.0, -0.006, 0.0], 0),
        ([50.0, 68.01, 0.0], 0),
        ([float("nan"), 1.0, 0.0], 0),
        ([50.0, 34.0, -0.5], 0),
        ([50.0, 34.0, 0.0], -200),
    ],
)
def test_frames_pydantic_rejects_are_rejected(ball: list[float], ms: int) -> None:
    trk = synthetic([ball], np.zeros((1, 1, 2)))
    trk.ms[0] = ms
    with pytest.raises(ValueError):  # pydantic.ValidationError is a ValueError
        reference(trk)
    with pytest.raises(ValueError, match="frame 0"):
        rust(trk)


# ---------------------------------------------------------------------------------------- shape of output
def test_players_off_the_pitch_are_skipped_in_order() -> None:
    nan = float("nan")
    xy = np.array(
        [
            [[1, 1], [2, 2], [3, 3], [4, 4]],
            [[nan, nan], [2, 2], [nan, 0], [4, 4]],
            [[nan, nan], [nan, nan], [nan, nan], [nan, nan]],
        ],
        dtype=np.float32,
    )
    trk = synthetic([[50.0, 30.0, 0.0]] * 3, xy, ids=["Z-9", "A-1", "M-5", "B-2"])
    got = rust(trk)
    assert got == reference(trk)
    assert [p["player_id"] for p in json.loads(got[1])["players"]] == ["A-1", "B-2"]
    assert json.loads(got[2])["players"] == []


def test_ids_are_escaped_like_json_dumps() -> None:
    ids = ['Q-"1"', "B\\2", "É-ü-東", "tab\tnew\nline", "ctl\x01\x1f\x7f"]
    trk = synthetic([[1.0, 2.0, 0.0]], np.ones((1, len(ids), 2)), ids=ids, match_id='M "1" \\ é')
    assert rust(trk) == reference(trk)


def test_ranges_clamp_like_a_slice() -> None:
    trk = synthetic([[1.0, 2.0, 0.0]] * 5, np.ones((5, 2, 2)))
    empty = synthetic([], np.zeros((0, 2, 2)))
    for fn in (rust, python):
        assert fn(empty) == [] and fn(empty, 0, 10) == []
        assert fn(trk, 2, 2) == [] and fn(trk, 4, 1) == [] and fn(trk, 5, 9) == [] and fn(trk, 99, 100) == []
        assert fn(trk, 3, 99) == reference(trk, 3, 5) and fn(trk, 3) == reference(trk, 3, 5)
        with pytest.raises(ValueError, match="non-negative"):
            fn(trk, -1, 3)
        with pytest.raises(ValueError, match="non-negative"):
            fn(trk, 0, -1)


def test_mismatched_arrays_are_an_error() -> None:
    trk = synthetic([[1.0, 2.0, 0.0]] * 2, np.ones((2, 2, 2)))
    trk.player_ids = ["only-one"]
    with pytest.raises(ValueError, match="xy has shape"):
        rust(trk)


# ---------------------------------------------------------------------------------------- the live stream
def test_frame_for_stream(sample: MatchRecord) -> None:
    trk = sample.tracking
    assert trk is not None
    for i in (0, 1, 777, len(trk) // 2, len(trk) - 1):
        frame, text = using(pitchlens_core, trk.frame_for_stream, i)
        assert frame == trk.frame(i) and text == reference(trk, i, i + 1)[0]
        assert dumps(frame.model_dump(mode="json")) == text  # parsed back without losing a -0.0
        frame, text = using(None, trk.frame_for_stream, i)
        assert frame == trk.frame(i) and text is None
    for native in (pitchlens_core, None):
        for bad in (-1, len(trk)):
            with pytest.raises(IndexError):
                using(native, trk.frame_for_stream, bad)


def test_session_sends_the_same_frame_text(sample: MatchRecord) -> None:
    from pitchlens.ingest.bus import BusMessage, MessageKind
    from pitchlens.ingest.memory_bus import MemoryBus
    from pitchlens.live.session import MatchSession

    trk = sample.tracking
    assert trk is not None
    i = 4321

    def sent(native: Any) -> str:
        session = MatchSession(sample.meta, MemoryBus())
        sub = session.subscribe(frames=True)
        frame, text = using(native, trk.frame_for_stream, i)
        message = BusMessage(
            kind=MessageKind.FRAME,
            match_id=sample.match_id,
            match_ms=frame.match_ms,
            frame=frame,
            frame_json=text,
        )
        assert "frame_json" not in message.model_dump()  # never leaves the process
        session.handle(message)
        out = [sub.queue.get_nowait() for _ in range(sub.queue.qsize())]
        (data,) = [o.data for o in out if o is not None and o.event == "frame"]
        return data

    assert sent(pitchlens_core) == sent(None) == reference(trk, i, i + 1)[0]


# ---------------------------------------------------------------------------------------- the goal clip
@pytest.fixture
def old_clip_route() -> Iterator[Any]:
    """The clip endpoint as it was: the dict of Pydantic dumps, rendered by FastAPI."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    holder: dict[str, Any] = {}

    @app.get("/old")
    def old() -> dict[str, Any]:
        return holder["clip"]

    with TestClient(app) as client:
        yield holder, client


def old_goal_clip(
    record: MatchRecord, index: int = 0, before_ms: int = 10_000, after_ms: int = 4_000
) -> dict:
    tracking = record.tracking
    assert tracking is not None
    goals = [e for e in record.events if e.type == "goal"]
    goal = goals[index]
    start = tracking.index_at(goal.match_ms - before_ms)
    end = tracking.index_at(goal.match_ms + after_ms)
    return {
        "match_id": record.match_id,
        "moment": {
            "type": "goal",
            "match_ms": goal.match_ms,
            "team_id": goal.team_id,
            "player_id": goal.player_id,
            "goals": len(goals),
        },
        "frames": [tracking.frame(i).model_dump(mode="json") for i in range(start, end + 1)],
    }


def test_goal_clip_bytes_are_unchanged(sample: MatchRecord, old_clip_route: Any) -> None:
    from fastapi.testclient import TestClient

    from pitchlens.api.main import create_app
    from pitchlens.live.clips import goal_clip, goal_clip_json
    from pitchlens.live.hub import Hub

    holder, old_client = old_clip_route
    store = MatchStore([])
    store.add(sample)
    with TestClient(create_app(hub=Hub(store))) as client:
        for index, before, after in [(0, 10_000, 4_000), (-1, 10_000, 4_000), (0, 0, 0), (1, 30_000, 30_000)]:
            holder["clip"] = old_goal_clip(sample, index, before, after)
            old = old_client.get("/old")
            query = f"index={index}&before_ms={before}&after_ms={after}"
            new = client.get(f"/api/v1/matches/{sample.match_id}/clips/goal?{query}")
            assert new.status_code == old.status_code == 200
            assert new.headers["content-type"] == old.headers["content-type"]
            assert new.content == old.content  # byte for byte
            for native in (pitchlens_core, None):
                text = using(native, goal_clip_json, sample, index, before, after)
                assert text.encode() == old.content
                assert using(native, goal_clip, sample, index, before, after) == holder["clip"]
