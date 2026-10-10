import statistics

import pytest

from pitchlens.interpret.state import SAMPLE_EVERY_MS, MatchState
from pitchlens.interpret.win_prob import remaining_minutes, win_probability
from pitchlens.interpret.windows import DecayedSum, EventWindow, SegmentShare
from pitchlens.schema.events import EventType, Outcome
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture

LEAGUE = generate_league()


def run_state(storyline: str | None, seed: int) -> MatchState:
    home, away = default_fixture(LEAGUE, storyline or "")
    spec = build(LEAGUE, storyline, home, away) if storyline else None
    match = simulate(LEAGUE, home, away, seed=seed, storyline=spec)
    state = MatchState(match.meta)
    for e in match.events:
        state.apply(e)
    return state


def series_mean(state: MatchState, key: str, lo_min: float, hi_min: float, index: int | None = None) -> float:
    values = []
    for row in state.series:
        if lo_min <= row["clock_ms"] / 60_000 < hi_min and row[key] is not None:
            values.append(row[key][index] if index is not None else row[key])
    return statistics.mean(values)


# ---------------------------------------------------------------- building blocks
def test_event_window_expires_old_items() -> None:
    w = EventWindow(1000)
    w.add(0, "a")
    w.add(500, "a", 2)
    assert w.total("a", 900) == 3
    assert w.total("a", 1200) == 2
    assert w.total("a", 2000) == 0


def test_decayed_sum_halves_every_half_life() -> None:
    d = DecayedSum(1000)
    d.add(0, 8.0)
    assert d.value(1000) == pytest.approx(4.0)
    d.add(2000, 1.0)
    assert d.value(2000) == pytest.approx(3.0)


def test_segment_share_over_trailing_window() -> None:
    s = SegmentShare(10_000)
    s.change(0, "H")
    s.change(6_000, "A")
    assert s.shares(10_000) == pytest.approx({"H": 0.6, "A": 0.4})
    assert s.shares(20_000) == pytest.approx({"A": 1.0})


# ---------------------------------------------------------------- win probability
def test_win_probability_basics() -> None:
    start = win_probability(0, 0, home_rate_90=1.8, away_rate_90=1.0, minutes_left=95)
    assert start.home + start.draw + start.away == pytest.approx(1.0)
    assert start.home > start.away
    late_lead = win_probability(1, 0, home_rate_90=1.4, away_rate_90=1.4, minutes_left=3)
    assert late_lead.home > 0.85
    reds = win_probability(0, 0, home_rate_90=1.4, away_rate_90=1.4, minutes_left=60, home_reds=1)
    assert reds.home < reds.away
    final = win_probability(2, 3, home_rate_90=1, away_rate_90=1, minutes_left=0)
    assert (final.home, final.draw, final.away) == (0.0, 0.0, 1.0)
    assert remaining_minutes(30, 1, None) == pytest.approx(65)
    assert remaining_minutes(92, 2, 6) == pytest.approx(4)


def test_comeback_win_probability_and_momentum_swing() -> None:
    state = run_state("comeback", 7)
    away_wp_at_60 = next(r["wp"][2] for r in state.series if r["clock_ms"] >= 60 * 60_000)
    assert away_wp_at_60 < 0.12
    assert state.win_prob.away == 1.0 and state.finished
    # Momentum is signed home-minus-away: the away press after 62' should tip it towards the away side.
    assert series_mean(state, "momentum", 64, 90) < series_mean(state, "momentum", 25, 55)


def test_goal_events_carry_win_probability_shift() -> None:
    home, away = default_fixture(LEAGUE, "comeback")
    match = simulate(LEAGUE, home, away, seed=7, storyline=build(LEAGUE, "comeback", home, away))
    state = MatchState(match.meta)
    goals = [x for x in (state.apply(e) for e in match.events) if x.event.type is EventType.GOAL]
    assert all(x.metrics["wp_after"] > x.metrics["wp_before"] for x in goals)
    winner = goals[-1]
    assert winner.metrics["wp_after"] - winner.metrics["wp_before"] > 0.4  # 90+3 winner flips the match


# ---------------------------------------------------------------- pressing, territory, control, rhythm
def test_pressure_surge_collapses_away_ppda() -> None:
    """A 10-minute PPDA rests on ~10 defensive actions, so judge the press across several seeds."""
    ratios = []
    for seed in range(3, 8):
        state = run_state("pressure_surge", seed)
        before = series_mean(state, "ppda", 25, 50, index=1)
        after = series_mean(state, "ppda", 62, 85, index=1)
        ratios.append(after / before)
    assert statistics.median(ratios) < 0.75


def test_chaos_derby_control_index_follows_the_phases() -> None:
    state = run_state("chaos_derby", 5)
    chaos = statistics.mean([series_mean(state, "control", 8, 21), series_mean(state, "control", 46, 67)])
    calm = statistics.mean([series_mean(state, "control", 28, 40), series_mean(state, "control", 73, 81)])
    assert calm - chaos > 15


def test_series_and_readings_shape() -> None:
    state = run_state(None, 11)
    assert len(state.series) >= 180
    assert all(b["match_ms"] >= a["match_ms"] for a, b in zip(state.series, state.series[1:], strict=False))
    gaps = [b["match_ms"] - a["match_ms"] for a, b in zip(state.series, state.series[1:], strict=False)]
    assert statistics.median(gaps) <= SAMPLE_EVERY_MS * 1.5
    row = state.readings()
    assert 0 <= row["field_tilt"] <= 1 and -1 <= row["momentum"] <= 1
    assert sum(row["wp"]) == pytest.approx(1.0, abs=1e-3)
    assert row["control"] is not None and 0 <= row["control"] <= 100
    assert row["tempo"] > 5
    snap = state.snapshot()
    assert snap["dynamics"]["score"] == list(state.score)
    reds = sum(1 for p in state.players.values() if p.sent_off)
    assert reds == state.teams[state.home_id].red_cards + state.teams[state.away_id].red_cards


def test_tempo_baseline_and_rhythm_z_are_populated() -> None:
    state = run_state(None, 12)
    zs = [r["tempo_z"] for r in state.series if r["tempo_z"] is not None]
    assert len(zs) > 100
    assert max(abs(z) for z in zs) > 1.0  # rhythm genuinely varies within a match


def test_red_card_lowers_short_handed_win_probability() -> None:
    home, away = default_fixture(LEAGUE, "red_card_turn")
    match = simulate(LEAGUE, home, away, seed=4, storyline=build(LEAGUE, "red_card_turn", home, away))
    state = MatchState(match.meta)
    for x in (state.apply(e) for e in match.events):
        if x.event.type is EventType.CARD and x.event.outcome is Outcome.RED:
            assert x.metrics["wp_after"] < x.metrics["wp_before"]
            break
    else:
        pytest.fail("no red card")
