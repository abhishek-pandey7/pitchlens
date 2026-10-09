import math
import statistics
from collections import Counter
from itertools import pairwise

import pytest

from pitchlens.schema.events import EventType, Outcome
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import SimulatedMatch, simulate
from pitchlens.sim.storylines import STORYLINES, build, default_fixture, star_forward

LEAGUE = generate_league()


def run(seed: int, storyline: str | None = None, home: str = "NHR", away: str = "SVC") -> SimulatedMatch:
    if storyline:
        home, away = default_fixture(LEAGUE, storyline)
    spec = build(LEAGUE, storyline, home, away) if storyline else None
    return simulate(LEAGUE, home, away, seed=seed, storyline=spec)


def final_score(match: SimulatedMatch) -> tuple[int, int]:
    goals = [e for e in match.events if e.type is EventType.GOAL]
    home = sum(1 for g in goals if g.team_id == match.meta.home.club_id)
    return home, len(goals) - home


def test_same_seed_same_match() -> None:
    a, b = run(11), run(11)
    assert [e.model_dump() for e in a.events] == [e.model_dump() for e in b.events]
    assert [e.model_dump() for e in run(12).events] != [e.model_dump() for e in a.events]


def test_event_stream_is_well_formed() -> None:
    match = run(3)
    events = match.events
    assert events[0].type is EventType.MATCH_START and events[-1].type is EventType.MATCH_END
    assert [e.seq for e in events] == list(range(len(events)))
    assert all(b.match_ms >= a.match_ms for a, b in pairwise(events))
    counts = Counter(e.type for e in events)
    assert counts[EventType.PERIOD_START] == counts[EventType.PERIOD_END] == 2
    for period in (1, 2):
        clocks = [e.clock_ms for e in events if e.period == period]
        assert clocks == sorted(clocks)
        assert min(clocks) >= 45 * 60_000 * (period - 1)
    # possession ids only advance on possession changes
    last = 0
    for e in events:
        if e.possession_id is None:
            continue
        if e.type is EventType.POSSESSION_CHANGE:
            assert e.possession_id == last + 1
            last = e.possession_id
        else:
            assert e.possession_id == last


def test_actions_carry_durations_and_consistent_carry_speeds() -> None:
    match = run(5)
    for e in match.events:
        if e.type in (EventType.PASS, EventType.CARRY, EventType.SHOT):
            assert e.duration_ms is not None and e.duration_ms > 0
        if e.type is EventType.CARRY:
            assert e.start and e.end and e.player_id and e.duration_ms
            dist = math.hypot(e.end.x - e.start.x, e.end.y - e.start.y)
            player = LEAGUE.club(e.team_id or "").player(e.player_id)
            assert dist / (e.duration_ms / 1000) * 3.6 <= player.top_speed_kmh + 0.5


def test_goal_scores_are_cumulative() -> None:
    match = run(8)
    home, away = 0, 0
    for e in match.events:
        if e.type is EventType.GOAL:
            if e.team_id == match.meta.home.club_id:
                home += 1
            else:
                away += 1
            assert f"score:{home}-{away}" in e.qualifiers


def test_calibration_against_real_football_ranges() -> None:
    clubs = [c.club_id for c in LEAGUE.clubs]
    rows = []
    for seed in range(12):
        match = run(seed, home=clubs[seed], away=clubs[(seed + 7) % 20])
        counts = Counter(e.type for e in match.events)
        passes = [e for e in match.events if e.type is EventType.PASS]
        rows.append(
            {
                "passes": len(passes),
                "completion": sum(e.outcome is Outcome.COMPLETE for e in passes) / len(passes),
                "shots": counts[EventType.SHOT],
                "goals": counts[EventType.GOAL],
                "fouls": counts[EventType.FOUL],
                "throw_ins": counts[EventType.THROW_IN],
            }
        )
    mean = {k: statistics.mean(r[k] for r in rows) for k in rows[0]}
    assert 850 <= mean["passes"] <= 1300
    assert 0.74 <= mean["completion"] <= 0.85
    assert 18 <= mean["shots"] <= 32
    assert 1.5 <= mean["goals"] <= 4.0
    assert 15 <= mean["fouls"] <= 35
    assert 25 <= mean["throw_ins"] <= 60


def test_comeback_storyline_follows_the_script() -> None:
    match = run(7, "comeback")
    goals = [e for e in match.events if e.type is EventType.GOAL]
    home = match.meta.home.club_id
    assert [g.team_id == home for g in goals] == [True, True, False, False, False]
    minutes = [g.clock_ms / 60_000 for g in goals]
    for got, want in zip(minutes, [21, 52, 71, 84, 93], strict=True):
        assert want <= got < want + 3
    assert final_score(match) == (2, 3)


def test_red_card_turn_storyline() -> None:
    match = run(4, "red_card_turn")
    reds = [e for e in match.events if e.type is EventType.CARD and e.outcome is Outcome.RED]
    assert reds and reds[0].team_id == match.meta.away.club_id and 38 <= reds[0].clock_ms / 60_000 < 41
    assert final_score(match) == (0, 1)


def test_star_player_storyline_moments() -> None:
    match = run(2, "star_player")
    star = star_forward(LEAGUE.club(match.meta.home.club_id))
    sprints = [
        e
        for e in match.events
        if e.type is EventType.CARRY
        and e.player_id == star.player_id
        and e.start
        and e.end
        and e.duration_ms
        and math.hypot(e.end.x - e.start.x, e.end.y - e.start.y) / e.duration_ms * 3600 >= 33.5
    ]
    assert sprints, "expected a 34 km/h sprint"
    assert any(e.type is EventType.GOAL and e.player_id == star.player_id for e in match.events)


@pytest.mark.parametrize("name", sorted(STORYLINES))
def test_every_storyline_runs(name: str) -> None:
    match = run(1, name)
    assert match.meta.storyline == name
    assert match.events[-1].type is EventType.MATCH_END


def test_chaos_derby_is_scrappier_than_organic() -> None:
    chaos = Counter(e.type for e in run(9, "chaos_derby").events)
    calm = Counter(e.type for e in run(9).events)
    assert chaos[EventType.FOUL] > calm[EventType.FOUL]
