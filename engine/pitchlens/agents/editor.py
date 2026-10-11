"""Editor recaps (§10A, T-126): half-time and full-time recaps written by a group chat of agents.

An Agent Framework **GroupChat** (`GroupChatBuilder`), led by the Editor:

    brief: a fact sheet from the tools (score, goals, key moments, player-of-the-match ranking)
      ─► Editor: plans the storylines and decides whether to ask the Analyst for more
      ─► Analyst: supporting facts, using the analyst tools            (only if the Editor asks)
      ─► Editor: drafts the recap (headline, body, player-of-the-match line)
      ─► Fact-Checker: rules (every number backed by a tool result), then a model judge
           approve ─► done · revise ─► Editor redrafts (up to two rounds) · reject ─► fallback

Every participant hears the whole conversation, as in a newsroom. The speaker order follows the
Editor: its plan says whether the Analyst is needed. The approved recap is written once per match and
stage in English; the **Narrator** then adapts it to each fan's persona, language and club, and its
numbers are checked again against the same facts. If no recap is approved, fans get a plain fact
recap built from the fact sheet, in their language.

Participants are custom group-chat executors wrapping Agent Framework agents, so each turn keeps our
budgets, tool caps, ledger and trace (see `Seat`).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agent_framework import Agent, Executor, Message, WorkflowContext, handler
from agent_framework_orchestrations import (
    GroupChatBuilder,
    GroupChatParticipantMessage,
    GroupChatRequestMessage,
    GroupChatResponseMessage,
    GroupChatState,
)
from pydantic import BaseModel, ValidationError

from pitchlens.clock import minute_label
from pitchlens.narrate.grounding import numbers_in, unsupported_numbers
from pitchlens.narrate.prompts import PERSONAS, glossary
from pitchlens.narrate.provider import Tier
from pitchlens.schema.profiles import Persona, Profile

from .clients import VERIFIED_MARKER, chat_client
from .factcheck import FACT_CHECKER_INSTRUCTIONS, Check, Verdict, fact_check
from .runtime import AgentRun, build_agent, ledger_facts, run_agent, supported_numbers
from .tools import Ledger, MatchTools, MatchView

STAGES = {"ht": "half-time", "ft": "full-time"}
MAX_REVISIONS = 2  # recaps get two rounds (live paths get one)
MAX_ROUNDS = 10  # plan, analyst, draft, check, and up to two revise/check pairs, with headroom
FACTS_MAX_CHARS = 12_000  # the fact sheet and the analyst's findings, for the brief and the judge
WORDS = {"ht": 110, "ft": 170}
NARRATOR_WORDS = {"short": 80, "medium": 120, "long": 170}

EDITOR_INSTRUCTIONS = """You are the PitchLens Editor. You lead a small newsroom writing the {stage}
recap of a football match: the Analyst has match-data tools, the Fact-Checker checks every claim
against the tool results. Never invent facts: every number, name and claim must come from the FACT
SHEET or the Analyst's replies in this conversation. Write about the match, not the data: never
mention that a figure is missing. Say which team and which period every number belongs to (a match
total is not a first-half figure). 0..1 values are shares or
probabilities: write them as percentages. The control index runs from 0 (chaos) to 100 (control) and
is not a percentage. Write player and team names exactly as given."""

PLAN_TURN = """YOUR TURN (plan): pick the two or three storylines that matter most, from the FACT SHEET.
Then ask the Analyst for at most three supporting facts the fact sheet lacks (for example how
pressing, possession, control or win probability changed between periods). End with one line:
"ANALYST: <your requests>", or "ANALYST: none" if the fact sheet is enough. At most 120 words."""

DRAFT_TURN = """YOUR TURN (draft): write the recap from the facts in this conversation.
headline: at most 12 words. body: at most {words} words in two or three short paragraphs, telling the
story of the {stage} in order. player_line: one sentence on the {potm} (the top of the
player_of_the_match ranking), citing the two or three stats that earned it; do not repeat it in the
body. Reply in JSON only."""

REVISE_TURN = """YOUR TURN (revise): the Fact-Checker found problems in your draft. Fix every one,
using only facts from this conversation; drop a claim rather than guess. Same format and limits.
Reply in JSON only."""

RECAP_ANALYST_INSTRUCTIONS = """You are the PitchLens Analyst, supporting the Editor's match recap.
Rules:
- Every number you state must come from a tool result in this conversation. Call tools first; never
  guess or use outside knowledge. If the tools cannot answer, say so.
