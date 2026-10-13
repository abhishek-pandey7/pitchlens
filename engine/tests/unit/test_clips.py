"""Goal clips: frames around a goal, from the record's tracking."""

from __future__ import annotations

import numpy as np
import pytest

from pitchlens.live.clips import ClipError, goal_clip
from pitchlens.live.store import MatchRecord
from pitchlens.sim.rosters import load_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build
from pitchlens.sim.tracking import Tracking


@pytest.fixture(scope="module")
def record() -> MatchRecord:
    league = load_league()
    home, away = "NHR", "SVC"
    match = simulate(league, home, away, seed=7, storyline=build(league, "comeback", home, away))
    return MatchRecord(meta=match.meta, events=match.events, source="generated")


def tracking_for(record: MatchRecord) -> Tracking:
    """A light stand-in for the generator: 5 Hz frames, two players, ball across the whole match."""
    end = record.events[-1].match_ms
    ms = np.arange(0, end + 200, 200, dtype=np.int64)
    ball = np.zeros((len(ms), 3), dtype=np.float32)
    ball[:, 0] = np.linspace(0, 105, len(ms))
    xy = np.full((len(ms), 2, 2), 30.0, dtype=np.float32)
    return Tracking(record.match_id, ms, np.ones(len(ms), dtype=np.int8), ball, ["NHR-09", "SVC-10"], xy)


def test_needs_tracking(record: MatchRecord) -> None:
    with pytest.raises(ClipError, match="no tracking"):
        goal_clip(record)


def test_frames_around_each_goal(record: MatchRecord) -> None:
    record.tracking = tracking_for(record)
    goals = [e for e in record.events if e.type == "goal"]
    first = goal_clip(record, before_ms=10_000, after_ms=4_000)
    assert first["moment"]["match_ms"] == goals[0].match_ms and first["moment"]["goals"] == len(goals)
    times = [f["match_ms"] for f in first["frames"]]
    assert times == sorted(times) and len(times) == 71  # 14 s at 5 Hz, both ends included
    assert times[0] <= goals[0].match_ms - 10_000 and times[-1] <= goals[0].match_ms + 4_000
    assert goal_clip(record, index=-1)["moment"]["match_ms"] == goals[-1].match_ms
    with pytest.raises(ClipError, match="out of range"):
        goal_clip(record, index=len(goals))
    with pytest.raises(ClipError, match="at most"):
        goal_clip(record, before_ms=50_000, after_ms=20_000)
