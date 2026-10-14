"""The Rust tracking build (`pitchlens_core.build_tracking`) against the Python reference
(`generate_tracking_py`): same frames, same players on the pitch, same positions (docs/rust-core-plan.md
§5). `scripts/parity_core.py` runs the same check over every stored sample and 100 generated matches.

Skipped when the extension isn't built (or is an older build without `build_tracking`)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pitchlens_core = pytest.importorskip("pitchlens_core")
if not hasattr(pitchlens_core, "build_tracking"):
    pytest.skip("pitchlens_core has no build_tracking (rebuild the core)", allow_module_level=True)

from pitchlens.live.store import load_match_dir  # noqa: E402
from pitchlens.schema.events import EventType, MatchEvent, MatchMeta  # noqa: E402
from pitchlens.sim.rosters import League, generate_league, load_league  # noqa: E402
from pitchlens.sim.simulator import simulate  # noqa: E402
from pitchlens.sim.storylines import build, default_fixture  # noqa: E402
from pitchlens.sim.tracking import Tracking, _pack, generate_tracking_py  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[3] / "data" / "samples"
TOLERANCE = 1e-3  # metres; far below anything visible or measurable


def build_rust(meta: MatchMeta, events: list[MatchEvent], league: League) -> Tracking:
    """The Rust build called directly, so this test runs whatever PITCHLENS_CORE says."""
    player_ids, packed = _pack(meta, events, league)
    ms, period, ball, xy = pitchlens_core.build_tracking(**packed)
    return Tracking(match_id=meta.match_id, ms=ms, period=period, ball=ball, player_ids=player_ids, xy=xy)


def assert_parity(meta: MatchMeta, events: list[MatchEvent], league: League) -> None:
    py = generate_tracking_py(meta, events, league)
    rs = build_rust(meta, events, league)
    assert rs.match_id == py.match_id
    assert rs.player_ids == py.player_ids
    for name in ("ms", "period", "ball", "xy"):
        a, b = getattr(py, name), getattr(rs, name)
        assert (a.dtype, a.shape) == (b.dtype, b.shape), name
    np.testing.assert_array_equal(rs.ms, py.ms)
    np.testing.assert_array_equal(rs.period, py.period)
    off_pitch = np.isnan(py.xy)
    np.testing.assert_array_equal(np.isnan(rs.xy), off_pitch)  # who is on the pitch, frame by frame
    assert not np.isnan(rs.ball).any()
    assert float(np.abs(rs.ball - py.ball).max(initial=0.0)) <= TOLERANCE
    assert float(np.abs(rs.xy[~off_pitch] - py.xy[~off_pitch]).max(initial=0.0)) <= TOLERANCE


def stored(match_id: str):
    record = load_match_dir(SAMPLES / match_id)
    if record is None:
        pytest.skip(f"stored sample {match_id} not found")
    return record


@pytest.mark.parametrize("match_id", ["SYN-NHR-SVC-1000", "SYN-SGV-ELR-1003"])
def test_stored_samples_match_the_python_reference(match_id: str) -> None:
    record = stored(match_id)
    assert_parity(record.meta, record.events, load_league())


@pytest.mark.parametrize(("storyline", "seed"), [("comeback", 7), ("red_card_turn", 3), ("chaos_derby", 11)])
def test_generated_matches_match_the_python_reference(storyline: str, seed: int) -> None:
    league = generate_league()
    home, away = default_fixture(league, storyline)
    match = simulate(league, home, away, seed=seed, storyline=build(league, storyline, home, away))
    if storyline == "red_card_turn":  # the sending-off path: a slot and its anchor leave the shape
        assert any(e.type is EventType.CARD and e.outcome in ("red", "second_yellow") for e in match.events)
    assert any(e.type is EventType.SUBSTITUTION for e in match.events)
    assert_parity(match.meta, match.events, league)


def extra(type_: EventType, **fields: object) -> MatchEvent:
    return MatchEvent(
        event_id="x", match_id="x", seq=0, type=type_, period=1, match_ms=0, clock_ms=0, **fields
    )


def short_match(*extras: MatchEvent) -> tuple[MatchMeta, list[MatchEvent], League]:
    """The first minute of a match, then `extras` 200 ms apart, then the end of the period."""
    league = generate_league()
    home, away = default_fixture(league, "comeback")
    match = simulate(league, home, away, seed=7, storyline=build(league, "comeback", home, away))
    events = [e for e in match.events if e.match_ms <= 60_000]
    for e in [*extras, next(e for e in match.events if e.type is EventType.PERIOD_END)]:
        events.append(e.model_copy(update={"match_ms": events[-1].match_ms + 200, "seq": events[-1].seq + 1}))
    return match.meta, events, league


def test_substitution_and_red_card_in_a_short_match() -> None:
    club = generate_league().club("NHR")
    xi = club.starting_xi()
    bench = next(p.player_id for p in club.players if p not in xi)
    meta, events, league = short_match(
        extra(EventType.SUBSTITUTION, team_id="NHR", player_id=xi[5].player_id, related_player_id=bench),
        extra(EventType.CARD, team_id="NHR", player_id=xi[2].player_id, outcome="red"),
        extra(EventType.CARD, team_id="NHR", player_id=bench, outcome="second_yellow"),
    )
    assert_parity(meta, events, league)


def test_bad_events_fail_the_same_way() -> None:
    """Python raises StopIteration / KeyError for players who aren't on the pitch or in the league (the
    store catches both); Rust raises the same exceptions."""
    on_pitch = generate_league().club("NHR").starting_xi()[5].player_id
    for error, bad in (
        (
            StopIteration,
            extra(EventType.SUBSTITUTION, team_id="NHR", player_id="NOPE", related_player_id="X"),
        ),
        (
            KeyError,
            extra(EventType.SUBSTITUTION, team_id="NHR", player_id=on_pitch, related_player_id="NOPE"),
        ),
        (KeyError, extra(EventType.SUBSTITUTION, team_id="ZZZ", player_id=on_pitch, related_player_id="X")),
        (StopIteration, extra(EventType.CARD, team_id="NHR", player_id=None, outcome="red")),
    ):
        meta, events, league = short_match(bad)
        for build_fn in (generate_tracking_py, build_rust):
            with pytest.raises(error):
                build_fn(meta, events, league)
