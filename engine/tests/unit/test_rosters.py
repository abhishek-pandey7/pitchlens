from pitchlens.sim.rosters import (
    DEFAULT_PATH,
    FORMATION_STARTERS,
    SQUAD_POSITIONS,
    generate_league,
    load_league,
)


def test_generation_is_deterministic() -> None:
    assert generate_league(7) == generate_league(7)
    assert generate_league(7) != generate_league(8)


def test_league_shape_and_unique_ids() -> None:
    league = generate_league()
    assert len(league.clubs) == 20
    assert len({c.club_id for c in league.clubs}) == 20
    player_ids = [p.player_id for c in league.clubs for p in c.players]
    assert len(player_ids) == 20 * len(SQUAD_POSITIONS)
    assert len(set(player_ids)) == len(player_ids)
    names = [p.name for c in league.clubs for p in c.players]
    assert len(set(names)) == len(names)


def test_short_names_unique_within_each_club() -> None:
    for club in generate_league().clubs:
        short_names = [p.short_name for p in club.players]
        assert len(set(short_names)) == len(short_names), club.club_id


def test_starting_xi_matches_formation() -> None:
    for club in generate_league().clubs:
        xi = club.starting_xi()
        assert [p.pos for p in xi] == FORMATION_STARTERS[club.formation]
        assert len({p.player_id for p in xi}) == 11
        lineup = club.lineup()
        assert sum(p.starter for p in lineup) == 11


def test_some_players_sit_just_below_a_milestone() -> None:
    near = [
        p
        for c in generate_league().clubs
        for p in c.players
        if p.career.appearances % 50 == 49 or (p.career.goals > 20 and p.career.goals % 25 == 24)
    ]
    assert len(near) >= 20


def test_committed_roster_matches_generator() -> None:
    """data/rosters/league.json is generated; regenerate with `python -m pitchlens.sim.rosters`."""
    assert load_league(DEFAULT_PATH) == generate_league()
