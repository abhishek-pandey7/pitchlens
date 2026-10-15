"""Instant template text (§10): renders every insight in six languages in well under a millisecond.

Slots are filled only from the insight's evidence and entity names, formatted per locale. Detail
sentences whose slots are missing are dropped rather than guessed. The same slot table also tells
the grounding validator which numbers a line is allowed to contain.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from pitchlens.clock import minute_label
from pitchlens.interpret.state import MatchState
from pitchlens.schema.events import MatchMeta
from pitchlens.schema.insights import Insight
from pitchlens.schema.profiles import SUPPORTED_LANGUAGES, Persona, Profile, Verbosity

TEMPLATE_DIR = Path(__file__).with_name("templates")
DETAIL_PERSONAS = {Persona.ANALYST, Persona.TACTICIAN}
DECIMAL_COMMA = {"es", "fr", "pt"}
PCT_METRICS = {
    "win_prob", "field_tilt", "possession_5m", "stoppage_share", "what_if_win_prob", "no_red_win_prob",
    "futures_win", "futures_comeback", "futures_home", "futures_draw", "futures_away", "futures_top_score_p",
    "what_if_futures_win", "no_red_futures_win",
}  # fmt: skip
# Decimal places per metric (default: 1). Integers always print without decimals.
PRECISION = {
    "xg": 2, "xg_difference": 2, "momentum": 2,
    "shot_speed_kmh": 0, "previous_fastest_kmh": 0, "pass_distance_m": 0, "shot_distance_m": 0,
    "pass_difficulty": 0, "assist_pass_difficulty": 0, "control_index": 0, "career_goals": 0,
    "career_appearances": 0,
}  # fmt: skip
_FIELD = re.compile(r"\{([a-z0-9_]+)\}")
NARROW_NBSP = chr(0x202F)  # French typography puts a narrow no-break space before "%"


@dataclass(frozen=True)
class NarrationContext:
    """Names and live scoreline needed to put an insight into words."""

    match_id: str
    home_id: str
    away_id: str
    team_names: dict[str, str]
    player_names: dict[str, str]
    score: tuple[int, int] = (0, 0)

    @classmethod
    def from_meta(cls, meta: MatchMeta, score: tuple[int, int] = (0, 0)) -> NarrationContext:
        players = {lp.player_id: lp.name.split()[-1] for lps in meta.lineups.values() for lp in lps}
        teams = {meta.home.club_id: meta.home.short_name, meta.away.club_id: meta.away.short_name}
        return cls(meta.match_id, meta.home.club_id, meta.away.club_id, teams, players, score)

    @classmethod
    def from_state(cls, state: MatchState) -> NarrationContext:
        return cls.from_meta(state.meta, state.score)

    def opponent(self, club_id: str) -> str:
        return self.away_id if club_id == self.home_id else self.home_id


@dataclass(frozen=True)
class TemplateText:
    headline: str
    body: str = ""

    @property
    def text(self) -> str:
        return f"{self.headline} {self.body}".strip()


def format_number(value: float, metric: str, lang: str) -> str:
    if isinstance(value, bool):
        return str(value)
    # Evidence values arrive as floats; whole numbers (players left, streak lengths, km/h records) must
    # print as "9", never "9.0".
    if isinstance(value, int) or float(value).is_integer():
        text = str(round(value))
    else:
        text = f"{value:.{PRECISION.get(metric, 1)}f}"
    return text.replace(".", ",") if lang in DECIMAL_COMMA else text


def format_pct(fraction: float, lang: str) -> str:
    n = round(100 * fraction)
    return f"{n}{NARROW_NBSP}%" if lang == "fr" else f"{n}%"


def slots(insight: Insight, ctx: NarrationContext, lang: str) -> dict[str, str]:
    """Every placeholder value available for this insight, already formatted for `lang`."""
    out: dict[str, str] = {"minute": minute_label(insight.clock_ms, insight.period)}
    players, teams = insight.entities.players, insight.entities.teams
    if players:
        out["player"] = ctx.player_names.get(players[0], players[0])
    if len(players) > 1:
        out["player2"] = ctx.player_names.get(players[1], players[1])
    team_id = teams[0] if teams else None
    if team_id:
        out["team"] = ctx.team_names.get(team_id, team_id)
        out["opponent"] = ctx.team_names.get(ctx.opponent(team_id), ctx.opponent(team_id))
    out["score"] = f"{ctx.score[0]}-{ctx.score[1]}"
    for e in insight.evidence:
        names = [e.metric]
        if e.team and team_id:
            names.append(f"{e.metric}_team" if e.team == team_id else f"{e.metric}_opp")
        for name in names:
            if isinstance(e.value, str):
                out[name] = e.value
            elif e.value is not None:
                out[name] = format_number(e.value, e.metric, lang)
                if e.metric in PCT_METRICS:
                    out[f"{name}_pct"] = format_pct(e.value, lang)
            for side, v in (("from", e.from_), ("to", e.to)):
                if v is None:
                    continue
                out[f"{name}_{side}"] = format_number(v, e.metric, lang)
                if e.metric in PCT_METRICS:
                    out[f"{name}_{side}_pct"] = format_pct(v, lang)
            if e.window_min is not None:
                out[f"{name}_window"] = str(round(e.window_min))
    return out


@cache
def load_templates(lang: str) -> dict[str, dict[str, Any]]:
    path = TEMPLATE_DIR / f"{lang}.yaml"
    if not path.exists():
        path = TEMPLATE_DIR / "en.yaml"
    data: dict[str, dict[str, Any]] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def template_constants(headline_key: str, lang: str) -> set[float]:
    """Numbers written literally in a template (e.g. '/100', 'last 5 minutes'): trusted, not evidence."""
    entry = load_templates(lang).get(headline_key, {})
    texts = [entry.get("headline", ""), *(entry.get("detail") or [])]
    numbers: set[float] = set()
    for text in texts:
        literal = _FIELD.sub(" ", text)
        numbers |= {float(m) for m in re.findall(r"\d+(?:\.\d+)?", literal)}
    return numbers


def _fill(template: str, values: dict[str, str]) -> str | None:
    needed = {f for _, f, _, _ in string.Formatter().parse(template) if f}
    if not needed <= values.keys():
        return None
    return template.format_map(values)


def render(insight: Insight, ctx: NarrationContext, profile: Profile | None = None) -> TemplateText:
    lang = profile.language if profile and profile.language in SUPPORTED_LANGUAGES else "en"
    persona = profile.persona if profile else Persona.CASUAL
    templates = load_templates(lang)
    values = slots(insight, ctx, lang)
    entry = templates.get(insight.headline_key) or templates["_fallback"]
    headline = _fill(entry["headline"], values)
    if headline is None:
        headline = _fill(templates["_fallback"]["headline"], values) or values["minute"]
        return TemplateText(headline)
    detailed = persona in DETAIL_PERSONAS or (profile is not None and profile.verbosity is Verbosity.LONG)
    if not detailed:
        return TemplateText(headline)
    sentences = [s for t in (entry.get("detail") or []) if (s := _fill(t, values))]
    return TemplateText(headline, " ".join(sentences))
