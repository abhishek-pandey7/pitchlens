from pitchlens.ingest.validator import validate_batch

GOOD = {
    "event_id": "e-1",
    "match_id": "m1",
    "seq": 1,
    "type": "pass",
    "period": 1,
    "match_ms": 2000,
    "clock_ms": 2000,
    "start": {"x": 10, "y": 10},
}


def test_batch_validation_is_per_event_and_sorted() -> None:
    early = {**GOOD, "event_id": "e-0", "seq": 0, "match_ms": 1000, "clock_ms": 1000}
    bad_type = {**GOOD, "type": "teleport"}
    off_pitch = {**GOOD, "start": {"x": 200, "y": 10}}
    wrong_match = {**GOOD, "match_id": "m2"}
    result = validate_batch([GOOD, bad_type, early, off_pitch, wrong_match], match_id="m1")
    assert [e.event_id for e in result.accepted] == ["e-0", "e-1"]
    assert [r["index"] for r in result.rejected] == [1, 3, 4]
    assert any("type" in err for err in result.rejected[0]["errors"])
