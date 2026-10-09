"""Shared primitives: pitch geometry, identifiers and the base model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

# Pitch dimensions in metres. Origin is the bottom-left corner from the home team's perspective;
# the home team attacks towards +x in the first period.
PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0


class Model(BaseModel):
    """Base model: strict about unknown fields so malformed producers fail loudly at ingest."""

    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, json_schema_serialization_defaults_required=True
    )


class Point(Model):
    x: float = Field(ge=0.0, le=PITCH_LENGTH)
    y: float = Field(ge=0.0, le=PITCH_WIDTH)


class Point3(Point):
    z: float = Field(default=0.0, ge=0.0)
