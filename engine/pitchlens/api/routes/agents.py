"""Agent endpoints: Ask the Match, Editor recaps and the agent trace log."""

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

router = APIRouter(prefix="/matches/{match_id}", tags=["agents"])


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    profile_id: str = "p-analyst-en"


class RecapBody(BaseModel):
    stage: Literal["ht", "ft"] = "ft"
    profile_id: str = "p-analyst-en"


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


@router.get("/agents/trace")
def agent_trace(match_id: str, desk: AgentDesk = Depends(get_desk)) -> dict[str, Any]:
    """Recent agent runs for this match, newest first: every step, tool call and verdict."""
    return {"match_id": match_id, "runs": desk.traces(match_id)}
