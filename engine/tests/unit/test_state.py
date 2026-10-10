from collections import Counter

import pytest

from pitchlens.interpret.physical import SPRINT_KMH
from pitchlens.interpret.state import MatchState, build_state
from pitchlens.schema.enriched import AlertKind
from pitchlens.schema.events import EventType, Outcome
from pitchlens.sim.dataset import match_stats
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture, playmaker, star_forward
from pitchlens.sim.tracking import generate_tracking

LEAGUE = generate_league()


@pytest.fixture(scope="module")
def star():
    home, away = default_fixture(LEAGUE, "star_player")
    match = simulate(LEAGUE, home, away, seed=2, storyline=build(LEAGUE, "star_player", home, away))
    trk = generate_tracking(match.meta, match.events, LEAGUE)
    frames = [trk.frame(i) for i in range(len(trk))]
    state, enriched, alerts = build_state(match.meta, match.events, frames)
    return match, state, enriched, alerts


def test_counts_agree_with_the_raw_event_stream(star) -> None:
    match, state, enriched, _ = star
    counts = Counter(e.type for e in match.events)
    passes = [e for e in match.events if e.type is EventType.PASS]
    home, away = state.teams[state.home_id], state.teams[state.away_id]
    assert home.passes + away.passes == len(passes)
    assert home.passes_completed + away.passes_completed == sum(e.outcome is Outcome.COMPLETE for e in passes)
    assert home.shots + away.shots == counts[EventType.SHOT]
    assert home.fouls + away.fouls == counts[EventType.FOUL]
    assert sum(p.passes for p in state.players.values()) == len(passes)
    assert len(enriched) == len(match.events) == state.events_applied


def test_score_goals_and_xg(star) -> None:
    match, state, enriched, _ = star
    goal_events = [e for e in match.events if e.type is EventType.GOAL]
    assert sum(state.score) == len(goal_events) == len(state.goals)
    last = state.goals[-1]
    assert f"score:{last.home_score}-{last.away_score}" in goal_events[-1].qualifiers
    assert enriched[-1].home_score == state.score[0]
    stats = match_stats(match.meta, match.events)
    assert sum(t.xg for t in state.teams.values()) == pytest.approx(stats["xg"], abs=1e-6)
    assert sum(state.possession_share(c) for c in state.teams) == pytest.approx(1.0)
    assert all(0.2 < state.possession_share(c) < 0.8 for c in state.teams)


def test_scripted_pass_of_note_is_rated_hard_and_line_breaking(star) -> None:
    match, _, enriched, _ = star
    club = LEAGUE.club(match.meta.home.club_id)
    pm, target = playmaker(club), star_forward(club)
    scripted = [
        x
        for x in enriched
        if x.event.type is EventType.PASS
        and x.event.player_id == pm.player_id
        and x.event.recipient_id == target.player_id
        and 32 <= x.event.clock_ms / 60_000 <= 36
        and "through_ball" in x.event.qualifiers
    ]
    assert scripted
    m = scripted[0].metrics
    assert m["pass_difficulty"] >= 80 and m["lines_broken"] >= 1 and m["into_box"] is True


def test_shots_carry_xg_speed_and_assist_context(star) -> None:
    _, _, enriched, _ = star
    shots = [x for x in enriched if x.event.type is EventType.SHOT]
    assert all(0 < x.metrics["xg"] < 1 and x.metrics["shot_speed_kmh"] > 30 for x in shots)
    assert any("assist_pass_difficulty" in x.metrics for x in shots)


def test_physical_metrics_and_threshold_alerts(star) -> None:
    match, state, _, alerts = star
    club = LEAGUE.club(match.meta.home.club_id)
    hero = star_forward(club)
    phys = state.physical.players[hero.player_id]
    assert phys.top_speed_kmh >= 33.0 and phys.sprints >= 1 and phys.sprint_m > 0
    kinds = {(a.kind, a.player_id) for a in alerts}
    assert (AlertKind.TOP_SPEED, hero.player_id) in kinds
    assert sum(a.kind is AlertKind.DISTANCE and a.threshold == 5.0 for a in alerts) >= 15
    roster = {p.player_id: p for c in LEAGUE.clubs for p in c.players}
    for pid, p in state.physical.players.items():
        assert p.top_speed_kmh <= roster[pid].top_speed_kmh + 1.0
        assert p.hsr_m >= p.sprint_m
    assert state.physical.max_ball_speed_kmh > SPRINT_KMH


def test_without_tracking_lines_broken_is_omitted_not_guessed() -> None:
    match = simulate(LEAGUE, "NHR", "SVC", seed=3)
    _, enriched, alerts = build_state(match.meta, match.events)
    passes = [x for x in enriched if x.event.type is EventType.PASS]
    assert passes and all("lines_broken" not in x.metrics for x in passes)
    assert all("pass_difficulty" in x.metrics for x in passes) and alerts == []


def test_substitutions_and_red_cards_update_who_is_on_the_pitch() -> None:
    home, away = default_fixture(LEAGUE, "red_card_turn")
    match = simulate(LEAGUE, home, away, seed=4, storyline=build(LEAGUE, "red_card_turn", home, away))
    state = MatchState(match.meta)
    for e in match.events:
        state.apply(e)
    dismissed = Counter(
        e.team_id
        for e in match.events
        if e.type is EventType.CARD and e.outcome in (Outcome.RED, Outcome.SECOND_YELLOW)
    )
    assert dismissed[away] >= 1  # the scripted red card (organic second yellows may add more)
    for club in (home, away):
        assert len(state.teams[club].on_pitch) == 11 - dismissed[club]
    subs_on = [p for p in state.players.values() if p.on_ms and p.on_ms > 0]
    assert subs_on and all(p.minutes(state.match_ms) < 45 for p in subs_on)
