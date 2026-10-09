import math

import numpy as np
import pytest

from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import SimulatedMatch, simulate
from pitchlens.sim.storylines import build, default_fixture, star_forward
from pitchlens.sim.tracking import TICK_MS, BallPath, Tracking, generate_tracking

LEAGUE = generate_league()


@pytest.fixture(scope="module")
def star_match() -> tuple[SimulatedMatch, Tracking]:
    home, away = default_fixture(LEAGUE, "star_player")
    match = simulate(LEAGUE, home, away, seed=2, storyline=build(LEAGUE, "star_player", home, away))
    return match, generate_tracking(match.meta, match.events, LEAGUE)


def smoothed_speeds_kmh(trk: Tracking, window: int = 5) -> np.ndarray:
    """Speed over a 1 s window, ignoring windows that straddle half-time."""
    d = np.linalg.norm(trk.xy[window:] - trk.xy[:-window], axis=2)
    d[trk.period[window:] != trk.period[:-window]] = np.nan
    return d / (window * TICK_MS / 1000) * 3.6


def test_ball_path_passes_through_every_event_location(star_match) -> None:
    match, _ = star_match
    path = BallPath(match.events)
    for e in match.events:
        if e.start:
            x, y, _ = path.at(float(e.match_ms))
            assert math.hypot(x - e.start.x, y - e.start.y) < 0.01, e.event_id


def test_frames_track_the_ball_and_cover_both_halves(star_match) -> None:
    match, trk = star_match
    assert set(np.unique(trk.period)) == {1, 2}
    assert np.all(np.diff(trk.ms) > 0)
    errors = []
    for e in match.events:
        if e.start and e.type in ("pass", "carry", "shot"):
            i = trk.index_at(e.match_ms)
            errors.append(math.hypot(trk.ball[i][0] - e.start.x, trk.ball[i][1] - e.start.y))
    assert np.percentile(errors, 99) < 2.5


def test_eleven_per_side_on_pitch_and_bounds(star_match) -> None:
    _, trk = star_match
    on_pitch = (~np.isnan(trk.xy[:, :, 0])).sum(axis=1)
    assert on_pitch.max() == 22 and on_pitch.min() >= 16
    xs, ys = trk.xy[:, :, 0], trk.xy[:, :, 1]
    assert np.nanmin(xs) >= 0 and np.nanmax(xs) <= 105 and np.nanmin(ys) >= 0 and np.nanmax(ys) <= 68


def test_speeds_are_capped_and_star_sprint_is_visible(star_match) -> None:
    match, trk = star_match
    top = np.nanmax(smoothed_speeds_kmh(trk), axis=0)
    players = {p.player_id: p for c in LEAGUE.clubs for p in c.players}
    for pid, speed in zip(trk.player_ids, top, strict=True):
        if not np.isnan(speed):
            assert speed <= players[pid].top_speed_kmh + 0.5, pid
    star = star_forward(LEAGUE.club(match.meta.home.club_id))
    assert top[trk.player_ids.index(star.player_id)] >= 33.0


def test_distance_covered_is_realistic(star_match) -> None:
    _, trk = star_match
    steps = np.linalg.norm(np.diff(trk.xy, axis=0), axis=2)
    steps[trk.period[1:] != trk.period[:-1]] = 0
    km = np.nansum(steps, axis=0) / 1000
    full_game = ~np.isnan(trk.xy).any(axis=(0, 2))
    assert 8.0 < np.median(km[full_game]) < 12.5


def test_round_trip_npz(star_match, tmp_path) -> None:
    _, trk = star_match
    loaded = Tracking.load(trk.save(tmp_path / "tracking.npz"))
    assert loaded.player_ids == trk.player_ids
    np.testing.assert_array_equal(loaded.ms, trk.ms)
    frame = loaded.frame(500)
    assert frame.match_ms == int(trk.ms[500]) and 18 <= len(frame.players) <= 22
