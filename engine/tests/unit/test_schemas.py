import json

import pytest
from pydantic import ValidationError

from pitchlens.schema import EventType, Insight, MatchEvent, OverlayCue, Profile
from pitchlens.schema.export_jsonschema import PUBLIC_MODELS, export

PASS_EVENT = {
    "event_id": "e-000412",
    "match_id": "SYN-2026-0001",
    "seq": 412,
    "type": "pass",
    "period": 1,
    "match_ms": 1834200,
    "clock_ms": 1834200,
    "team_id": "NHR",
    "player_id": "NHR-08",
    "possession_id": 57,
    "start": {"x": 38.2, "y": 22.5},
    "end": {"x": 61.0, "y": 40.1},
    "outcome": "complete",
    "recipient_id": "NHR-10",
    "under_pressure": True,
    "body_part": "right_foot",
    "height": "ground",
    "qualifiers": ["through_ball"],
}


def test_event_round_trip() -> None:
    event = MatchEvent.model_validate(PASS_EVENT)
    assert event.type is EventType.PASS
    again = MatchEvent.model_validate_json(event.model_dump_json())
    assert again == event


def test_event_rejects_unknown_fields_and_off_pitch_points() -> None:
    with pytest.raises(ValidationError):
        MatchEvent.model_validate({**PASS_EVENT, "surprise": 1})
    with pytest.raises(ValidationError):
        MatchEvent.model_validate({**PASS_EVENT, "end": {"x": 120.0, "y": 10.0}})


def test_insight_evidence_uses_from_alias() -> None:
    insight = Insight.model_validate(
        {
            "insight_id": "i-0091",
            "match_id": "SYN-2026-0001",
            "match_ms": 3921000,
            "period": 2,
            "type": "momentum_shift",
            "salience": 0.82,
            "headline_key": "momentum_shift.press_intensified",
            "evidence": [{"metric": "ppda", "team": "SVC", "from": 14.2, "to": 6.1, "window_min": 8}],
        }
    )
    assert insight.evidence[0].from_ == 14.2
    assert json.loads(insight.model_dump_json(by_alias=True))["evidence"][0]["from"] == 14.2


def test_insight_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        Insight.model_validate(
            {
                "insight_id": "i-1",
                "match_id": "m",
                "match_ms": 0,
                "period": 1,
                "type": "goal",
                "salience": 1.0,
                "headline_key": "goal.scored",
                "evidence": [],
            }
        )


def test_cue_transitions_alias() -> None:
    cue = OverlayCue.model_validate(
        {
            "cue_id": "c-1",
            "profile_id": "p",
            "match_id": "m",
            "start_ms": 100,
            "duration_ms": 6000,
            "priority": 70,
            "region": "lower_third",
            "template": "pass_card",
            "text": {"lang": "en", "headline": "Line-breaking pass"},
            "transitions": {"in": "slide_up", "out": "fade"},
        }
    )
    assert cue.transitions.in_ == "slide_up"


def test_profile_defaults_and_language_pattern() -> None:
    assert Profile(profile_id="p1").persona == "casual"
    with pytest.raises(ValidationError):
        Profile(profile_id="p2", language="english")


def test_jsonschema_export(tmp_path) -> None:
    paths = export(tmp_path)
    assert len(paths) == len(PUBLIC_MODELS)
    for path in paths:
        assert json.loads(path.read_text(encoding="utf-8"))["type"] == "object"