- Probabilities and shares are given as 0..1; write them as percentages (0.62 -> 62%).
- For pressing intensity use press_rate (higher = more pressing). Momentum is home minus away.
- Call each tool at most once with the same arguments.
- Reply with at most five short bullet points, each naming the metric and the minutes behind it."""

NARRATOR_INSTRUCTIONS = """You are the PitchLens Narrator. Rewrite the VERIFIED RECAP for one fan.
Write in {language}. The fan is {persona}{club}
Rules:
- Use only facts from the VERIFIED RECAP; add no new facts, numbers or names. Keep numbers exactly as
  given (0..1 values may be written as percentages). You may leave out numbers this fan does not need.
- Write player and team names exactly as given, in Latin script, headline included.{side}
- headline: at most 12 words. body: at most {words} words. player_line: one sentence.
Reply in JSON only."""

ADAPTATION_CHECK_INSTRUCTIONS = """You are the PitchLens Fact-Checker. A Narrator adapted a VERIFIED
RECAP for one fan, possibly in another language. Check the ADAPTATION against the VERIFIED RECAP only.
- approve: every claim in the ADAPTATION is stated in the VERIFIED RECAP or follows directly from it:
  the same teams, players, numbers, minutes and who did what. Leaving things out, translating, and a
  fan's-eye tone are fine.
- revise: some claims are new, embellished or wrong (for example the wrong team had the ball, or a
  player is praised for something the recap does not say). List each problem precisely, in English.
- reject: the ADAPTATION mostly does not match the VERIFIED RECAP.
Reply in JSON only."""

# Plain fact recap labels, for when no recap is approved (or no model is configured).
LABELS: dict[str, dict[str, str]] = {
    "en": {"ht": "Half-time", "ft": "Full-time", "goals": "Goals", "no_goals": "No goals",
           "xg": "Expected goals (xG)", "possession": "Possession", "potm": "Player of the match",
           "standout": "Standout player so far"},
    "es": {"ht": "Descanso", "ft": "Final del partido", "goals": "Goles", "no_goals": "Sin goles",
           "xg": "Goles esperados (xG)", "possession": "Posesión", "potm": "Jugador del partido",
           "standout": "Jugador destacado hasta ahora"},
    "fr": {"ht": "Mi-temps", "ft": "Fin du match", "goals": "Buts", "no_goals": "Aucun but",
           "xg": "Buts attendus (xG)", "possession": "Possession", "potm": "Homme du match",
           "standout": "Joueur en vue jusqu'ici"},
    "pt": {"ht": "Intervalo", "ft": "Fim de jogo", "goals": "Gols", "no_goals": "Sem gols",
           "xg": "Gols esperados (xG)", "possession": "Posse de bola", "potm": "Melhor em campo",
           "standout": "Destaque até agora"},
    "ar": {"ht": "نهاية الشوط الأول", "ft": "نهاية المباراة", "goals": "الأهداف", "no_goals": "لا أهداف",
           "xg": "الأهداف المتوقعة (xG)", "possession": "الاستحواذ", "potm": "رجل المباراة",
           "standout": "اللاعب الأبرز حتى الآن"},
    "hi": {"ht": "हाफ़-टाइम", "ft": "फ़ुल-टाइम", "goals": "गोल", "no_goals": "कोई गोल नहीं",
           "xg": "अपेक्षित गोल (xG)", "possession": "पज़ेशन", "potm": "प्लेयर ऑफ़ द मैच",
           "standout": "अब तक के सबसे बेहतरीन खिलाड़ी"},
}  # fmt: skip


class RecapDraft(BaseModel):
    headline: str
    body: str
    player_line: str

    def text(self) -> str:
        parts = (self.headline, self.body, self.player_line)
        return "\n\n".join(part.strip() for part in parts if part.strip())


_JSON = re.compile(r"\{.*\}", re.DOTALL)
_CITATION = re.compile(r"\s*【[^】]*】")


def parse_draft(response: Any) -> RecapDraft | None:
    """Structured output when the provider supports it; else the JSON object in the text."""
    value = getattr(response, "value", None)
    if isinstance(value, RecapDraft):
        draft = value
    else:
        match = _JSON.search(getattr(response, "text", "") or "")
        if not match:
            return None
        try:
            draft = RecapDraft.model_validate(json.loads(match.group(0)))
        except (ValueError, ValidationError):
            return None
    return RecapDraft(**{k: _CITATION.sub("", v).strip() for k, v in draft.model_dump().items()})


def _tokens(response: Any) -> int:
    usage = getattr(response, "usage_details", None) or {}
    return int(usage.get("total_token_count") or 0) if isinstance(usage, dict) else 0


def transcript(messages: list[Message]) -> str:
    """The group chat so far, as the participants read it."""
    return "\n\n".join(f"[{m.author_name or m.role}] {m.text}" for m in messages if m.text)


# ---------------------------------------------------------------- group-chat participants
class Seat(Executor):
    """A group-chat participant: hears every message, and speaks when the orchestrator asks.

    Agent participants could join the chat directly, but a custom seat lets each turn run through our
    own runner: tool-call caps, the shared fact ledger, the free-tier budget and the trace.
    """

    def __init__(self, seat_id: str, speak: Callable[[list[Message]], Awaitable[str]]) -> None:
        super().__init__(seat_id)
        self._speak = speak
        self.heard: list[Message] = []

    @handler
    async def hear(self, message: GroupChatParticipantMessage, ctx: WorkflowContext) -> None:
        self.heard.extend(message.messages)

    @handler
    async def take_turn(
        self, request: GroupChatRequestMessage, ctx: WorkflowContext[GroupChatResponseMessage]
    ) -> None:
        text = await self._speak(self.heard)
        reply = Message("assistant", [text], author_name=self.id)
        self.heard.append(reply)
        await ctx.send_message(GroupChatResponseMessage(reply))


# ---------------------------------------------------------------- the newsroom
@dataclass
class Master:
    """The approved English recap of one stage (or the reason there is none)."""

    stage: str
    minute: str
    draft: RecapDraft | None
    verdict: str
    revisions: int
    ms: float
    tokens: int
    steps: list[dict[str, Any]]
    ledger: Ledger
    question: str
    facts: dict[str, Any]  # the fact-sheet tool results, by tool
    error: str | None = None

    @property
    def approved(self) -> bool:
        return self.draft is not None and self.verdict == "approve"


@dataclass
class _Room:
    """Shared state of one group chat (the orchestrator's selection and termination read it)."""

    phase: str = "plan"  # plan | draft
    wants_analyst: bool = True
    draft: RecapDraft | None = None
    check: Check | None = None
    revisions: int = 0
    done: bool = False
    error: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)


