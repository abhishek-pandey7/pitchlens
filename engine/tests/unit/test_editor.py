"""Editor recaps (T-126): the group chat, the reflection loop, per-fan adaptation, stage views. Offline."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pitchlens.agents.clients import OfflineChatClient
from pitchlens.agents.editor import (
    MAX_REVISIONS,
    EditorRoom,
    Master,
    RecapDraft,
    adapt,
    fact_recap,
    needs_narrator,
)
from pitchlens.agents.tools import MatchTools, MatchView
from pitchlens.agents.views import MatchViews, NotYet
from pitchlens.explain.engine import explain_match
from pitchlens.live.hub import Hub
from pitchlens.live.store import MatchStore
from pitchlens.personalize.profiles import PRESETS
from pitchlens.schema.events import EventType
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture


@pytest.fixture(scope="module")
def full() -> MatchView:
    league = generate_league()
    home, away = default_fixture(league, "comeback")
    match = simulate(league, home, away, seed=7, storyline=build(league, "comeback", home, away))
    return MatchView(*explain_match(match.meta, match.events))


@pytest.fixture(scope="module")
def clubs(full: MatchView) -> dict[str, str]:
    return {cid: t.name for cid, t in full.state.teams.items()}


def scripted(**scripts: list[Any]) -> Any:
    def make(role: str, tier: Any) -> OfflineChatClient:
        return (
            OfflineChatClient(scripts[role], role=role) if role in scripts else OfflineChatClient(role=role)
        )

    return make


def draft(headline: str, body: str, line: str = "Jonah Mensah was the standout.") -> str:
    return json.dumps({"headline": headline, "body": body, "player_line": line})


def profile(profile_id: str) -> Any:
    return next(p for p in PRESETS if p.profile_id == profile_id)


# ---------------------------------------------------------------- deterministic tools
def test_goals_and_player_of_the_match(full: MatchView) -> None:
    tools = MatchTools(lambda: full)
    goals = tools.get_goals()
    assert goals["score"] == "Northbridge Harriers 2-3 Southvale City" and len(goals["goals"]) == 5
    assert goals["goals"][-1]["score_after"].endswith("2-3 Southvale City")
    ranking = tools.player_of_the_match(limit=5)["ranking"]
    assert [r["points"] for r in ranking] == sorted((r["points"] for r in ranking), reverse=True)
    assert ranking[0]["team"] == "Southvale City" and ranking[0]["stats"]["on_winning_side"] == 1
    assert [c.tool for c in tools.ledger.calls] == ["get_goals", "player_of_the_match"]


# ---------------------------------------------------------------- the group chat
async def test_offline_group_chat_writes_an_approved_recap(full: MatchView) -> None:
    master = await EditorRoom(lambda: full, "ft", client=scripted()).write()
    assert master.approved and master.error is None and master.revisions == 0
    assert [(s["agent"], s.get("task")) for s in master.steps] == [
        ("editor", "fact sheet"),
        ("editor", "plan"),
        ("analyst", None),
        ("editor", "draft"),
        ("fact_checker", None),
    ]
    assert master.steps[2]["tool_calls"] == [  # the analyst's own calls, not the fact sheet's
        {"tool": "get_match_state", "args": {}, "ms": master.steps[2]["tool_calls"][0]["ms"], "error": None}
    ]
    assert master.draft is not None and "2-3" in master.draft.headline


async def test_the_editor_decides_whether_the_analyst_speaks(full: MatchView) -> None:
    make = scripted(
        editor=["The fact sheet tells the story.\nANALYST: none", draft("Late drama", "It ended 2-3.")]
    )
    master = await EditorRoom(lambda: full, "ft", client=make).write()
    assert master.approved
    assert [s["agent"] for s in master.steps] == ["editor", "editor", "editor", "fact_checker"]


async def test_fact_checker_feedback_drives_revisions(full: MatchView) -> None:
    make = scripted(
        editor=[
            "ANALYST: none",
            draft("Southvale win 2-3", "Southvale won it 2-3."),
            draft("Late show", "2-3."),
        ],
        checker=[
            json.dumps({"verdict": "revise", "problems": ["say who scored the winner"]}),
            json.dumps({"verdict": "approve", "problems": []}),
        ],
    )
    master = await EditorRoom(lambda: full, "ft", client=make).write()
    assert master.approved and master.revisions == 1
    assert master.draft is not None and master.draft.headline == "Late show"
    assert [s.get("task") for s in master.steps if s["agent"] == "editor"] == [
        "fact sheet", "plan", "draft", "revise",
    ]  # fmt: skip


async def test_invented_numbers_never_get_approved(full: MatchView) -> None:
    bad = draft("Southvale romp", "Southvale won it on 4.44 expected goals.")
    make = scripted(editor=["ANALYST: none", *[bad] * (MAX_REVISIONS + 1)])
    master = await EditorRoom(lambda: full, "ft", client=make).write()
    assert not master.approved and master.verdict == "revise" and master.revisions == MAX_REVISIONS
    checks = [s for s in master.steps if s["agent"] == "fact_checker"]
    assert len(checks) == MAX_REVISIONS + 1 and all(c["by"] == "rules" for c in checks)
    assert "4.44" in checks[0]["problems"][0]

    # The fan still gets a recap: the plain facts, in their language.
    recap = await adapt(master, profile("p-casual-es"), {})
    assert recap.source == "facts" and recap.lang == "es"
    assert recap.headline == "Final del partido: Northbridge Harriers 2-3 Southvale City"
    assert "Diego Ogunleye 90+4'" in recap.body


async def test_editor_failure_closes_the_room(full: MatchView) -> None:
    master = await EditorRoom(lambda: full, "ft", client=scripted(editor=["ANALYST: none"])).write()
    assert not master.approved and master.error and "exhausted" in master.error
    assert master.steps[-1]["agent"] == "editor" and master.steps[-1]["error"]


# ---------------------------------------------------------------- per-fan adaptation
@pytest.fixture
async def master(full: MatchView) -> Master:
    return await EditorRoom(lambda: full, "ft", client=scripted()).write()


async def test_english_analyst_reads_the_editors_recap(master: Master, clubs: dict[str, str]) -> None:
    analyst = profile("p-analyst-en")
    assert not needs_narrator(analyst)
    recap = await adapt(master, analyst, clubs)
    assert recap.source == "editor" and recap.lang == "en" and recap.steps == master.steps
    assert recap.player_of_match and recap.player_of_match["player"] == "Jonah Mensah"


async def test_narrator_adapts_and_the_fact_checker_approves(master: Master, clubs: dict[str, str]) -> None:
    spanish = draft("¡Remontada de Southvale!", "Southvale ganó 2-3 con un gol en el 90+4'.")
    recap = await adapt(master, profile("p-casual-es"), clubs, client=scripted(narrator=[spanish]))
    assert recap.source == "narrator" and recap.lang == "es" and recap.headline == "¡Remontada de Southvale!"
    assert [s["agent"] for s in recap.steps[len(master.steps) :]] == ["narrator", "fact_checker"]


async def test_narrator_gets_one_retry_then_falls_back(master: Master, clubs: dict[str, str]) -> None:
    invented = draft("¡Remontada!", "Southvale ganó con 937 pases.")
    embellished = draft("¡Remontada!", "Southvale ganó 2-3 con un gol de chilena.")
    make = scripted(
        narrator=[invented, embellished],
        checker=[json.dumps({"verdict": "revise", "problems": ["no bicycle kick in the recap"]})],
    )
    recap = await adapt(master, profile("p-casual-es"), clubs, client=make)
    assert recap.source == "editor" and recap.lang == "en"  # the verified English recap
    tail = recap.steps[len(master.steps) :]
    assert [s["agent"] for s in tail] == ["narrator", "narrator", "fact_checker"]
    assert tail[0]["unsupported_numbers"] == ["937"] and tail[2]["verdict"] == "revise"


def test_fact_recap_in_hindi(master: Master) -> None:
    recap = fact_recap(master, "hi")
    assert recap.headline.startswith("फ़ुल-टाइम: ") and "प्लेयर ऑफ़ द मैच" in recap.player_line
    assert isinstance(recap, RecapDraft)


# ---------------------------------------------------------------- stage views
async def test_half_time_view_stops_at_the_whistle_and_waits_for_live_play() -> None:
    hub = Hub(MatchStore(data_dirs=[]), narrate=False)
    record = hub.store.generate(seed=7, storyline="comeback", tracking=False)
    views = MatchViews(lambda: hub)
    try:
        ht = (await views.stage_view(record.match_id, "ht"))().state
        whistle = next(e for e in record.events if e.type == EventType.PERIOD_END and e.period == 1)
        assert ht.period == 1 and ht.match_ms == whistle.match_ms and ht.score == (1, 0)
        assert (await views.stage_view(record.match_id, "ft"))().state.finished

        hub.session(record.match_id)  # a live replay at kick-off: no spoilers, even from the cache
        for stage in ("ht", "ft"):
            with pytest.raises(NotYet):
                await views.stage_view(record.match_id, stage)
    finally:
        await hub.shutdown()
