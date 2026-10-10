import json

import pytest

from pitchlens.explain.engine import explain_match
from pitchlens.narrate.grounding import numbers_in, validate
from pitchlens.narrate.mock import MockProvider
from pitchlens.narrate.narrator import Narrator
from pitchlens.narrate.prompts import build_request, parse_response, system_prompt
from pitchlens.narrate.provider import LLMError, LLMRequest, LLMResponse
from pitchlens.narrate.templates import NarrationContext, load_templates, render
from pitchlens.schema.insights import Insight, InsightType
from pitchlens.schema.profiles import SUPPORTED_LANGUAGES, Persona, Profile, Verbosity
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture
from pitchlens.sim.tracking import generate_tracking

LEAGUE = generate_league()
PROFILES = [
    Profile(profile_id=f"{p.value}-{lang}", persona=p, language=lang)
    for lang in SUPPORTED_LANGUAGES
    for p in (Persona.CASUAL, Persona.ANALYST)
]


def explain(storyline: str, seed: int, *, tracking: bool = False) -> tuple[NarrationContext, list[Insight]]:
    home, away = default_fixture(LEAGUE, storyline)
    match = simulate(LEAGUE, home, away, seed=seed, storyline=build(LEAGUE, storyline, home, away))
    frames = []
    if tracking:
        trk = generate_tracking(match.meta, match.events, LEAGUE)
        frames = [trk.frame(i) for i in range(len(trk))]
    state, insights = explain_match(match.meta, match.events, frames)
    return NarrationContext.from_state(state), insights


@pytest.fixture(scope="module")
def corpus() -> list[tuple[NarrationContext, list[Insight]]]:
    return [
        explain("comeback", 7),
        explain("star_player", 2, tracking=True),
        explain("red_card_turn", 4),
        explain("chaos_derby", 5),
        explain("pressure_surge", 7),
    ]


@pytest.fixture(scope="module")
def comeback(corpus) -> tuple[NarrationContext, list[Insight]]:
    return corpus[0]


def goals(insights: list[Insight]) -> list[Insight]:
    return [i for i in insights if i.type is InsightType.GOAL]


# ---------------------------------------------------------------- templates
def test_every_detected_moment_has_a_template_in_every_language(corpus) -> None:
    keys = {i.headline_key for _, insights in corpus for i in insights}
    assert len(keys) >= 25
    for lang in SUPPORTED_LANGUAGES:
        missing = keys - load_templates(lang).keys()
        assert not missing, (lang, missing)
        assert load_templates(lang).keys() == load_templates("en").keys(), lang


def test_template_text_is_grounded_and_complete_everywhere(corpus) -> None:
    rendered = 0
    for ctx, insights in corpus:
        for insight in insights:
            for profile in PROFILES:
                text = render(insight, ctx, profile).text
                rendered += 1
                assert "{" not in text and "}" not in text, text
                grounding = validate(text, insight, ctx, profile.language)
                assert grounding.passed, (
                    insight.headline_key,
                    profile.profile_id,
                    text,
                    grounding.violations,
                )
    assert rendered > 2000


def test_personas_and_locales_shape_the_line(comeback) -> None:
    ctx, insights = comeback
    winner = goals(insights)[-1]
    casual = render(winner, ctx, Profile(profile_id="c", persona=Persona.CASUAL, language="en"))
    analyst = render(winner, ctx, Profile(profile_id="a", persona=Persona.ANALYST, language="en"))
    assert casual.body == "" and "xG" in analyst.body and "win probability" in analyst.body
    spanish = render(winner, ctx, Profile(profile_id="s", persona=Persona.ANALYST, language="es")).text
    french = render(winner, ctx, Profile(profile_id="f", persona=Persona.ANALYST, language="fr")).text
    assert "¡GOOOL!" in spanish and "0,0" in spanish  # decimal comma
    assert " %" in french  # French percent spacing
    long_casual = Profile(profile_id="l", persona=Persona.CASUAL, language="en", verbosity=Verbosity.LONG)
    assert render(winner, ctx, long_casual).body