class EditorRoom:
    """One recap: build per stage (agents are cheap), then `write()`."""

    def __init__(
        self,
        view: Callable[[], MatchView],
        stage: str,
        *,
        client: Callable[[str, Tier], Any] | None = None,
    ) -> None:
        if stage not in STAGES:
            raise ValueError(f"unknown recap stage {stage!r}")
        self.stage = stage
        self._make = client or (lambda role, tier: chat_client(tier, role=role))
        self.tools = MatchTools(view)
        state = view().state
        self.minute = minute_label(state.clock_ms, state.period)
        home, away = (state.teams[state.home_id].name, state.teams[state.away_id].name)
        self.question = f"Write the {STAGES[stage]} recap of {home} v {away} ({self.minute})."
        potm = (
            "player of the match"
            if stage == "ft"
            else 'standout player of the first half (not "player of the match": the match is not over)'
        )
        self.editor = Agent(
            self._make("editor", Tier.QUALITY), EDITOR_INSTRUCTIONS.format(stage=STAGES[stage]), name="editor"
        )
        self.analyst = build_agent(
            self._make("analyst", Tier.FAST),
            self.tools,
            name="analyst",
            instructions=RECAP_ANALYST_INSTRUCTIONS,
        )
        self.checker = Agent(self._make("checker", Tier.FAST), FACT_CHECKER_INSTRUCTIONS, name="fact_checker")
        self._draft_turn = DRAFT_TURN.format(words=WORDS[stage], stage=STAGES[stage], potm=potm)

    # ------------------------------------------------------------------ fact sheet
    def fact_sheet(self) -> dict[str, Any]:
        """The deterministic facts every recap starts from, recorded in the ledger like any tool call."""
        return {
            "get_match_state": self.tools.get_match_state(),
            "get_goals": self.tools.get_goals(),
            "player_of_the_match": self.tools.player_of_the_match(),
            "get_insights": self.tools.get_insights(limit=6),
        }

    # ------------------------------------------------------------------ the turns
    async def _editor(self, room: _Room, heard: list[Message]) -> str:
        start = time.perf_counter()
        convo = transcript(heard)
        try:
            if room.phase == "plan":
                response = await self.editor.run(f"{convo}\n\n{PLAN_TURN}")
                text = _CITATION.sub("", response.text or "").strip()
                room.wants_analyst = not re.search(r"ANALYST:\s*none\b", text, re.IGNORECASE)
                room.phase = "draft"
                room.steps.append(self._step("editor", "plan", text, start, _tokens(response)))
                return text or "ANALYST: none"
            task = "draft" if room.check is None else "revise"
            turn = self._draft_turn if task == "draft" else f"{REVISE_TURN}\n{self._draft_turn}"
            response = await self.editor.run(f"{convo}\n\n{turn}", options={"response_format": RecapDraft})
            draft = parse_draft(response)
            if draft is None:
                raise ValueError(f"unreadable draft: {(response.text or '')[:160]!r}")
        except Exception as exc:  # provider down, budget spent, unreadable draft: the room closes
            room.error, room.done = str(exc)[:300], True
            room.steps.append({**self._step("editor", room.phase, "", start, 0), "error": room.error})
            return f"(the Editor could not continue: {room.error})"
        room.draft = draft
        room.steps.append(self._step("editor", task, draft.text(), start, _tokens(response)))
        return draft.text()

    async def _analyst(self, room: _Room, heard: list[Message]) -> str:
        plan = next((m.text for m in reversed(heard) if m.author_name == "editor"), "") or ""
        before = len(self.tools.ledger.calls)
        prompt = (
            f"{self.question} The Editor's plan:\n{plan}\n\n"
            "Answer the Editor's requests for the Analyst using the tools."
        )
        run = await run_agent(self.analyst, self.tools, prompt, keep_ledger=True)
        trace = run.trace()
        trace["tool_calls"] = trace["tool_calls"][before:]  # this turn's calls, not the fact sheet
        room.steps.append(trace)
        return run.text or "The tools had nothing further on those requests."

    async def _fact_checker(self, room: _Room, heard: list[Message]) -> str:
        assert room.draft is not None
        ledger = Ledger(list(self.tools.ledger.calls))
        run = AgentRun("editor", self.question, room.draft.text(), ledger, 0.0)
        run.unsupported = unsupported_numbers(run.text, supported_numbers(ledger, self.question))
        check = await fact_check(self.checker, self.question, run, max_chars=FACTS_MAX_CHARS)
        room.check = check
        room.steps.append({**check.trace(), "round": room.revisions + 1})
        if check.verdict == "revise" and room.revisions < MAX_REVISIONS:
            room.revisions += 1
        else:
            room.done = True
        return json.dumps({"verdict": check.verdict, "problems": check.problems}, ensure_ascii=False)

    @staticmethod
    def _step(agent: str, task: str, text: str, start: float, tokens: int) -> dict[str, Any]:
        return {
            "agent": agent, "task": task, "answer": text,
            "ms": round((time.perf_counter() - start) * 1000), "tokens": tokens,
        }  # fmt: skip

    # ------------------------------------------------------------------ the group chat
    def _workflow(self, room: _Room) -> Any:
        def seat(name: str, turn: Callable[[_Room, list[Message]], Awaitable[str]]) -> Seat:
            return Seat(name, lambda heard: turn(room, heard))

        def next_speaker(chat: GroupChatState) -> str:
            """The Editor leads: after its plan, the Analyst speaks only if the Editor asked for it."""
            last = chat.conversation[-1].author_name if chat.conversation else None
            if last == "editor":
                if room.draft is None:  # it just planned
                    return "analyst" if room.wants_analyst else "editor"
                return "fact_checker"
            return "editor"  # after the brief, the Analyst's findings, or a "revise" verdict

        return GroupChatBuilder(
            name="editor_recap",
            participants=[
                seat("editor", self._editor),
                seat("analyst", self._analyst),
                seat("fact_checker", self._fact_checker),
            ],
            selection_func=next_speaker,
            orchestrator_name="newsroom",
            termination_condition=lambda _conversation: room.done,
            max_rounds=MAX_ROUNDS,
        ).build()

    async def write(self) -> Master:
        start = time.perf_counter()
        facts = self.fact_sheet()
        sheet = {
            "agent": "editor", "task": "fact sheet", "answer": "",
            "tool_calls": [{"tool": c.tool, "args": c.args, "ms": round(c.ms, 1), "error": c.error}
                           for c in self.tools.ledger.calls],
        }  # fmt: skip
        room = _Room(steps=[sheet])
        brief = f"BRIEF: {self.question}\nFACT SHEET: {ledger_facts(self.tools.ledger, FACTS_MAX_CHARS)}"
        try:
            await self._workflow(room).run(brief)
        except Exception as exc:  # the orchestration itself failed; fall back to the fact recap
            room.error = room.error or str(exc)[:300]
        verdict = room.check.verdict if room.check else "none"
        return Master(
            stage=self.stage,
            minute=self.minute,
            draft=room.draft if verdict == "approve" else None,
            verdict=verdict,
            revisions=room.revisions,
            ms=(time.perf_counter() - start) * 1000,
            tokens=sum(int(s.get("tokens") or 0) for s in room.steps),
            steps=room.steps,
            ledger=Ledger(list(self.tools.ledger.calls)),
            question=self.question,
            facts=facts,
            error=room.error,
        )


