"""Versioned Pydantic schemas shared by every pipeline stage (events, insights, cues, profiles)."""

from .common import PITCH_LENGTH, PITCH_WIDTH, SCHEMA_VERSION, Point, Point3
from .enriched import AlertKind, EnrichedEvent, PhysicalAlert
from .events import (
    BodyPart,
    EventType,
    Height,
    LineupPlayer,
    MatchEvent,
    MatchMeta,
    Outcome,
    Position,
    PreMatch,
    TeamInfo,
    TrackedPlayer,
    TrackingFrame,
)
from .insights import Entities, Evidence, Insight, InsightType
from .overlays import CueAnchor, CueTemplate, CueText, OverlayCue, Region
from .profiles import FocusMetric, Grounding, Narration, Persona, PlayerFocus, Profile, Verbosity

__all__ = [
    "PITCH_LENGTH",
    "PITCH_WIDTH",
    "SCHEMA_VERSION",
    "AlertKind",
    "BodyPart",
    "CueAnchor",
    "CueTemplate",
    "CueText",
    "EnrichedEvent",
    "Entities",
    "EventType",
    "Evidence",
    "FocusMetric",
    "Grounding",
    "Height",
    "Insight",
    "InsightType",
    "LineupPlayer",
    "MatchEvent",
    "MatchMeta",
    "Narration",
    "Outcome",
    "OverlayCue",
    "Persona",
    "PhysicalAlert",
    "PlayerFocus",
    "Point",
    "Point3",
    "Position",
    "PreMatch",
    "Profile",
    "Region",
    "TeamInfo",
    "TrackedPlayer",
    "TrackingFrame",
    "Verbosity",
]
