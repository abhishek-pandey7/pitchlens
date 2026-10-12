"""Exports for streaming players and broadcast partners (§23 P1, T-93).

- **Chapters** (WebVTT): kick-off, goals, red cards, half-time, the second half, the biggest other moments
  and full time, with titles in the requested language. A player shows them as a chapter menu.
- **Captions** (WebVTT): the moments one fan's feed would show, as narration lines in their language.
- **Cues** (JSON): every overlay cue a fan's feed would put on air, scheduled under the same rules as live
  (regions, priority, rate limit), so a renderer can play a whole match's graphics back.

Timestamps are match time (`match_ms`): a video that starts at kick-off and runs on the match clock lines
up with them. All text is template text: deterministic, grounded, in six languages.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any

from pitchlens.interpret.state import MatchState
from pitchlens.narrate.templates import NarrationContext, render
from pitchlens.personalize.ranker import rank
from pitchlens.render.cues import CueScheduler, build_cue
from pitchlens.schema.events import EventType, MatchEvent
from pitchlens.schema.insights import Insight, InsightType
from pitchlens.schema.profiles import Profile

CAPTION_MS = 6_000  # how long a caption stays up (or until the next one)
CHAPTER_GAP_MS = 4 * 60_000  # "big moment" chapters keep at least this far from any other chapter
BIG_MOMENT = 0.7  # salience for a moment to become a chapter of its own
MAX_BIG_MOMENTS = 6

# Chapter titles that are not insights: kick-off, half-time, second half, full time.
MARKER_KEYS = ("kickoff", "half_time", "second_half", "full_time")
MARKERS = {
    lang: dict(zip(MARKER_KEYS, titles, strict=True))
    for lang, titles in {
        "en": ("Kick-off", "Half-time", "Second half", "Full time"),
        "es": ("Saque inicial", "Descanso", "Segunda parte", "Final del partido"),
        "fr": ("Coup d'envoi", "Mi-temps", "Seconde période", "Fin du match"),
        "pt": ("Pontapé inicial", "Intervalo", "Segundo tempo", "Fim de jogo"),
        "ar": ("صافرة البداية", "نهاية الشوط الأول", "الشوط الثاني", "نهاية المباراة"),
        "hi": ("किक-ऑफ़", "हाफ़-टाइम", "दूसरा हाफ़", "फ़ुल-टाइम"),
    }.items()
}


def timestamp(ms: int) -> str:
    """WebVTT time: HH:MM:SS.mmm."""
    ms = max(0, int(ms))
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _vtt(blocks: list[tuple[int, int, str]], header: str) -> str:
    lines = ["WEBVTT", f"NOTE {header}", ""]
    for n, (start, end, text) in enumerate(blocks, 1):
        cue_time = f"{timestamp(start)} --> {timestamp(max(end, start + 1000))}"
        lines += [str(n), cue_time, text.replace("-->", "→"), ""]
    return "\n".join(lines)


class _Narrator:
    """Template text for an insight with the scoreline as it stood at that moment."""

    def __init__(self, state: MatchState, profile: Profile | None) -> None:
        self.state, self.profile = state, profile
        self._goal_ms = [g.match_ms for g in state.goals]

    def __call__(self, insight: Insight) -> Any:
        n = bisect.bisect_right(self._goal_ms, insight.match_ms)
        goals = self.state.goals[:n]
        score = (goals[-1].home_score, goals[-1].away_score) if goals else (0, 0)
        return render(insight, NarrationContext.from_meta(self.state.meta, score), self.profile)


@dataclass(frozen=True)
class _Point:
    ms: int
    title: str
    fixed: bool  # whistles, goals and red cards always make a chapter


def chapters(state: MatchState, insights: list[Insight], events: list[MatchEvent], lang: str) -> str:
    labels = MARKERS.get(lang, MARKERS["en"])
    profile = Profile(profile_id=f"export-{lang}", language=lang) if lang in MARKERS else None
    say = _Narrator(state, profile)
    points = [_Point(0, labels["kickoff"], True)]
    for e in events:
        if e.type is EventType.PERIOD_END and e.period == 1:
            points.append(_Point(e.match_ms, labels["half_time"], True))
        elif e.type is EventType.PERIOD_START and e.period == 2:
            points.append(_Point(e.match_ms, labels["second_half"], True))
        elif e.type is EventType.MATCH_END:
            points.append(_Point(e.match_ms, labels["full_time"], True))
    for i in insights:
        dismissal = i.type is InsightType.CARD and i.headline_key != "card.yellow"
        if i.type is InsightType.GOAL or dismissal:
            points.append(_Point(i.match_ms, say(i).headline, True))
    covered = (InsightType.GOAL, InsightType.CARD)
    big = sorted(
        (i for i in insights if i.salience >= BIG_MOMENT and i.type not in covered), key=lambda i: -i.salience
    )
    for i in big:
        if sum(1 for p in points if not p.fixed) >= MAX_BIG_MOMENTS:
            break
        if all(abs(i.match_ms - p.ms) >= CHAPTER_GAP_MS for p in points):
            points.append(_Point(i.match_ms, say(i).headline, False))
    points.sort(key=lambda p: p.ms)
    # Points at the same instant share a chapter: half-time and the second half's kick-off are one moment
    # in match time (the break is not on the match clock).
    merged: list[_Point] = []
    for p in points:
        if merged and p.ms - merged[-1].ms < 1000:
            merged[-1] = _Point(merged[-1].ms, f"{merged[-1].title} · {p.title}", True)
        else:
            merged.append(p)
    points = merged
    end = max(state.match_ms, points[-1].ms + 60_000)
    ends = [p.ms for p in points[1:]] + [end]
    blocks = [(p.ms, stop, p.title) for p, stop in zip(points, ends, strict=True)]
    return _vtt(blocks, f"PitchLens chapters, {state.meta.home.name} v {state.meta.away.name}, match time")


def captions(state: MatchState, insights: list[Insight], profile: Profile) -> str:
    say = _Narrator(state, profile)
    shown = [i for i in sorted(insights, key=lambda i: i.match_ms) if rank(i, profile).feed]
    groups: list[list[Insight]] = []  # moments within a second of each other share one caption
    for i in shown:
        if groups and i.match_ms - groups[-1][0].match_ms < 1000:
            groups[-1].append(i)
        else:
            groups.append([i])
    blocks = []
    for n, group in enumerate(groups):
        start = group[0].match_ms
        nxt = groups[n + 1][0].match_ms if n + 1 < len(groups) else start + CAPTION_MS
        blocks.append((start, min(start + CAPTION_MS, nxt), "\n".join(say(i).text for i in group)))
    return _vtt(blocks, f"PitchLens captions, {profile.language} ({profile.persona.value}), match time")


def cues(state: MatchState, insights: list[Insight], profile: Profile) -> dict[str, Any]:
    say = _Narrator(state, profile)
    scheduler = CueScheduler(profile.max_overlays_per_min)
    aired = []
    for i in sorted(insights, key=lambda i: i.match_ms):
        decision = rank(i, profile)
        if not (decision.feed and decision.overlay):
            continue
        text = say(i)
        cue = build_cue(i, profile, headline=text.headline, body=text.body)
        if scheduler.admit(cue):
            aired.append(cue.model_dump(mode="json", by_alias=True))
    return {
        "match_id": state.match_id,
        "profile_id": profile.profile_id,
        "language": profile.language,
        "time_base": "match_ms",
        "cues": aired,
    }