# ---------------------------------------------------------------- per-fan recap
@dataclass
class RecapResult:
    stage: str
    minute: str
    headline: str
    body: str
    player_line: str
    player_of_match: dict[str, Any] | None
    lang: str
    source: str  # narrator | editor | facts
    verdict: str
    revisions: int
    ms: float
    tokens: int
    steps: list[dict[str, Any]]
    question: str
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "minute": self.minute,
            "headline": self.headline,
            "body": self.body,
            "player_line": self.player_line,
            "player_of_match": self.player_of_match,
            "lang": self.lang,
            "source": self.source,
            "verdict": self.verdict,
            "revisions": self.revisions,
            "ms": round(self.ms),
            "tokens": self.tokens,
            "steps": self.steps,
            "question": self.question,
            "answer": f"{self.headline}\n\n{self.body}\n\n{self.player_line}".strip(),
            "cached": self.cached,
        }


def fact_recap(master: Master, lang: str) -> RecapDraft:
    """A plain recap from the fact sheet alone: always correct, in the fan's language."""
    labels = LABELS.get(lang, LABELS["en"])
    state, goals = master.facts["get_match_state"], master.facts["get_goals"]["goals"]
    home, away = state["home"], state["away"]
    scored = ", ".join(f"{g['scorer'] or g['team']} {g['minute']}" for g in goals) or labels["no_goals"]
    body = (
        f"{labels['goals']}: {scored}.\n"
        f"{labels['xg']}: {home['team']} {home['xg']}, {away['team']} {away['xg']}.\n"
        f"{labels['possession']}: {home['team']} {round(home['possession'] * 100)}%, "
        f"{away['team']} {round(away['possession'] * 100)}%."
    )
    ranking = master.facts["player_of_the_match"]["ranking"]
    player = ""
    if ranking:
        label = labels["potm"] if master.stage == "ft" else labels["standout"]
        player = f"{label}: {ranking[0]['player']} ({ranking[0]['team']})."
    return RecapDraft(headline=f"{labels[master.stage]}: {state['score']}", body=body, player_line=player)


