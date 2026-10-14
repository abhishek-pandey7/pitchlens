"""Overlay cue contract: timed, machine-readable instructions a broadcast renderer can consume."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from .common import SCHEMA_VERSION, Model


class Region(StrEnum):
    LOWER_THIRD = "lower_third"
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    PLAYER_ANCHOR = "player_anchor"
    FULL_BAR = "full_bar"


class CueTemplate(StrEnum):
    PASS_CARD = "pass_card"
    SHOT_SPEED = "shot_speed"
    PLAYER_TAG = "player_tag"
    MOMENTUM_BAR = "momentum_bar"
    LOWER_THIRD = "lower_third"
    CHAOS_METER = "chaos_meter"
    WP_SWING = "wp_swing"
    WHAT_IF = "what_if"
    FUTURES_BAR = "futures_bar"


class CueAnchor(Model):
    player_id: str | None = None


class CueText(Model):
    lang: str
    headline: str
    body: str = ""
    source: str = Field(default="template", description="template | llm")


class CueTransitions(Model):
    in_: str = Field(default="slide_up", alias="in")
    out: str = "fade"


class OverlayCue(Model):
    schema_version: str = SCHEMA_VERSION
    cue_id: str
    insight_id: str | None = None
    profile_id: str
    match_id: str
    start_ms: int = Field(ge=0, description="Match clock (ms) at which the cue should appear.")
    duration_ms: int = Field(gt=0)
    priority: int = Field(ge=0, le=100)
    region: Region
    template: CueTemplate
    anchor: CueAnchor | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    text: CueText
    transitions: CueTransitions = Field(default_factory=CueTransitions)
