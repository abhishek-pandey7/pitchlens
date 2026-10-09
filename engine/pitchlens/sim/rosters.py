"""Fictional league: 20 clubs x 25 players, generated deterministically from a seed.

Nothing here refers to real clubs or players. Names are assembled from broad, international name pools.
Career stats are seeded so that some players sit just below milestones (e.g. 99 appearances, 49 goals)
and milestone insights can fire during a match.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from pydantic import Field

from pitchlens.schema.common import Model
from pitchlens.schema.events import LineupPlayer, Position, TeamInfo

ROSTER_VERSION = "1.0"
DEFAULT_SEED = 2026
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "rosters" / "league.json"

# (town, suffix, short code). Invented place names, no real club identities.
CLUB_NAMES: list[tuple[str, str, str]] = [
    ("Northbridge", "Harriers", "NHR"),
    ("Southvale", "City", "SVC"),
    ("Elmford", "Rovers", "ELR"),
    ("Kingsmere", "Athletic", "KMA"),
    ("Ashcombe", "United", "ASU"),
    ("Redhaven", "Albion", "RHA"),
    ("Thornbury", "Wanderers", "THW"),
    ("Westmarsh", "Town", "WMT"),
    ("Highcliffe", "Forest", "HCF"),
    ("Coldwater", "Mariners", "CWM"),
    ("Brackenhill", "Rangers", "BRR"),
    ("Stonegate", "Villa", "SGV"),
    ("Larkspur", "Saints", "LKS"),
    ("Oakhurst", "Borough", "OKB"),
    ("Fenwick", "Dynamo", "FWD"),
    ("Silverton", "Sporting", "SLS"),
    ("Marlow Bay", "Pilots", "MBP"),
    ("Greywick", "Celtic", "GWC"),
    ("Hollins", "Wolves", "HLW"),
    ("Eastport", "Corinthians", "EPC"),
]

PALETTES: list[tuple[str, str]] = [
    ("#0B3D91", "#FFFFFF"),
    ("#C8102E", "#111111"),
    ("#1F7A4D", "#F2E8CF"),
    ("#5B2A86", "#F5C518"),
    ("#E85D04", "#1B1B1E"),
    ("#006D77", "#EDF6F9"),
    ("#3A0CA3", "#4CC9F0"),
    ("#9D0208", "#FFBA08"),
    ("#2B2D42", "#EF233C"),
    ("#14213D", "#FCA311"),
    ("#386641", "#F2E8CF"),
    ("#7B2CBF", "#E0AAFF"),
    ("#BC4749", "#F4F1DE"),
    ("#023E8A", "#90E0EF"),
    ("#6A040F", "#E5E5E5"),
    ("#8D99AE", "#2B2D42"),
    ("#00509D", "#FDC500"),
    ("#2D6A4F", "#FFFFFF"),
    ("#F77F00", "#003049"),
    ("#4A4E69", "#F2E9E4"),
]

FIRST_NAMES = [
    "Tomás",
    "Kwame",
    "Luca",
    "Arjun",
    "Mateo",
    "Yusuf",
    "Elias",
    "Kenji",
    "Oliver",
    "Rafael",
    "Idris",
    "Nikolai",
    "Diego",
    "Samuel",
    "Amadou",
    "Felix",
    "Jonah",
    "Ilias",
    "Marco",
    "Kofi",
    "Hugo",
    "Emre",
    "Theo",
    "Bruno",
    "Sami",
    "Ravi",
    "Callum",
    "Dario",
    "Ade",
    "Tariq",
    "Leon",
    "Jakub",
    "Mikel",
    "Noah",
    "Bilal",
    "Ezra",
    "Andrés",
    "Femi",
    "Lars",
    "Omar",
    "Pavel",
    "Seun",
    "Viktor",
    "Wen",
    "Youssef",
    "Zane",
    "Caio",
    "Declan",
    "Henrik",
    "Iker",
]
SURNAMES = [
    "Reyes",
    "Okafor",
    "Bianchi",
    "Mehta",
    "Navarro",
    "Demir",
    "Lindqvist",
    "Tanaka",
    "Hartley",
    "Costa",
    "Mensah",
    "Petrov",
    "Alvarez",
    "Adeyemi",
    "Diallo",
    "Becker",
    "Whitlock",
    "Haddad",
    "Rossi",
    "Asante",
    "Laurent",
    "Yilmaz",
    "Fletcher",
    "Moreira",
    "Karimi",
    "Iyer",
    "Doyle",
    "Ferraro",
    "Obi",
    "Rahman",
    "Vogel",
    "Novak",
    "Etxeberria",
    "Brooks",
    "Saleh",
    "Kowalski",
    "Ortega",
    "Balogun",
    "Nyberg",
    "Farouk",
    "Horvat",
    "Ogunleye",
    "Sokolov",
    "Zhang",
    "Benali",
    "Calloway",
    "Pereira",
    "Gallagher",
    "Lund",
    "Arrieta",
]

# Squad template: 25 players. Starters are the first XI for each formation (see FORMATION_STARTERS).
SQUAD_POSITIONS: list[Position] = [
    Position.GK,
    Position.GK,
    Position.GK,
    Position.RB,
    Position.RB,
    Position.CB,
    Position.CB,
    Position.CB,
    Position.CB,
    Position.LB,
    Position.LB,
    Position.DM,
    Position.DM,
    Position.CM,
    Position.CM,
    Position.CM,
    Position.AM,
    Position.AM,
    Position.RW,
    Position.RW,
    Position.LW,
    Position.LW,
    Position.ST,
    Position.ST,
    Position.ST,
]

FORMATION_STARTERS: dict[str, list[Position]] = {
    "4-3-3": [
        Position.GK,
        Position.RB,
        Position.CB,
        Position.CB,
        Position.LB,
        Position.DM,
        Position.CM,
        Position.CM,
        Position.RW,
        Position.ST,
        Position.LW,
    ],
    "4-2-3-1": [
        Position.GK,
        Position.RB,
        Position.CB,
        Position.CB,
        Position.LB,
        Position.DM,
        Position.DM,
        Position.RW,
        Position.AM,
        Position.LW,
        Position.ST,
    ],
    "4-4-2": [
        Position.GK,
        Position.RB,
        Position.CB,
        Position.CB,
        Position.LB,
        Position.RW,
        Position.CM,
        Position.CM,
        Position.LW,
        Position.ST,
        Position.ST,
    ],
    "3-5-2": [
        Position.GK,
        Position.CB,
        Position.CB,
        Position.CB,
        Position.RB,
        Position.DM,
        Position.CM,
        Position.CM,
        Position.LB,
        Position.ST,
        Position.ST,
    ],
}

# Attribute emphasis per position: (pace, passing, finishing, defending, work_rate, aggression, composure)
_POSITION_BIAS: dict[Position, tuple[int, ...]] = {
    Position.GK: (-25, -5, -40, 10, -10, -10, 10),
    Position.RB: (8, 0, -15, 8, 8, 2, 0),
    Position.LB: (8, 0, -15, 8, 8, 2, 0),
    Position.CB: (-5, -3, -20, 15, 3, 8, 3),
    Position.DM: (-3, 6, -10, 10, 8, 6, 4),
    Position.CM: (0, 10, -3, 2, 8, 0, 6),
    Position.AM: (3, 12, 6, -10, 2, -5, 8),
    Position.RW: (12, 4, 6, -12, 3, -5, 2),
    Position.LW: (12, 4, 6, -12, 3, -5, 2),
    Position.ST: (6, -2, 15, -15, 0, 3, 6),
}


class PlayerAttributes(Model):
    pace: int = Field(ge=1, le=99)
    passing: int = Field(ge=1, le=99)
    finishing: int = Field(ge=1, le=99)
    defending: int = Field(ge=1, le=99)
    work_rate: int = Field(ge=1, le=99)
    aggression: int = Field(ge=1, le=99)
    composure: int = Field(ge=1, le=99)


class CareerStats(Model):
    appearances: int = Field(ge=0)
    goals: int = Field(ge=0)
    assists: int = Field(ge=0)


class Player(Model):
    player_id: str
    name: str
    short_name: str
    pos: Position
    shirt: int = Field(ge=1, le=99)
    age: int = Field(ge=16, le=40)
    top_speed_kmh: float = Field(gt=0, description="Personal top speed; caps simulated sprints.")
    attributes: PlayerAttributes
    career: CareerStats


class ClubStyle(Model):
    """Tactical tendencies the simulator uses; all in [0, 1]."""

    press_intensity: float = Field(ge=0, le=1)
    tempo: float = Field(ge=0, le=1)
    directness: float = Field(ge=0, le=1)
    width: float = Field(ge=0, le=1)


class Club(Model):
    club_id: str
    name: str
    short_name: str
    colors: list[str] = Field(min_length=2, max_length=2)
    formation: str
    strength: float = Field(gt=0, description="Expected goals per 90 against an average side.")
    style: ClubStyle
    venue: str
    players: list[Player] = Field(min_length=len(SQUAD_POSITIONS), max_length=len(SQUAD_POSITIONS))

    def team_info(self) -> TeamInfo:
        return TeamInfo(
            club_id=self.club_id,
            name=self.name,
            short_name=self.short_name,
            colors=self.colors,
            formation=self.formation,
        )

    def player(self, player_id: str) -> Player:
        return next(p for p in self.players if p.player_id == player_id)

    def starting_xi(self) -> list[Player]:
        """Best available player for each slot of the formation, by overall rating."""
        remaining = sorted(self.players, key=overall, reverse=True)
        xi: list[Player] = []
        for slot in FORMATION_STARTERS[self.formation]:
            pick = next(p for p in remaining if p.pos == slot)
            remaining.remove(pick)
            xi.append(pick)
        return xi

    def lineup(self) -> list[LineupPlayer]:
        starters = {p.player_id for p in self.starting_xi()}
        return [
            LineupPlayer(
                player_id=p.player_id, name=p.name, pos=p.pos, shirt=p.shirt, starter=p.player_id in starters
            )
            for p in self.players
        ]


class League(Model):
    roster_version: str = ROSTER_VERSION
    seed: int
    name: str
    clubs: list[Club]

    def club(self, club_id: str) -> Club:
        return next(c for c in self.clubs if c.club_id == club_id)


def overall(player: Player) -> float:
    a = player.attributes
    weights = {
        Position.GK: (0.05, 0.15, 0.0, 0.5, 0.1, 0.0, 0.2),
        Position.CB: (0.1, 0.1, 0.0, 0.5, 0.1, 0.1, 0.1),
        Position.ST: (0.2, 0.1, 0.45, 0.0, 0.1, 0.05, 0.1),
    }.get(player.pos, (0.15, 0.3, 0.15, 0.15, 0.15, 0.0, 0.1))
    values = (a.pace, a.passing, a.finishing, a.defending, a.work_rate, a.aggression, a.composure)
    return sum(w * v for w, v in zip(weights, values, strict=True))


def _clamp(value: float, lo: int = 1, hi: int = 99) -> int:
    return int(max(lo, min(hi, round(value))))


def _make_player(
    rng: random.Random,
    club_id: str,
    pos: Position,
    shirt: int,
    quality: float,
    used_names: set[str],
    club_surnames: set[str],
) -> Player:
    # Full names are unique league-wide and surnames unique within a club, so commentary that uses
    # short names is never ambiguous.
    while True:
        first, last = rng.choice(FIRST_NAMES), rng.choice(SURNAMES)
        if f"{first} {last}" not in used_names and last not in club_surnames:
            used_names.add(f"{first} {last}")
            club_surnames.add(last)
            break
    bias = _POSITION_BIAS[pos]
    base = [rng.gauss(quality, 7) + b for b in bias]
    attrs = PlayerAttributes(
        pace=_clamp(base[0]),
        passing=_clamp(base[1]),
        finishing=_clamp(base[2]),
        defending=_clamp(base[3]),
        work_rate=_clamp(base[4]),
        aggression=_clamp(base[5]),
        composure=_clamp(base[6]),
    )
    age = rng.randint(18, 35)
    seasons = max(0, age - 18)
    apps = int(seasons * rng.uniform(12, 34))
    goal_rate = {
        Position.ST: 0.42,
        Position.RW: 0.25,
        Position.LW: 0.25,
        Position.AM: 0.2,
        Position.CM: 0.08,
        Position.DM: 0.04,
    }.get(pos, 0.02)
    goals = int(apps * goal_rate * rng.uniform(0.6, 1.4))
    assists = int(apps * goal_rate * 0.7 * rng.uniform(0.5, 1.5))
    # Nudge ~1 in 8 players to sit one short of a round milestone so milestone insights can fire.
    if rng.random() < 0.125 and apps > 60:
        apps = (apps // 50 + 1) * 50 - 1
    if rng.random() < 0.125 and goals > 20:
        goals = (goals // 25 + 1) * 25 - 1
    top_speed = round(26.0 + (attrs.pace / 99) * 10.0 + rng.uniform(-0.8, 0.8), 1)
    return Player(
        player_id=f"{club_id}-{shirt:02d}",
        name=f"{first} {last}",
        short_name=last,
        pos=pos,
        shirt=shirt,
        age=age,
        top_speed_kmh=top_speed,
        attributes=attrs,
        career=CareerStats(appearances=apps, goals=goals, assists=assists),
    )


def generate_league(seed: int = DEFAULT_SEED) -> League:
    rng = random.Random(seed)
    used_names: set[str] = set()
    clubs = []
    formations = list(FORMATION_STARTERS)
    for i, (town, suffix, code) in enumerate(CLUB_NAMES):
        quality = rng.uniform(58, 80)
        shirts = rng.sample(range(2, 40), len(SQUAD_POSITIONS) - 1)
        players = []
        club_surnames: set[str] = set()
        for idx, pos in enumerate(SQUAD_POSITIONS):
            shirt = 1 if idx == 0 else shirts[idx - 1]
            players.append(_make_player(rng, code, pos, shirt, quality, used_names, club_surnames))
        clubs.append(
            Club(
                club_id=code,
                name=f"{town} {suffix}",
                short_name=town,
                colors=list(PALETTES[i]),
                formation=rng.choice(formations),
                strength=round(0.9 + (quality - 58) / 22 * 1.0 + rng.uniform(-0.1, 0.1), 2),
                style=ClubStyle(
                    press_intensity=round(rng.uniform(0.25, 0.85), 2),
                    tempo=round(rng.uniform(0.3, 0.9), 2),
                    directness=round(rng.uniform(0.2, 0.8), 2),
                    width=round(rng.uniform(0.3, 0.8), 2),
                ),
                venue=f"{town} {rng.choice(['Park', 'Ground', 'Stadium', 'Arena', 'Lane', 'Road'])}",
                players=players,
            )
        )
    return League(seed=seed, name="Synthetic Premier Division", clubs=clubs)


def save_league(league: League, path: Path = DEFAULT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(league.model_dump_json(indent=1) + "\n", encoding="utf-8", newline="\n")
    return path


def load_league(path: Path = DEFAULT_PATH) -> League:
    return League.model_validate(json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    print(save_league(generate_league()))
