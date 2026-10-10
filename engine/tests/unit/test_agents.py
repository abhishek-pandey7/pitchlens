"""Agent layer (T-120, T-121): analyst tools, grounded agent runs, offline client. No network."""

from __future__ import annotations

import pytest

from pitchlens.agents.clients import OfflineChatClient, chat_client
from pitchlens.agents.runtime import ANALYST_INSTRUCTIONS, build_agent, run_agent, supported_numbers
from pitchlens.agents.tools import Ledger, MatchTools, MatchView, ToolError
from pitchlens.explain.engine import explain_match
from pitchlens.narrate.grounding import unsupported_numbers
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture


@pytest.fixture(scope="module")
def view() -> MatchView:
    league = generate_league()
    home, away = default_fixture(league, "comeback")
    match = simulate(league, home, away, seed=7, storyline=build(league, "comeback", home, away))
    state, insights = explain_match(match.meta, match.events)
    return MatchView(state, insights)


@pytest.fixture
def tools(view: MatchView) -> MatchTools:
    return MatchTools(lambda: view)


# ---------------------------------------------------------------- tools
def test_match_state_reports_the_engine_scoreline(tools: MatchTools, view: MatchView) -> None:
    result = tools.get_match_state()
    home, away = view.state.score
    assert result["score"] == f"Northbridge Harriers {home}-{away} Southvale City"
    assert result["finished"] is True and set(result["win_probability"]) == {
        "Northbridge Harriers", "draw", "Southvale City",
    }  # fmt: skip


def test_comeback_press_rises_for_the_away_side(tools: MatchTools) -> None:
    change = tools.compare_periods("press_rate", 45, 60, 63, 78)["change"]
    assert change["Southvale City"] > 0 > change["Northbridge Harriers"]


def test_window_and_win_prob_at(tools: MatchTools) -> None:
    window = tools.get_window("xg", 60, 90)["teams"]
    assert window["Southvale City"]["end"] > window["Southvale City"]["start"]
    early = tools.win_prob_at(10)
    assert early["minute"] in {"10'", "11'"} and early["score"].endswith("0-0 Southvale City")


def test_what_if_recomputes_win_probability(tools: MatchTools, view: MatchView) -> None:
    result = tools.what_if(home_goals=1)
    home, away = view.state.score
    assert result["what_if"]["score"] == f"Northbridge Harriers {home + 1}-{away} Southvale City"
    with pytest.raises(ToolError):
        tools.what_if(home_goals=-9)


def test_players_by_name_and_physical_stats_need_tracking(tools: MatchTools, view: MatchView) -> None:
    scorer = next(p for p in view.state.players.values() if p.goals >= 2)
    card = tools.get_player_card(scorer.name)
    assert card["player"] == scorer.name and card["goals"] == scorer.goals
    assert card["distance_km"] is None  # no tracking frames in this view: unavailable, not zero
    with pytest.raises(ToolError, match="tracking"):
        tools.top_players("distance_km")
    assert tools.top_players("goals", limit=1)["leaders"][0]["player"] == scorer.name
    with pytest.raises(ToolError, match="unknown player"):
        tools.get_player_card("Nobody McNobody")


def test_bad_arguments_are_tool_errors_and_logged(tools: MatchTools) -> None:
    with pytest.raises(ToolError, match="unknown metric"):
        tools.get_metric("shots")
    with pytest.raises(ToolError, match="unknown team"):
        tools.get_insights(team="Arsenal")
    assert [c.tool for c in tools.ledger.calls] == ["get_metric", "get_insights"]
    assert all(c.error for c in tools.ledger.calls)


def test_insights_are_compact_and_filterable(tools: MatchTools) -> None:
    goals = tools.get_insights("goal", limit=10)["insights"]
    assert goals and all(i["headline"].startswith("goal.") for i in goals)
    assert "→" in goals[0]["evidence"]  # from→to as a short string, not nested objects
    late = tools.get_insights(since_minute=80, limit=15)["insights"]
    assert all(int(i["minute"].split("+")[0].rstrip("'")) >= 80 for i in late)


# ---------------------------------------------------------------- grounding of agent answers
def test_answer_numbers_must_come_from_tool_results(tools: MatchTools) -> None:
    tools.get_match_state()
    win = tools.get_match_state()["win_probability"]["Southvale City"]
    allowed = supported_numbers(Ledger(tools.ledger.calls), "What happened after 60 minutes?")
    good = f"Southvale won it: {round(win * 100)}% win probability, and nothing changed after 60 minutes."
    assert unsupported_numbers(good, allowed) == []
    assert unsupported_numbers("Southvale had 41 shots on target.", allowed) == ["41"]


# ---------------------------------------------------------------- agents (offline client)
async def test_offline_agent_answers_from_a_tool_and_is_grounded(tools: MatchTools) -> None:
    agent = build_agent(OfflineChatClient(), tools, name="analyst", instructions=ANALYST_INSTRUCTIONS)
    run = await run_agent(agent, tools, "What's the score?")
    assert run.grounded and "Northbridge Harriers 2-3 Southvale City" in run.text
    assert [c["tool"] for c in run.trace()["tool_calls"]] == ["get_match_state"]


async def test_scripted_agent_recovers_from_a_bad_tool_call(tools: MatchTools) -> None:
    script = [
        ("get_metric", {"metric": "shots"}),  # invalid: comes back as a normal "unavailable" result
        ("get_metric", {"metric": "press_rate"}),
        "Southvale pressed harder late on.",
    ]
    client = OfflineChatClient(script)
    agent = build_agent(client, tools, name="analyst", instructions=ANALYST_INSTRUCTIONS)
    run = await run_agent(agent, tools, "Who pressed more?")
    assert run.grounded and run.text == "Southvale pressed harder late on."
    assert [(c.tool, c.error is None) for c in run.ledger.calls] == [
        ("get_metric", False),
        ("get_metric", True),
    ]
    assert "unknown metric" in str(client.requests[1][-1].contents[0].result)


async def test_invented_number_is_flagged_and_failures_are_soft(tools: MatchTools) -> None:
    agent = build_agent(
        OfflineChatClient([("get_match_state", {}), "Southvale scored 7 goals."]), tools, name="analyst",
        instructions=ANALYST_INSTRUCTIONS,
    )  # fmt: skip
    run = await run_agent(agent, tools, "How many goals?")
    assert 7.0 in supported_numbers(run.ledger)  # a real value (Northbridge's shots)...
    assert run.grounded  # ...so a number check alone cannot catch this misattribution

    agent = build_agent(
        OfflineChatClient([("get_match_state", {}), "Southvale scored 41 goals."]), tools, name="analyst",
        instructions=ANALYST_INSTRUCTIONS,
    )  # fmt: skip
    run = await run_agent(agent, tools, "How many goals?")
    assert not run.grounded and run.unsupported == ["41"]

    broken = build_agent(OfflineChatClient([]), tools, name="analyst", instructions=ANALYST_INSTRUCTIONS)
    run = await run_agent(broken, tools, "Anything?")
    assert run.error and not run.grounded and run.text == ""


def test_chat_client_is_offline_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert isinstance(chat_client(provider="groq"), OfflineChatClient)
    assert isinstance(chat_client(provider="mock"), OfflineChatClient)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    assert not isinstance(chat_client(provider="groq"), OfflineChatClient)
