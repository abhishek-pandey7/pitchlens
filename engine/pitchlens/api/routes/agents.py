"""Agent endpoints: Ask the Match, Editor recaps, the Director's programme and the agent trace log."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from pitchlens.agents.ask import MAX_QUESTION_CHARS
from pitchlens.agents.desk import AgentDesk, DeskBusy
from pitchlens.agents.tools import ToolError
from pitchlens.agents.views import NotYet
from pitchlens.api.deps import get_profiles
from pitchlens.personalize.profiles import ProfileStore
from pitchlens.render.programme import Mode

router = APIRouter(prefix="/matches/{match_id}", tags=["agents"])


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    profile_id: str = "p-analyst-en"


class RecapBody(BaseModel):
    stage: Literal["ht", "ft"] = "ft"
    profile_id: str = "p-analyst-en"


class DirectorBody(BaseModel):
    mode: Literal["off", "auto", "approve"]
    profile_id: str = "p-analyst-en"


class CueDecision(BaseModel):
    action: Literal["air", "hold", "drop", "approve"]


def get_desk(request: Request) -> AgentDesk:
    desk: AgentDesk = request.app.state.agents
    return desk


@router.post("/ask")
async def ask(
    match_id: str,
    body: AskBody,
    desk: AgentDesk = Depends(get_desk),
    profiles: ProfileStore = Depends(get_profiles),
) -> dict[str, Any]:
    """A fan's question, answered by the Analyst, checked by the Fact-Checker and personalised by the
    Companion. Every number in the answer comes from a tool result."""
    profile = profiles.get(body.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"unknown profile {body.profile_id!r}")
    try:
        result = await desk.ask(match_id, body.question, profile)
    except ToolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DeskBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result.to_dict()


@router.post("/recap")
async def recap(
    match_id: str,
    body: RecapBody,
    desk: AgentDesk = Depends(get_desk),
    profiles: ProfileStore = Depends(get_profiles),
) -> dict[str, Any]:
    """The half-time (`ht`) or full-time (`ft`) recap for one fan, with the player of the match. Written
    by the Editor's group chat (Editor, Analyst, Fact-Checker), adapted by the Narrator to the fan's
    persona, language and club. 409 before the whistle."""
    profile = profiles.get(body.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"unknown profile {body.profile_id!r}")
    try:
        result = await desk.recap(match_id, body.stage, profile)
    except ToolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotYet as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DeskBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result.to_dict()


@router.get("/director")
def programme(match_id: str, desk: AgentDesk = Depends(get_desk)) -> dict[str, Any]:
    """The directed programme: mode, cues waiting for a decision (with the Director's proposals in
    approve mode), what is on screen, and the decision log."""
    try:
        return desk.programme(match_id)
    except ToolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/director")
async def direct(match_id: str, body: DirectorBody, desk: AgentDesk = Depends(get_desk)) -> dict[str, Any]:
    """Make a fan channel the programme and set the Director's mode: off, auto (the Director decides)
    or approve (the Director proposes, the producer decides). Goals and red cards never wait."""
    try:
        return desk.direct(match_id, body.profile_id, Mode(body.mode))
    except ToolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown profile {body.profile_id!r}") from exc


@router.post("/director/cues/{cue_id}")
async def produce(
    match_id: str, cue_id: str, body: CueDecision, desk: AgentDesk = Depends(get_desk)
) -> dict[str, Any]:
    """The producer's decision on a pending cue: air, hold, drop, or approve the Director's proposal.
    Airing still follows the scheduler's rules (a busy region or the rate limit can refuse it)."""
    try:
        return desk.produce(match_id, cue_id, body.action)
    except ToolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/agents/trace")
def agent_trace(match_id: str, desk: AgentDesk = Depends(get_desk)) -> dict[str, Any]:
    """Recent agent runs for this match, newest first: every step, tool call and verdict."""
    return {"match_id": match_id, "runs": desk.traces(match_id)}