# ---------------------------------------------------------------- grounding validator
def test_validator_catches_invented_numbers_and_players(comeback) -> None:
    ctx, insights = comeback
    winner = goals(insights)[-1]
    scorer = ctx.player_names[winner.entities.players[0]]
    assert validate(f"GOAL! {scorer} wins it, 2-3.", winner, ctx).passed
    invented = validate(f"GOAL! {scorer} wins it, 2-3 - his 12th of the season.", winner, ctx)
    assert not invented.passed and any("12" in v for v in invented.violations)
    outsider = next(
        name
        for pid, name in ctx.player_names.items()
        if pid not in winner.entities.players and list(ctx.player_names.values()).count(name) == 1
    )
    stranger = validate(f"GOAL! {outsider} wins it, 2-3.", winner, ctx)
    assert not stranger.passed and any(outsider in v for v in stranger.violations)


def test_validator_handles_scripts_rounding_and_percentages(comeback) -> None:
    ctx, insights = comeback
    winner = goals(insights)[-1]
    wp = next(e for e in winner.evidence if e.metric == "win_prob")
    pct = round(100 * wp.to)
    assert validate(f"Win probability now {pct}%.", winner, ctx).passed
    devanagari = str(pct).translate(str.maketrans("0123456789", "०१२३४५६७८९"))
    assert validate(f"जीत की संभावना {devanagari}%", winner, ctx, "hi").passed
    arabic_indic = "٧٧"  # 77, not in the evidence
    assert not validate(f"احتمال الفوز {arabic_indic}%", winner, ctx, "ar").passed
    assert numbers_in("Probabilidad 0,41 y 2-1 al 90+3'") == [0.41, 2.0, 1.0, 90.0, 3.0]


# ---------------------------------------------------------------- prompts
def test_system_prompt_is_cache_stable_and_user_prompt_carries_the_evidence(comeback) -> None:
    ctx, insights = comeback
    profile = Profile(profile_id="p", persona=Persona.TACTICIAN, language="es")
    first, second = (build_request(i, ctx, profile) for i in goals(insights)[:2])
    assert first.system == second.system  # identical prefix for every moment: cacheable
    assert first.system == system_prompt(Persona.TACTICIAN, "es", Verbosity.SHORT)
    assert "Spanish" in first.system and "presión" in first.system
    payload = json.loads(first.user)
    assert payload["EVIDENCE"] and payload["REFERENCE"] and payload["ENTITIES"]["players"]
    assert first.json_schema and first.json_schema["required"] == ["text", "emphasis_entities"]
    retry = build_request(goals(insights)[0], ctx, profile, feedback=["number 12 is not in the evidence"])
    assert retry.system == first.system and "number 12" in retry.user


@pytest.mark.parametrize(
    "raw",
    [
        '{"text": "Hola", "emphasis_entities": ["Reyes"]}',
        '```json\n{"text": "Hola", "emphasis_entities": ["Reyes"]}\n```',
        'Sure! Here it is: {"text": "Hola", "emphasis_entities": ["Reyes"]} Hope it helps.',
    ],
)
def test_parse_response_tolerates_fences_and_prose(raw: str) -> None:
    assert parse_response(raw) == ("Hola", ["Reyes"])


# ---------------------------------------------------------------- narrator
async def test_narrator_uses_grounded_llm_text(comeback) -> None:
    ctx, insights = comeback
    narrator = Narrator(MockProvider())
    profile = Profile(profile_id="p-es", persona=Persona.CASUAL, language="es")
    narration = await narrator.narrate(goals(insights)[-1], profile, ctx)
    assert narration.source == "llm" and narration.grounding.passed and narration.lang == "es"
    assert narration.insight_id == goals(insights)[-1].insight_id


async def test_narrator_retries_then_falls_back_to_template(comeback) -> None:
    ctx, insights = comeback
    profile = Profile(profile_id="p-en", persona=Persona.CASUAL, language="en")
    winner = goals(insights)[-1]

    once = Narrator(MockProvider(faulty_first=1))
    recovered = await once.narrate(winner, profile, ctx)
    assert recovered.source == "llm" and recovered.grounding.passed and once.stats["llm_retried"] == 1

    stubborn = Narrator(MockProvider(faulty_first=5))
    fallback = await stubborn.narrate(winner, profile, ctx)
    assert fallback.source == "template" and fallback.grounding.passed
    assert "7 goals" not in fallback.text and stubborn.stats["template_fallback"] == 1


async def test_provider_errors_fall_back_to_template(comeback) -> None:
    class Broken:
        name = "broken"

        async def generate(self, request: LLMRequest) -> LLMResponse:
            raise LLMError("quota exceeded")

    ctx, insights = comeback
    narrator = Narrator(Broken())
    narration = await narrator.narrate(goals(insights)[0], Profile(profile_id="p"), ctx)
    assert narration.source == "template" and narrator.stats["provider_errors"] == 1
