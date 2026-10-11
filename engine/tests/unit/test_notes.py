"""Analyst tactical notes (T-122): parsing, trigger policy, reflection, live publishing. Offline."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from pitchlens.agents.clients import OfflineChatClient
from pitchlens.agents.desk import AgentDesk
from pitchlens.agents.notes import NOTE_KEY, TacticalNote, TacticalNotes, note_enabled, parse_note
from pitchlens.agents.tools import MatchView
from pitchlens.explain.engine import explain_match
from pitchlens.live.hub import Hub
from pitchlens.live.store import MatchStore
from pitchlens.narrate.templates import NarrationContext, render
from pitchlens.personalize.profiles import PRESETS
from pitchlens.schema.events import EventType
from pitchlens.schema.insights import Entities, Evidence, Insight, InsightType
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture


@pytest.fixture(scope="module")
def view() -> MatchView:
    league = generate_league()
    home, away = default_fixture(league, "comeback")
    match = simulate(league, home, away, seed=7, storyline=build(league, "comeback", home, away))
    return MatchView(*explain_match(match.meta, match.events))


def scripted(**scripts: list[Any]) -> Any:
    def make(role: str, tier: Any) -> OfflineChatClient:
        return (
            OfflineChatClient(scripts[role], role=role) if role in scripts else OfflineChatClient(role=role)
        )

    return make


def note(text: str, team: str = "Southvale City") -> str:
    return json.dumps({"found": True, "team": team, "note": text})


# ---------------------------------------------------------------- parsing and switches
def test_parse_note() -> None:
    assert parse_note('Here: {"found": true, "team": "X", "note": "Y presses."}') == TacticalNote(
        found=True, team="X", note="Y presses."
    )
    assert parse_note("Southvale are pressing much higher after the break.").found  # prose is the note
    assert parse_note('{"found": "maybe?"') is None and parse_note("ok") is None


def test_note_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_NOTES", "auto")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert not note_enabled()
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    assert note_enabled()
    monkeypatch.setenv("AGENT_NOTES", "off")
    assert not note_enabled()


# ---------------------------------------------------------------- triggers
def fake_session(match_ms: int, *, period: int = 1, finished: bool = False) -> Any:
    state = SimpleNamespace(match_ms=match_ms, clock_ms=match_ms, period=period, finished=finished)
    return SimpleNamespace(match_id="m", state=state)


def moment(kind: InsightType, salience: float) -> Insight:
    return Insight(
        insight_id="i", match_id="m", match_ms=0, clock_ms=0, period=1, type=kind, salience=salience,
        entities=Entities(teams=["NHR"]), headline_key=f"{kind.value}.x",
        evidence=[Evidence(metric="m", value=1.0)],
    )  # fmt: skip


def test_trigger_policy() -> None:
    notes = TacticalNotes(min_gap_s=0, min_match_min=10, max_notes=2)
    half_time = SimpleNamespace(type=EventType.PERIOD_END, period=1)
    assert notes.trigger(fake_session(2_800_000), [], half_time) == "half-time (45+2')"
    assert notes.trigger(fake_session(600_000), [moment(InsightType.GOAL, 0.9)], None) is None  # not tactical
    assert notes.trigger(fake_session(600_000), [moment(InsightType.PRESSURE_CHANGE, 0.3)], None) is None
    assert notes.trigger(fake_session(600_000), [moment(InsightType.PRESSURE_CHANGE, 0.5)], None)

    tracked: list[Any] = []
    session = fake_session(600_000)
    session.track = lambda work: (tracked.append(work), work.close())
    notes.observe(session, [moment(InsightType.MOMENTUM_SHIFT, 0.6)], None)
    assert len(tracked) == 1
    later = fake_session(900_000)  # 5 match minutes later, and a note is still running
    assert notes.trigger(later, [moment(InsightType.MOMENTUM_SHIFT, 0.6)], None) is None
    notes._matches["m"].running = False
    assert notes.trigger(later, [moment(InsightType.MOMENTUM_SHIFT, 0.6)], None) is None  # under 10 minutes
    assert notes.trigger(fake_session(1_300_000), [moment(InsightType.MOMENTUM_SHIFT, 0.6)], None)
    notes._matches["m"].count = 2
    assert (
        notes.trigger(fake_session(5_000_000, period=2), [moment(InsightType.MOMENTUM_SHIFT, 0.6)], None)
        is None
    )  # cap
    assert notes.trigger(fake_session(5_000_000, period=2, finished=True), [], half_time) is None


# ---------------------------------------------------------------- writing a note
async def test_offline_note_is_published_as_an_agent_insight(view: MatchView) -> None:
    outcome = await TacticalNotes(client=scripted()).write(lambda: view, "pressure_change at 74'", number=3)
    insight = outcome.published
    assert insight is not None and insight.type is InsightType.TACTICAL_NOTE
    assert insight.author == "agent:analyst" and insight.headline_key == NOTE_KEY
    assert insight.insight_id.endswith("-note-3") and insight.entities.teams
    assert "pressing harder" in insight.evidence[0].value and insight.evidence[0].source == "agent:analyst"
    assert [s["agent"] for s in outcome.trace["steps"]] == ["analyst", "fact_checker", "safety"]
    assert outcome.trace["source"] == "published"


async def test_fact_checker_feedback_gets_one_revision(view: MatchView) -> None:
    make = scripted(
        tactical=[
            ("get_metric", {"metric": "press_rate"}),
            note("Southvale pressed harder in the second half."),
            note("Southvale pressed harder late in the match."),
        ],
        checker=[
            json.dumps({"verdict": "revise", "problems": ["timing: both periods are in the first half"]}),
            json.dumps({"verdict": "approve", "problems": []}),
        ],
    )
    outcome = await TacticalNotes(client=make).write(lambda: view, "half-time")
    assert outcome.published is not None and outcome.trace["revisions"] == 1
    assert outcome.published.evidence[0].value == "Southvale pressed harder late in the match."
    assert [s.get("verdict") for s in outcome.trace["steps"] if s["agent"] == "fact_checker"] == [
        "revise",
        "approve",
    ]


async def test_invented_numbers_are_dropped_after_one_retry(view: MatchView) -> None:
    make = scripted(
        tactical=[
            ("get_metric", {"metric": "press_rate"}),
            note("Press rate hit 41.5."),
            note("Press rate hit 41.5 again."),
        ]
    )
    outcome = await TacticalNotes(client=make).write(lambda: view, "momentum_shift")
    assert outcome.published is None and outcome.trace["source"] == "dropped"
    assert "41.5" in outcome.trace["steps"][1]["problems"][0]  # caught by the rules stage, no judge call


async def test_no_pattern_and_unreadable_replies_are_dropped(view: MatchView) -> None:
    quiet = await TacticalNotes(client=scripted(tactical=[json.dumps({"found": False})])).write(
        lambda: view, "x"
    )
    assert quiet.published is None and quiet.trace["answer"] == "no clear pattern right now"
    junk = await TacticalNotes(client=scripted(tactical=["{oops"])).write(lambda: view, "x")
    assert junk.published is None and "unreadable" in junk.trace["answer"]


# ---------------------------------------------------------------- live session
async def test_live_replay_gets_notes_in_the_feed_but_not_from_catch_up() -> None:
    hub = Hub(MatchStore(data_dirs=[]), narrate=False)
    desk = AgentDesk(lambda: hub, notes=TacticalNotes(min_gap_s=0, client=scripted()))
    hub.session_hooks.append(desk.attach)
    record = hub.store.generate(seed=7, storyline="comeback", tracking=False)
    session = hub.session(record.match_id)
    try:
        await hub.start_replay(record.match_id, speed=600, frames=False, from_ms=60 * 60_000)
        for _ in range(600):
            await asyncio.sleep(0.05)
            if session.state.finished:
                break
        await session.drain_narrations()
        notes = [i for i in session.engine.insights if i.author == "agent:analyst"]
        assert notes, "expected at least one note from the live part of the replay"
        assert all(
            n.match_ms >= 60 * 60_000 for n in notes
        )  # nothing from the 0'-60' catch-up (incl. half-time)
        raw = session.channels[next(iter(session.channels))]
        assert any(item["insight"]["type"] == "tactical_note" for item in raw.delivered)
        traces = desk.traces(record.match_id)
        assert traces and traces[0]["kind"] == "tactical_note"

        ctx = NarrationContext.from_state(session.state)
        spanish = next(p for p in PRESETS if p.language == "es")
        assert render(notes[0], ctx, spanish).text.startswith("Análisis: ")
    finally:
        await hub.shutdown()