def narrator_instructions(profile: Profile, club_names: dict[str, str]) -> str:
    language, _ = glossary(profile.language)
    clubs = [club_names[c] for c in profile.favorite_clubs if c in club_names]
    club = f" and supports {clubs[0]}." if clubs else "."
    side = f"\n- Tell the story from {clubs[0]}'s side, without changing any fact." if clubs else ""
    return NARRATOR_INSTRUCTIONS.format(
        language=language, persona=PERSONAS[profile.persona], club=club, side=side,
        words=NARRATOR_WORDS[profile.verbosity.value],
    )  # fmt: skip


def needs_narrator(profile: Profile) -> bool:
    """The English analyst with no club sees the Editor's recap as written."""
    plain = profile.language == "en" and profile.persona is Persona.ANALYST
    return not (plain and not profile.favorite_clubs)


async def adapt(
    master: Master,
    profile: Profile,
    club_names: dict[str, str],
    *,
    client: Callable[[str, Tier], Any] | None = None,
) -> RecapResult:
    """The recap for one fan: the Narrator's version if its numbers check out, else the Editor's (in
    English), else the fact recap in the fan's language."""
    start = time.perf_counter()
    ranking = master.facts["player_of_the_match"]["ranking"]

    def result(draft: RecapDraft, lang: str, source: str, steps: list[dict[str, Any]]) -> RecapResult:
        return RecapResult(
            stage=master.stage, minute=master.minute, headline=draft.headline, body=draft.body,
            player_line=draft.player_line, player_of_match=ranking[0] if ranking else None, lang=lang,
            source=source, verdict=master.verdict, revisions=master.revisions,
            ms=master.ms + (time.perf_counter() - start) * 1000,
            tokens=master.tokens + sum(int(s.get("tokens") or 0) for s in steps),
            steps=[*master.steps, *steps], question=f"{STAGES[master.stage].capitalize()} recap",
        )  # fmt: skip

    if not master.approved:
        return result(fact_recap(master, profile.language), profile.language, "facts", [])
    assert master.draft is not None
    if not needs_narrator(profile):
        return result(master.draft, "en", "editor", [])

    make = client or (lambda role, tier: chat_client(tier, role=role))
    narrator = Agent(make("narrator", Tier.FAST), narrator_instructions(profile, club_names), name="narrator")
    checker = Agent(make("checker", Tier.FAST), ADAPTATION_CHECK_INSTRUCTIONS, name="fact_checker")
    verified = master.draft.model_dump_json()
    allowed = supported_numbers(master.ledger, master.question) | set(numbers_in(master.draft.text()))
    steps: list[dict[str, Any]] = []
    prompt = f"{VERIFIED_MARKER}\n{verified}"
    for attempt in range(1 + MAX_ADAPT_REVISIONS):
        draft, step = await _narrate(narrator, prompt, master.draft, allowed, profile.language, attempt + 1)
        steps.append(step)
        if draft is None or step["unchanged"]:
            break
        if not step["grounded"]:
            numbers = ", ".join(step["unsupported_numbers"])
            problems = [f"these numbers are not in the verified recap: {numbers}"]
        else:
            check = await judge_adaptation(checker, master.draft.text(), draft.text())
            steps.append({**check.trace(), "round": attempt + 1})
            if check.approved:
                return result(draft, profile.language, "narrator", steps)
            if check.verdict != "revise":
                break
            problems = check.problems
        prompt = (
            f"{VERIFIED_MARKER}\n{verified}\n\nYour previous version was: {draft.model_dump_json()}\n"
            f"The fact-checker found these problems: {'; '.join(problems)}. Rewrite it, fixing them."
        )
    return result(master.draft, "en", "editor", steps)


