"""Player-focus card (T-57): numbers, touch map in the attacking direction, moments in the fan's language."""

from __future__ import annotations

import pytest

from pitchlens.agents.tools import MatchView
from pitchlens.evals.common import match_view
from pitchlens.personalize.player_focus import focus_card
from pitchlens.personalize.profiles import PRESETS
from pitchlens.schema.events import Position


@pytest.fixture(scope="module")
def view() -> MatchView:
    return match_view("comeback", 7, tracking=True)


def by_position(view: MatchView, position: Position) -> list[str]:
    return [pid for pid, p in view.state.players.items() if p.position == position and p.touches > 20]


def test_card_numbers_match_the_state(view: MatchView) -> None:
    pid, p = next((pid, p) for pid, p in view.state.players.items() if p.goals == 2)
    card = focus_card(view.state, view.insights, pid, None)
    assert card["name"] == p.name and card["shirt"] and card["attack"]["goals"] == 2
    assert card["involvement"]["touches"] == p.touches
    assert sum(card["zones"]["counts"]) <= p.touches and sum(card["zones"]["counts"]) > 0
    assert card["physical"] and card["physical"]["distance_km"] > 0  # tracked match
    assert card["timeline"] and card["latest"] == card["timeline"][-1]
    assert any(m["type"] == "goal" for m in card["timeline"])


def test_touch_map_is_in_the_attacking_direction(view: MatchView) -> None:
    """Strikers live in the final third and centre-backs in their own, for both teams and both halves."""
    for pid in by_position(view, Position.ST):
        counts = focus_card(view.state, view.insights, pid, None)["zones"]["counts"]
        attacking_half = sum(c for i, c in enumerate(counts) if i % 6 >= 3)
        assert attacking_half > sum(counts) / 2, (pid, counts)
    for pid in by_position(view, Position.CB):
        counts = focus_card(view.state, view.insights, pid, None)["zones"]["counts"]
        own_half = sum(c for i, c in enumerate(counts) if i % 6 < 3)
        assert own_half > sum(counts) / 2, (pid, counts)


def test_moments_are_written_in_the_fans_language(view: MatchView) -> None:
    pid = next(pid for pid, p in view.state.players.items() if p.goals == 2)
    spanish = next(p for p in PRESETS if p.language == "es")
    texts = [m["text"] for m in focus_card(view.state, view.insights, pid, spanish)["timeline"]]
    assert any("¡GOOOL!" in t for t in texts)


def test_unknown_player_and_untracked_match() -> None:
    untracked = match_view("comeback", 7)
    pid = next(iter(untracked.state.players))
    assert focus_card(untracked.state, untracked.insights, pid, None)["physical"] is None
    with pytest.raises(KeyError):
        focus_card(untracked.state, untracked.insights, "nobody", None)
