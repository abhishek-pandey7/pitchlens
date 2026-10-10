"""Persona prompts (§10, T-53).

The system prompt depends only on persona, language and verbosity, so it is byte-identical across every
moment of a match and every fan sharing those settings: providers can cache it. Everything specific to a
moment (evidence, names, minute, a reference line) goes in the user message as JSON.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from pitchlens.schema.insights import Insight
from pitchlens.schema.profiles import Persona, Profile, Verbosity

from .provider import LLMRequest, Tier
from .templates import NarrationContext, render, slots

GLOSSARY_DIR = Path(__file__).resolve().parents[1] / "personalize" / "glossary"

PERSONAS: dict[Persona, str] = {
    Persona.CASUAL: "a casual fan watching on a phone. Make it vivid and human, skip jargon and keep at most "
    "one number, the one that matters most.",
    Persona.ANALYST: "a data-hungry analyst. Lead with the numbers and say what they mean, precisely and "
    "without hype.",
    Persona.TACTICIAN: "a tactics enthusiast. Explain the pattern behind the moment (pressing, space, "
    "control, rhythm) using the correct tactical terms.",
    Persona.KIDS: "a ten-year-old fan. Short, simple, friendly words. One fun fact at most. One emoji is "
    "allowed.",
    Persona.FANTASY: "a fantasy-football manager. Focus on what the moment means for the players involved.",
    Persona.ACCESSIBILITY: "a listener using a screen reader or audio description. Say plainly who did what, "
    "for which team, and the score when relevant. No symbols or arrows.",
}
MAX_WORDS = {Verbosity.SHORT: 20, Verbosity.MEDIUM: 35, Verbosity.LONG: 60}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "emphasis_entities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "emphasis_entities"],
    "additionalProperties": False,
}


@cache
def glossary(lang: str) -> tuple[str, dict[str, str]]:
    path = GLOSSARY_DIR / f"{lang}.yaml"
    if not path.exists():
        path = GLOSSARY_DIR / "en.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["language"], data["terms"]


@cache
def system_prompt(persona: Persona, lang: str, verbosity: Verbosity) -> str:
    language, terms = glossary(lang)
    term_list = "; ".join(f"{k.replace('_', ' ')} = {v}" for k, v in terms.items())
    return (
        "You are PitchLens, a football broadcaster writing one on-screen line about a moment in a live "
        "match.\n"
        f"Write in {language}. Use these football terms: {term_list}.\n"
        f"Your audience is {PERSONAS[persona]}\n"
        f"Write at most {MAX_WORDS[verbosity]} words.\n"
        "Ground rules:\n"
        "- Use only facts from EVIDENCE, ENTITIES, MINUTE and SCORE in the user message. Every number you "
        "write must come from there; you may round it or write a probability as a percentage.\n"
        "- Name only the players and teams listed in ENTITIES.\n"
        "- Do not invent history, records, injuries, quotes or reasons that the evidence does not show. "
        "If a detail is missing, leave it out.\n"
        "- REFERENCE is a correct, plain version of the line. Improve its style for the audience; do not "
        "add facts it lacks.\n"
        'Reply with JSON only: {"text": "<your line>", "emphasis_entities": ["<names to highlight>"]}'
    )


def _payload(insight: Insight, ctx: NarrationContext, profile: Profile) -> dict[str, Any]:
    values = slots(insight, ctx, profile.language)
    evidence = []
    for e in insight.evidence:
        item: dict[str, Any] = {"metric": e.metric}
        for key in ("value", "from_", "to", "unit", "window_min"):
            value = getattr(e, key)
            if value is not None:
                item[key.rstrip("_")] = value
        if e.team:
            item["team"] = ctx.team_names.get(e.team, e.team)
        if e.player:
            item["player"] = ctx.player_names.get(e.player, e.player)
        evidence.append(item)
    return {
        "moment": insight.headline_key,
        "why_it_matters": insight.why_tags,
        "MINUTE": values["minute"],
        "SCORE": values["score"],
        "ENTITIES": {
            "players": [ctx.player_names.get(p, p) for p in insight.entities.players],
            "teams": [ctx.team_names.get(t, t) for t in insight.entities.teams],
        },
        "EVIDENCE": evidence,
        "REFERENCE": render(insight, ctx, profile).text,
    }


def build_request(
    insight: Insight,
    ctx: NarrationContext,
    profile: Profile,
    *,
    tier: Tier = Tier.FAST,
    feedback: list[str] | None = None,
) -> LLMRequest:
    payload = _payload(insight, ctx, profile)
    user = json.dumps(payload, ensure_ascii=False, indent=1)
    if feedback:
        user += (
            "\n\nYour previous line broke the ground rules: " + "; ".join(feedback)
            + ". Rewrite it using only the evidence."
        )  # fmt: skip
    return LLMRequest(
        system=system_prompt(profile.persona, profile.language, profile.verbosity),
        user=user,
        tier=tier,
        json_schema=RESPONSE_SCHEMA,
        payload=payload,
    )


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_response(raw: str) -> tuple[str, list[str]]:
    """Extract (text, emphasis_entities) from a model reply; tolerate code fences and stray prose."""
    cleaned = _FENCE.sub("", raw.strip())
    candidates = [cleaned]
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            emphasis = [str(x) for x in data.get("emphasis_entities", []) if isinstance(x, str)]
            return data["text"].strip(), emphasis
    return cleaned, []
