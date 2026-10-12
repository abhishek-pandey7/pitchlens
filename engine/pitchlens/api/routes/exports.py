"""Exports: WebVTT chapters and captions, and the full match's overlay cues (T-93)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from pitchlens.agents.desk import AgentDesk
from pitchlens.agents.tools import ToolError
from pitchlens.api.deps import get_hub, get_profiles
from pitchlens.live.hub import Hub
from pitchlens.personalize.profiles import ProfileStore
from pitchlens.render import exports

router = APIRouter(prefix="/matches/{match_id}/export", tags=["exports"])
VTT = "text/vtt; charset=utf-8"


def get_desk(request: Request) -> AgentDesk:
    desk: AgentDesk = request.app.state.agents
    return desk


@router.get("/{kind}")
async def export(
    match_id: str,
    kind: Literal["chapters", "captions", "cues"],
    lang: str | None = None,
    profile_id: str = "p-analyst-en",
    hub: Hub = Depends(get_hub),
    desk: AgentDesk = Depends(get_desk),
    profiles: ProfileStore = Depends(get_profiles),
) -> Any:
    """`chapters` (WebVTT, titles in `lang`), `captions` (WebVTT, the narration `profile_id` would see,
    optionally in another `lang`) or `cues` (JSON, every overlay cue that fan's feed puts on air).

    A live match exports what has happened so far; a stored match exports the whole match. Timestamps are
    match time."""
    try:
        view = (await desk.views.view(match_id))()
    except ToolError as exc:
        raise HTTPException(404, str(exc)) from exc
    state, insights = view.state, view.insights
    if kind == "chapters":
        events = [e for e in hub.record(match_id).events if e.match_ms <= state.match_ms]
        body = exports.chapters(state, insights, events, lang or "en")
        return Response(body, media_type=VTT, headers=_download(match_id, f"chapters-{lang or 'en'}.vtt"))
    profile = profiles.get(profile_id)
    if profile is None:
        raise HTTPException(404, f"unknown profile {profile_id!r}")
    if lang:
        profile = profile.model_copy(update={"language": lang})
    if kind == "captions":
        body = exports.captions(state, insights, profile)
        return Response(body, media_type=VTT, headers=_download(match_id, f"captions-{profile.language}.vtt"))
    return exports.cues(state, insights, profile)


def _download(match_id: str, name: str) -> dict[str, str]:
    return {"Content-Disposition": f'inline; filename="{match_id}-{name}"'}
