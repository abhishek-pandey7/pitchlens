"""Match metadata, raw match events (`events.v1`) and tracking frames."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from .common import SCHEMA_VERSION, Model, Point, Point3


class EventType(StrEnum):
    MATCH_START = "match_start"
    PERIOD_START = "period_start"
    PERIOD_END = "period_end"
    MATCH_END = "match_end"
    PASS = "pass"
    CARRY = "carry"
    DRIBBLE = "dribble"
    SHOT = "shot"
    SAVE = "save"
    GOAL = "goal"
    OWN_GOAL = "own_goal"
    TACKLE = "tackle"
    INTERCEPTION = "interception"
    CLEARANCE = "clearance"
    BLOCK = "block"
    BALL_RECOVERY = "ball_recovery"
    PRESSURE = "pressure"
    FOUL = "foul"
    CARD = "card"
    SUBSTITUTION = "substitution"
    OFFSIDE = "offside"
    CORNER = "corner"
    FREE_KICK = "free_kick"
    THROW_IN = "throw_in"
    GOAL_KICK = "goal_kick"
    POSSESSION_CHANGE = "possession_change"
    INJURY_STOP = "injury_stop"
    VAR_CHECK = "var_check"


class Outcome(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    OUT = "out"
    OFFSIDE = "offside"
    SUCCESS = "success"
    FAIL = "fail"
    ON_TARGET = "on_target"
    OFF_TARGET = "off_target"
    BLOCKED = "blocked"
    GOAL = "goal"
    YELLOW = "yellow"
    SECOND_YELLOW = "second_yellow"
    RED = "red"


class BodyPart(StrEnum):
    RIGHT_FOOT = "right_foot"
    LEFT_FOOT = "left_foot"
    HEAD = "head"
    OTHER = "other"


class Height(StrEnum):
    GROUND = "ground"
    LOW = "low"
    HIGH = "high"


class Position(StrEnum):
    GK = "GK"
    RB = "RB"
    CB = "CB"
    LB = "LB"
    DM = "DM"
    CM = "CM"
    AM = "AM"
    RW = "RW"
    LW = "LW"
    ST = "ST"


class LineupPlayer(Model):
    player_id: str
    name: str
    pos: Position
    shirt: int = Field(ge=1, le=99)
    starter: bool = True


class TeamInfo(Model):
    club_id: str
    name: str
    short_name: str
    colors: list[str] = Field(min_length=2, max_length=2)
    formation: str


class PreMatch(Model):
    """Expected goals per 90 for each side, used as the win-probability prior."""

    home_strength: float = Field(gt=0)
    away_strength: float = Field(gt=0)


class MatchMeta(Model):
    schema_version: str = SCHEMA_VERSION
    match_id: str
    competition: str
    venue: str
    kickoff_utc: datetime
    weather: str
    referee: str
    home: TeamInfo
    away: TeamInfo
    lineups: dict[str, list[LineupPlayer]]
    pre_match: PreMatch
    seed: int | None = None
    storyline: str | None = None


class MatchEvent(Model):
    schema_version: str = SCHEMA_VERSION
    event_id: str
    match_id: str
    seq: int = Field(ge=0)
    type: EventType
    period: int = Field(ge=1, le=4)
    match_ms: int = Field(ge=0, description="Continuous elapsed play time since kick-off (ms), monotonic.")
    clock_ms: int = Field(
        ge=0, description="Football clock (ms): period 2 starts at 45:00, so stoppage time reads 45+2."
    )
    team_id: str | None = None
    player_id: str | None = None
    possession_id: int | None = None
    start: Point | None = None
    end: Point | None = None
    outcome: Outcome | None = None
    recipient_id: str | None = None
    under_pressure: bool = False
    pressure_player_id: str | None = None
    body_part: BodyPart | None = None
    height: Height | None = None
    qualifiers: list[str] = Field(default_factory=list)
    ball_speed_kmh: float | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(
        default=None,
        ge=0,
        description="How long the action took (ball travel for passes/shots, run for carries).",
    )
    related_player_id: str | None = Field(
        default=None, description="Second player involved: tackled player, fouled player, sub coming on."
    )
    ts_utc: datetime | None = None


class TrackedPlayer(Model):
    player_id: str
    x: float
    y: float


class TrackingFrame(Model):
    match_id: str
    match_ms: int = Field(ge=0)
    ball: Point3
    players: list[TrackedPlayer]
