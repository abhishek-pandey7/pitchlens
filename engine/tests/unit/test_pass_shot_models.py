from hypothesis import given
from hypothesis import strategies as st

from pitchlens.interpret import passes, shots

coord_x = st.floats(min_value=1, max_value=104)
coord_y = st.floats(min_value=1, max_value=67)


def features(dist: float, **kw: object) -> passes.PassFeatures:
    base = {"forward": 0.0, "under_pressure": False, "lines_broken": 0, "aerial": False, "into_box": False}
    base.update(kw)
    return passes.PassFeatures(distance=dist, **base)  # type: ignore[arg-type]


def test_short_safe_pass_is_easy_and_long_pressured_through_ball_is_hard() -> None:
    assert passes.difficulty(features(6, forward=-0.5)) < 10
    hard = features(35, forward=1.0, under_pressure=True, lines_broken=2, into_box=True)
    assert passes.difficulty(hard) > 80


@given(st.floats(min_value=1, max_value=60), st.floats(min_value=0.1, max_value=20))
def test_difficulty_increases_with_distance(d: float, extra: float) -> None:
    assert passes.difficulty(features(d + extra)) >= passes.difficulty(features(d))


@given(st.floats(min_value=1, max_value=60), st.integers(min_value=0, max_value=2))
def test_pressure_and_lines_broken_make_passes_harder(d: float, lines: int) -> None:
    easy = features(d, lines_broken=lines)
    assert passes.difficulty(features(d, lines_broken=lines, under_pressure=True)) > passes.difficulty(easy)
    assert passes.difficulty(features(d, lines_broken=lines + 1)) > passes.difficulty(easy)


def test_lines_broken_counts_only_forward_crossings() -> None:
    assert passes.lines_broken(30, 70, [40.0, 55.0, 80.0]) == 2
    assert passes.lines_broken(70, 30, [40.0, 55.0]) == 0


def test_xg_reference_values() -> None:
    penalty_spot = shots.shot_features(94, 34, header=False, under_pressure=False, one_on_one=False)
    six_yard = shots.shot_features(99.5, 34, header=False, under_pressure=False, one_on_one=False)
    long_range = shots.shot_features(80, 34, header=False, under_pressure=False, one_on_one=False)
    assert 0.12 < shots.xg(penalty_spot) < 0.3
    assert shots.xg(six_yard) > 0.4
    assert shots.xg(long_range) < 0.05
    assert (
        shots.xg(
            shots.shot_features(94, 34, header=False, under_pressure=False, one_on_one=False, penalty=True)
        )
        == 0.76
    )


@given(coord_x, coord_y, st.floats(min_value=0.5, max_value=30))
def test_xg_decreases_moving_away_from_goal_centrally(x: float, y: float, back: float) -> None:
    near = shots.shot_features(min(x, 104), 34, header=False, under_pressure=False, one_on_one=False)
    far = shots.shot_features(
        max(min(x, 104) - back, 0.5), 34, header=False, under_pressure=False, one_on_one=False
    )
    assert shots.xg(far) <= shots.xg(near)


def test_header_and_pressure_lower_xg() -> None:
    open_shot = shots.shot_features(95, 30, header=False, under_pressure=False, one_on_one=False)
    assert shots.xg(
        shots.shot_features(95, 30, header=True, under_pressure=False, one_on_one=False)
    ) < shots.xg(open_shot)
    assert shots.xg(
        shots.shot_features(95, 30, header=False, under_pressure=True, one_on_one=False)
    ) < shots.xg(open_shot)