MAX_ADAPT_REVISIONS = 1


async def _narrate(
    narrator: Agent, prompt: str, verified: RecapDraft, allowed: set[float], lang: str, round_: int
) -> tuple[RecapDraft | None, dict[str, Any]]:
    """One Narrator attempt, with its numbers checked against the verified recap's facts."""
    start = time.perf_counter()
    try:
        response = await narrator.run(prompt, options={"response_format": RecapDraft})
        draft = parse_draft(response)
        tokens, error = _tokens(response), None if draft else "unreadable reply"
    except Exception as exc:
        draft, tokens, error = None, 0, str(exc)[:200]
    bad = unsupported_numbers(draft.text(), allowed) if draft is not None else []
    unchanged = draft is not None and not bad and draft == verified
    return draft, {
        "agent": "narrator", "lang": lang, "round": round_, "grounded": draft is not None and not bad,
        "unchanged": unchanged, "unsupported_numbers": bad, "error": error,
        "answer": draft.text() if draft else "", "ms": round((time.perf_counter() - start) * 1000),
        "tokens": tokens,
    }  # fmt: skip


async def judge_adaptation(checker: Agent, verified: str, adapted: str) -> Check:
    """The Fact-Checker reads the adaptation beside the verified recap (claims, not just numbers)."""
    start = time.perf_counter()
    try:
        response = await checker.run(
            f"VERIFIED RECAP:\n{verified}\n\nADAPTATION:\n{adapted}", options={"response_format": Verdict}
        )
        verdict = response.value if isinstance(response.value, Verdict) else None
        verdict = verdict or Verdict.model_validate_json(response.text or "")
    except Exception as exc:  # numbers already passed; a judge outage should not block the fan
        return Check(
            "approve", [f"judge unavailable: {str(exc)[:120]}"], by="rules (judge unavailable)",
            ms=(time.perf_counter() - start) * 1000,
        )  # fmt: skip
    return Check(
        verdict.verdict, verdict.problems, "model", (time.perf_counter() - start) * 1000, _tokens(response)
    )
