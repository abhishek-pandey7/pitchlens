import statistics
from collections import Counter
from itertools import pairwise

import pytest

from pitchlens.explain.changepoint import poisson_llr, rate_shift
from pitchlens.explain.engine import explain_match
from pitchlens.explain.salience import salience
from pitchlens.schema.insights import Insight, InsightType
from pitchlens.sim.dataset import plan_fixtures
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture, star_forward
from pitchlens.sim.tracking import generate_tracking

LEAGUE = generate_league()


def run(storyline: str | None, seed: int, *, tracking: bool = False, home: str = "NHR", away: str = "SVC"):
    if storyline:
        home, away = default_fixture(LEAGUE, storyline)
    spec = build(LEAGUE, storyline, home, away) if storyline else None
    match = simulate(LEAGUE, home, away, seed=seed, storyline=spec)
    frames = []
    if tracking:
        trk = generate_tracking(match.meta, match.events, LEAGUE)
        frames = [trk.frame(i) for i in range(len(trk))]
    state, insights = explain_match(match.meta, match.events, frames)
    return match, state, insights


def minute(i: Insight) -> float:
    return i.clock_ms / 60_000


def of(insights: list[Insight], kind: InsightType) -> list[Insight]:
    return [i for i in insights if i.type is kind]


# ---------------------------------------------------------------- building blocks
def test_rate_shift_detects_a_doubling_and_ignores_noise() -> None:
    assert poisson_llr(10, 50, 10, 50) == pytest.approx(0.0)
    shift = rate_shift(10, 50, 20, 40)
    assert shift is not None and shift.direction == 1 and shift.after == pytest.approx(0.5)
    assert rate_shift(10, 50, 6, 25) is None  # same rate
    assert rate_shift(3, 10, 6, 10) is None  # too little exposure to say anything


def test_salience_orders_by_impact_and_game_state() -> None:
    early_opener = salience(InsightType.GOAL, wp_delta=0.2, clock_minute=20)
    late_winner = salience(InsightType.GOAL, wp_delta=0.6, clock_minute=93, goal_difference=-1)
    blowout = salience(InsightType.GOAL, wp_delta=0.01, clock_minute=80, goal_difference=4)
    assert late_winner > early_opener > blowout
    assert 0 <= blowout <= 1 and late_winner <= 1


# ---------------------------------------------------------------- storylines
def test_comeback_goals_are_explained_and_ranked() -> None:
    _, _, insights = run("comeback", 7)
    goals = of(insights, InsightType.GOAL)
    assert [g.headline_key for g in goals] == [
        "goal.go_ahead", "goal.extends_lead", "goal.pulls_back", "goal.equaliser", "goal.go_ahead",
    ]  # fmt: skip
    winner = goals[-1]
    assert "late_drama" in winner.why_tags and winner.salience >= max(g.salience for g in goals[:3])
    wp = next(e for e in winner.evidence if e.metric == "win_prob")
    assert wp.to - wp.from_ > 0.4
    assert all(any(e.metric == "xg" for e in g.evidence) for g in goals)


@pytest.mark.parametrize("seed", [3, 4, 5, 6, 7])
def test_pressure_surge_is_detected_after_the_scripted_press(seed: int) -> None:
    match, _, insights = run("pressure_surge", seed)
    away = match.meta.away.club_id
    hits = [
        i
        for i in of(insights, InsightType.PRESSURE_CHANGE)
        if i.entities.teams == [away]
        and i.headline_key.endswith("press_intensified")
        and 55 <= minute(i) <= 72
    ]
    assert hits, [(minute(i), i.headline_key) for i in of(insights, InsightType.PRESSURE_CHANGE)]
    rate = next(e for e in hits[0].evidence if e.metric.startswith("pressing_actions"))
    assert rate.to >= 1.6 * rate.from_


def test_star_player_moments_with_tracking() -> None:
    match, state, insights = run("star_player", 2, tracking=True)
    star = star_forward(LEAGUE.club(match.meta.home.club_id)).player_id
    # The 33 km/h tag fires once, the first time he crosses it (organic counters can get there first).
    sprint = [i for i in of(insights, InsightType.SPRINT_THRESHOLD) if star in i.entities.players]
    assert len(sprint) == 1 and next(e for e in sprint[0].evidence).value >= 33
    assert state.physical.players[star].top_speed_kmh >= 33.5  # the scripted 34 km/h burst
    passes = [
        i
        for i in of(insights, InsightType.PASS_OF_NOTE)
        if 32 <= minute(i) <= 36 and star in i.entities.players
    ]
    assert passes and any(e.metric == "lines_broken" for e in passes[0].evidence)
    milestones = [
        i for i in of(insights, InsightType.CAREER_MILESTONE) if i.headline_key == "career_milestone.goals"
    ]
    assert milestones and milestones[0].entities.players == [star]
    assert next(e for e in milestones[0].evidence).value % 25 == 0


def test_red_card_is_a_high_salience_card_with_win_probability() -> None:
    _, _, insights = run("red_card_turn", 4)
    cards = of(insights, InsightType.CARD)
    red = next(i for i in cards if i.headline_key == "card.red")
    assert "dismissal" in red.why_tags and any(e.metric == "win_prob" for e in red.evidence)
    yellows = [i for i in cards if i.headline_key == "card.yellow"]
    assert all(red.salience > y.salience for y in yellows)


def test_chaos_derby_produces_control_chaos_flips() -> None:
    _, _, insights = run("chaos_derby", 5)
    flips = of(insights, InsightType.CONTROL_CHAOS_FLIP)
    assert {i.headline_key for i in flips} >= {"control_chaos_flip.to_chaos", "control_chaos_flip.to_control"}
    assert all(any(e.metric == "control_index" for e in i.evidence) for i in flips)


# ---------------------------------------------------------------- invariants
def test_organic_volume_cooldowns_and_evidence() -> None:
    per_match, pressure = [], []
    for fx in [f for f in plan_fixtures(LEAGUE, 15) if f.storyline is None]:
        _, state, insights = run(None, fx.seed, home=fx.home, away=fx.away)
        per_match.append(len(insights))
        pressure.append(len(of(insights, InsightType.PRESSURE_CHANGE)))
        ids = [i.insight_id for i in insights]
        assert len(ids) == len(set(ids))
        for i in insights:
            assert i.evidence and 0 <= i.salience <= 1 and i.match_id == state.match_id
        times = [i.match_ms for i in insights]
        assert times == sorted(times) or max(a - b for a, b in pairwise(times)) < 60_000
        momentum = [i.match_ms for i in of(insights, InsightType.MOMENTUM_SHIFT)]
        assert all(b - a >= 8 * 60_000 for a, b in pairwise(momentum))
    assert 15 <= statistics.mean(per_match) <= 50
    assert statistics.mean(pressure) <= 2.0  # few false pressing alarms in organic play


def test_every_goal_produces_exactly_one_goal_insight() -> None:
    match, state, insights = run(None, 21)
    assert len(of(insights, InsightType.GOAL)) == sum(state.score)
    kinds = Counter(i.type for i in insights)
    assert kinds[InsightType.GOAL] == len([e for e in match.events if e.type == "goal"])
