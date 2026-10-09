"""Seeded storylines that guarantee demo-worthy moments (§7).

Each storyline is a factory `(league, home_id, away_id) -> StorylineSpec`. Beats are executed by the
simulator with the same action code as organic play. `scripted_only` storylines suppress organic goals
so the scoreline follows the script exactly.
"""

from __future__ import annotations

from collections.abc import Callable

from pitchlens.schema.events import Position

from .rosters import Club, League, Player
from .simulator import Beat, StorylineSpec

StorylineFactory = Callable[[League, str, str], StorylineSpec]


def star_forward(club: Club) -> Player:
    """The starting forward closest to a goals milestone (e.g. on 49), falling back to the best finisher."""
    forwards = [p for p in club.starting_xi() if p.pos in {Position.ST, Position.RW, Position.LW}]
    near = [p for p in forwards if p.career.goals >= 20 and p.career.goals % 25 == 24]
    return (near or sorted(forwards, key=lambda p: p.attributes.finishing, reverse=True))[0]


def playmaker(club: Club) -> Player:
    mids = [p for p in club.starting_xi() if p.pos in {Position.AM, Position.CM}]
    return max(mids, key=lambda p: p.attributes.passing)


def comeback(league: League, home_id: str, away_id: str) -> StorylineSpec:
    return StorylineSpec(
        name="comeback",
        description="Home lead 2-0 after 60'. Away raise their press at 62' and win 3-2 in stoppage time.",
        goal_policy="scripted_only",
        min_added_minutes=(2, 5),
        beats=[
            Beat(21, "goal", "home", {"style": "through_ball"}),
            Beat(52, "goal", "home", {"style": "cross"}),
            Beat(62, "press", "away", {"value": 0.95}),
            Beat(62, "style", "away", {"tempo": 0.9, "directness": 0.7}),
            Beat(62, "style", "home", {"press": 0.2, "tempo": 0.35}),
            Beat(71, "goal", "away", {"style": "through_ball"}),
            Beat(84, "goal", "away", {"style": "cross"}),
            Beat(93, "goal", "away", {"style": "long_shot"}),
        ],
    )


def red_card_turn(league: League, home_id: str, away_id: str) -> StorylineSpec:
    return StorylineSpec(
        name="red_card_turn",
        description="Underdog away side score at 27', go down to ten at 38', then sit deep and win 1-0.",
        goal_policy="scripted_only",
        beats=[
            Beat(27, "goal", "away", {"style": "cross"}),
            Beat(38, "red_card", "away"),
            Beat(38.5, "style", "away", {"press": 0.15, "directness": 0.85, "tempo": 0.3}),
            Beat(39, "style", "home", {"press": 0.8, "tempo": 0.8}),
        ],
    )


def pressure_surge(league: League, home_id: str, away_id: str) -> StorylineSpec:
    return StorylineSpec(
        name="pressure_surge",
        description="Away switch to an aggressive high press at 55'; home's build-up starts to break down.",
        beats=[
            Beat(0, "style", "away", {"press": 0.3}),
            Beat(55, "press", "away", {"value": 0.97}),
            Beat(55, "style", "away", {"tempo": 0.85}),
        ],
    )


def star_player(league: League, home_id: str, away_id: str) -> StorylineSpec:
    home = league.club(home_id)
    star = star_forward(home)
    return StorylineSpec(
        name="star_player",
        description=f"{star.name}: a 34 km/h sprint, a line-breaking assist received and a milestone goal.",
        beats=[
            Beat(12, "sprint", "home", {"player": star.player_id, "speed_kmh": 34.2, "length_m": 40}),
            Beat(
                33, "pass_of_note", "home", {"player": playmaker(home).player_id, "receiver": star.player_id}
            ),
            Beat(58, "goal", "home", {"scorer": star.player_id, "style": "through_ball"}),
        ],
    )


def chaos_derby(league: League, home_id: str, away_id: str) -> StorylineSpec:
    chaos = {"aggression_mult": 1.6, "press": 0.9, "tempo": 0.92, "directness": 0.85}
    calm = {"aggression_mult": 0.6, "press": 0.35, "tempo": 0.35, "directness": 0.2}
    beats = []
    for minute, phase in ((0, chaos), (22, calm), (41, chaos), (68, calm), (82, chaos)):
        beats += [Beat(minute, "style", "home", dict(phase)), Beat(minute, "style", "away", dict(phase))]
    return StorylineSpec(
        name="chaos_derby",
        description="A derby that swings between scrappy, foul-heavy chaos and calm, patient control.",
        beats=beats,
    )


STORYLINES: dict[str, StorylineFactory] = {
    "comeback": comeback,
    "red_card_turn": red_card_turn,
    "pressure_surge": pressure_surge,
    "star_player": star_player,
    "chaos_derby": chaos_derby,
}


def default_fixture(league: League, storyline: str) -> tuple[str, str]:
    """A sensible home/away pairing for each storyline."""
    by_strength = sorted(league.clubs, key=lambda c: c.strength, reverse=True)
    if storyline == "star_player":
        for club in by_strength:
            star = star_forward(club)
            if star.career.goals % 25 == 24:
                away = next(c for c in by_strength[::-1] if c.club_id != club.club_id)
                return club.club_id, away.club_id
    if storyline == "red_card_turn":
        return by_strength[0].club_id, by_strength[-1].club_id
    return "NHR", "SVC"


def build(league: League, name: str, home_id: str, away_id: str) -> StorylineSpec:
    try:
        return STORYLINES[name](league, home_id, away_id)
    except KeyError:
        raise ValueError(f"unknown storyline {name!r}; choose from {sorted(STORYLINES)}") from None
