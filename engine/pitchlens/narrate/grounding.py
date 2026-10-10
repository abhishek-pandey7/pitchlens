"""Grounding validator (§10): a narration may only contain numbers and players the evidence supports.

Allowed numbers: every evidence value, from/to, window and threshold (and probabilities written as
percentages), differences between from/to pairs, the scoreline, the minute label, and numbers written
literally into the moment's template ("/100", "last 5 minutes"). Each number found in the text must
match one of these within rounding tolerance. Players: anyone in the lineups who is named in the text
must be one of the insight's entities (both teams may always be named).
"""

from __future__ import annotations

import re

from pitchlens.schema.insights import Insight
from pitchlens.schema.profiles import Grounding

from .templates import NarrationContext, slots, template_constants

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹०१२३४५६७८९", "0123456789" * 3)
# A number: digits with an optional decimal part ("." or ","). Letters may follow it, so ordinals
# ("12th", "2e", "12.º", "12वां") are caught too; only a preceding digit or separator is excluded.
_NUMBER = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)")


def normalise_digits(text: str) -> str:
    return text.translate(_DIGITS)


def numbers_with_precision(text: str) -> list[tuple[float, int]]:
    """(value, decimal places as written) for every number in the text."""
    found = []
    for raw in _NUMBER.findall(normalise_digits(text)):
        value = raw.replace(",", ".")
        decimals = len(value.split(".", 1)[1]) if "." in value else 0
        found.append((float(value), decimals))
    return found


def numbers_in(text: str) -> list[float]:
    return [value for value, _ in numbers_with_precision(text)]


def allowed_numbers(insight: Insight, ctx: NarrationContext, lang: str) -> set[float]:
    allowed: set[float] = set()

    def add(v: float) -> None:
        allowed.add(round(v, 4))
        if 0 <= v <= 1:
            allowed.add(round(100 * v, 4))

    for e in insight.evidence:
        for v in (e.value, e.from_, e.to, e.window_min):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                add(float(v))
        if isinstance(e.value, str):
            allowed |= set(numbers_in(e.value))
        if e.from_ is not None and e.to is not None:
            add(abs(e.to - e.from_))
    allowed |= set(numbers_in(f"{ctx.score[0]}-{ctx.score[1]}"))
    allowed |= set(numbers_in(slots(insight, ctx, lang)["minute"]))
    allowed |= template_constants(insight.headline_key, lang) | template_constants(insight.headline_key, "en")
    return allowed


def _matches(n: float, decimals: int, allowed: set[float]) -> bool:
    """A written number matches if it is an allowed value rounded to the precision it is written with."""
    tolerance = 0.5 * 10**-decimals + 1e-6
    return any(abs(n - a) <= tolerance for a in allowed)


def validate(text: str, insight: Insight, ctx: NarrationContext, lang: str = "en") -> Grounding:
    violations: list[str] = []
    allowed = allowed_numbers(insight, ctx, lang)
    numbers = numbers_with_precision(text)
    for n, decimals in numbers:
        if not _matches(n, decimals, allowed):
            violations.append(f"number {n:g} is not in the evidence")
    # Surnames are unique within a club but can repeat across the two squads, so a name is fine if any
    # player carrying it is part of the moment.
    entities = set(insight.entities.players)
    by_name: dict[str, set[str]] = {}
    for player_id, name in ctx.player_names.items():
        by_name.setdefault(name, set()).add(player_id)
    for name, ids in by_name.items():
        if not ids & entities and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text):
            violations.append(f"player {name} is not part of this moment")
    return Grounding(passed=not violations, numbers_checked=len(numbers), violations=violations)
