"""What-if win probability (T-92) and the WebVTT / cue exports (T-93). Offline."""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

import pytest

from pitchlens.agents.tools import MatchView
from pitchlens.evals.common import match_view
from pitchlens.explain import counterfactual
from pitchlens.narrate.grounding import validate
from pitchlens.narrate.templates import NarrationContext, render
from pitchlens.personalize.profiles import PRESETS
from pitchlens.render import exports
from pitchlens.schema.events import EventType
from pitchlens.schema.overlays import OverlayCue
from pitchlens.schema.profiles import Persona, Profile
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture

LANGS = ["en", "es", "fr", "pt", "ar", "hi"]


@pytest.fixture(scope="module")
def red_card() -> tuple[MatchView, list[Any]]:
    league = generate_league()
    home, away = default_fixture(league, "red_card_turn")
    match = simulate(league, home, away, seed=4, storyline=build(league, "red_card_turn", home, away))
    return match_view("red_card_turn", 4), match.events


def evidence(insight: Any, metric: str) -> Any:
    return next(e for e in insight.evidence if e.metric == metric)


# ---------------------------------------------------------------- what if (T-92)
def test_big_chances_and_red_cards_carry_their_counterfactual(red_card: tuple[MatchView, list[Any]]) -> None:
    view, _ = red_card
    chances = [i for i in view.insights if i.type.value == "big_chance"]
    assert chances
    for i in chances:
        e = evidence(i, counterfactual.SCORED)
        assert e.team == i.entities.teams[0] and e.to > e.from_  # a goal never lowers your chances
    red = next(i for i in view.insights if i.headline_key == "card.red")
    e = evidence(red, counterfactual.ELEVEN)
    assert e.to > e.from_
    # Taking the red card away restores the win probability from just before it.
    assert e.to == pytest.approx(evidence(red, "win_prob").from_, abs=0.02)


def test_what_if_is_grounded_in_every_language(red_card: tuple[MatchView, list[Any]]) -> None:
    view, _ = red_card
    ctx = NarrationContext.from_state(view.state)
    moments = [
        next(i for i in view.insights if i.headline_key == "card.red"),
        next(i for i in view.insights if i.type.value == "big_chance"),
    ]
    for lang in LANGS:
        profile = Profile(profile_id=f"t-{lang}", persona=Persona.ANALYST, language=lang)
        for insight in moments:
            text = render(insight, ctx, profile).text
            metric = counterfactual.ELEVEN if insight.type.value == "card" else counterfactual.SCORED
            what_if = round(evidence(insight, metric).to * 100)
            assert f"{what_if}" in text, (lang, text)
            assert validate(text, insight, ctx, lang).passed, (lang, text)


# ---------------------------------------------------------------- exports (T-93)
def vtt_blocks(text: str) -> list[tuple[str, str, str]]:
    assert text.startswith("WEBVTT\n")
    found = re.findall(r"(\d\d:\d\d:\d\d\.\d{3}) --> (\d\d:\d\d:\d\d\.\d{3})\n(.+?)(?:\n\n|$)", text, re.S)
    return [(a, b, body) for a, b, body in found]


def test_chapters_follow_the_match_without_overlaps(red_card: tuple[MatchView, list[Any]]) -> None:
    view, events = red_card
    blocks = vtt_blocks(exports.chapters(view.state, view.insights, events, "es"))
    titles = [b[2] for b in blocks]
    assert titles[0] == "Saque inicial" and titles[-1] == "Final del partido"
    assert "Descanso · Segunda parte" in titles
    assert any("¡GOOOL!" in t for t in titles) and any("Roja directa" in t for t in titles)
    for (_, end, _), (start, _, _) in pairwise(blocks):
        assert end <= start  # chapters never overlap
    goals = sum(1 for e in events if e.type in (EventType.GOAL, EventType.OWN_GOAL))
    assert sum("GOOOL" in t for t in titles) == goals


def test_captions_follow_one_fans_feed_in_their_language(red_card: tuple[MatchView, list[Any]]) -> None:
    view, _ = red_card
    hindi = next(p for p in PRESETS if p.language == "hi")
    blocks = vtt_blocks(exports.captions(view.state, view.insights, hindi))
    assert blocks and all(a < b for a, b, _ in blocks)
    assert any("गोल" in body for _, _, body in blocks)
    for (_, end, _), (start, _, _) in pairwise(blocks):
        assert end <= start


def test_cue_export_respects_the_live_scheduling_rules(red_card: tuple[MatchView, list[Any]]) -> None:
    view, _ = red_card
    profile = next(p for p in PRESETS if p.profile_id == "p-casual-es")
    out = exports.cues(view.state, view.insights, profile)
    cues = [OverlayCue.model_validate(c) for c in out["cues"]]
    assert cues and out["time_base"] == "match_ms" and {c.text.lang for c in cues} == {"es"}
    starts = sorted(c.start_ms for c in cues if c.priority < 90)
    assert max(sum(1 for t in starts if s <= t < s + 60_000) for s in starts) <= profile.max_overlays_per_min
