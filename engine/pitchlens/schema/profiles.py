"""Fan profiles and generated narrations."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .common import SCHEMA_VERSION, Model

SUPPORTED_LANGUAGES = ("en", "es", "fr", "pt", "ar", "hi")


class Persona(StrEnum):
    CASUAL = "casual"
    ANALYST = "analyst"
    TACTICIAN = "tactician"
    KIDS = "kids"
    FANTASY = "fantasy"
    ACCESSIBILITY = "accessibility"


class Verbosity(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class FocusMetric(StrEnum):
    PASS_DIFFICULTY = "pass_difficulty"
    TOP_SPEED = "top_speed"
    XG = "xg"
    PPDA = "ppda"
    CONTROL_CHAOS = "control_chaos"


class PlayerFocus(Model):
    enabled: bool = False
    player_id: str | None = None


class Profile(Model):
    schema_version: str = SCHEMA_VERSION
    profile_id: str
    display_name: str | None = None
    persona: Persona = Persona.CASUAL
    language: str = Field(default="en", pattern=r"^[a-z]{2}$")
    favorite_clubs: list[str] = Field(default_factory=list)
    favorite_players: list[str] = Field(default_factory=list)
    focus_metric: FocusMetric | None = None
    player_focus_mode: PlayerFocus = Field(default_factory=PlayerFocus)
    spoiler_safe: bool = False
    verbosity: Verbosity = Verbosity.SHORT
    max_overlays_per_min: int = Field(default=3, ge=0, le=20)
    audio_commentary: bool = False


class Grounding(Model):
    passed: bool
    numbers_checked: int = 0
    violations: list[str] = Field(default_factory=list)


class Narration(Model):
    schema_version: str = SCHEMA_VERSION
    narration_id: str
    insight_id: str
    profile_id: str
    lang: str
    text: str
    source: str = Field(description="template | llm")
    grounding: Grounding
    latency_ms: int | None = None
