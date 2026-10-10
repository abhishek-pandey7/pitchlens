"""Validation for events arriving from external producers (`POST /ingest`, CV pipeline, partners)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from pitchlens.schema.events import MatchEvent


@dataclass
class IngestResult:
    accepted: list[MatchEvent] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)


def validate_batch(raw_events: list[dict[str, Any]], *, match_id: str | None = None) -> IngestResult:
    """Validate a batch independently per event; one bad event never sinks the batch."""
    result = IngestResult()
    for index, raw in enumerate(raw_events):
        try:
            event = MatchEvent.model_validate(raw)
        except ValidationError as exc:
            errors = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]
            result.rejected.append({"index": index, "errors": errors})
            continue
        if match_id is not None and event.match_id != match_id:
            result.rejected.append({"index": index, "errors": [f"match_id: expected {match_id}"]})
            continue
        result.accepted.append(event)
    result.accepted.sort(key=lambda e: (e.match_ms, e.seq))
    return result
