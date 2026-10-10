"""Shared dependencies: the hub and profile store live on `app.state`."""

from __future__ import annotations

from fastapi import HTTPException, Request

from pitchlens.live.hub import Hub, HubError
from pitchlens.live.session import MatchSession
from pitchlens.personalize.profiles import ProfileStore


def get_hub(request: Request) -> Hub:
    hub: Hub = request.app.state.hub
    return hub


def get_profiles(request: Request) -> ProfileStore:
    profiles: ProfileStore = request.app.state.profiles
    return profiles


def session_or_404(hub: Hub, match_id: str) -> MatchSession:
    try:
        return hub.session(match_id)
    except HubError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
