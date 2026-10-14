"""Health, profiles, external ingest and the published JSON Schemas."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from pitchlens import __version__, core
from pitchlens.agents.safety import default_safety
from pitchlens.api.deps import get_hub, get_profiles
from pitchlens.api.models import IngestBatch
from pitchlens.ingest.validator import validate_batch
from pitchlens.live.hub import Hub, HubError
from pitchlens.personalize.profiles import ProfileStore
from pitchlens.schema.export_jsonschema import PUBLIC_MODELS
from pitchlens.schema.profiles import Profile

router = APIRouter()


@router.get("/health", tags=["ops"])
def health(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "version": __version__,
        "bus": settings.bus,
        "llm_provider": settings.llm_provider,
        "core": core.backend(),
    }


@router.get("/health/ready", tags=["ops"])
def ready(request: Request, response: Response) -> dict[str, Any]:
    """Readiness probe: the configured model provider has what it needs (503 if not, so the platform
    keeps traffic away), with the loaded matches, content safety mode and agent budget for operators."""
    settings = request.app.state.settings
    hub: Hub = request.app.state.hub
    env = os.environ
    llm_ok = {
        "mock": True,
        "groq": bool(env.get("GROQ_API_KEY")),
        "openai_compat": bool(env.get("OPENAI_COMPAT_BASE_URL")),
        "azure_openai": bool(env.get("AZURE_OPENAI_ENDPOINT")),
        "foundry_claude": bool(
            env.get("ANTHROPIC_FOUNDRY_RESOURCE") or env.get("ANTHROPIC_FOUNDRY_BASE_URL")
        ),
    }.get(settings.llm_provider, False)
    desk = getattr(request.app.state, "agents", None)
    checks = {
        "matches": len(hub.store.list()),
        "llm": {"provider": settings.llm_provider, "configured": llm_ok},
        "content_safety": "azure" if default_safety().azure else "local",
        "agent_budget": desk.caps.snapshot() if desk is not None else None,
    }
    ok = llm_ok  # an empty store is still ready: matches can be generated or ingested
    response.status_code = 200 if ok else 503
    return {"status": "ready" if ok else "not ready", "checks": checks}


@router.get("/profiles", tags=["profiles"])
def list_profiles(profiles: ProfileStore = Depends(get_profiles)) -> list[Profile]:
    return profiles.list()


async def _screen_name(name: str | None) -> None:
    """Fan-supplied names are shown to others (studio, traces): content safety first."""
    if name:
        verdict = await default_safety().screen_input(name)
        if not verdict.allowed:
            raise HTTPException(422, "Please choose a different display name.")


@router.post("/profiles", status_code=201, tags=["profiles"])
async def create_profile(body: dict[str, Any], profiles: ProfileStore = Depends(get_profiles)) -> Profile:
    body.pop("profile_id", None)
    await _screen_name(body.get("display_name"))
    try:
        return profiles.create(**body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/profiles/{profile_id}", tags=["profiles"])
def get_profile(profile_id: str, profiles: ProfileStore = Depends(get_profiles)) -> Profile:
    profile = profiles.get(profile_id)
    if profile is None:
        raise HTTPException(404, f"unknown profile {profile_id!r}")
    return profile


@router.put("/profiles/{profile_id}", tags=["profiles"])
async def put_profile(
    profile_id: str, body: Profile, profiles: ProfileStore = Depends(get_profiles)
) -> Profile:
    if body.profile_id != profile_id:
        raise HTTPException(422, "profile_id in the body must match the URL")
    await _screen_name(body.display_name)
    return profiles.put(body)


@router.post("/ingest", tags=["ingest"])
async def ingest(body: IngestBatch, hub: Hub = Depends(get_hub)) -> dict[str, Any]:
    result = validate_batch(body.events, match_id=body.match_id)
    try:
        accepted = await hub.ingest(body.match_id, result.accepted)
    except HubError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"accepted": accepted, "rejected": result.rejected}


@router.get("/schema/{name}", tags=["schema"])
def schema(name: str) -> dict[str, Any]:
    model = PUBLIC_MODELS.get(name)
    if model is None:
        raise HTTPException(404, f"unknown schema; choose from {sorted(PUBLIC_MODELS)}")
    return model.model_json_schema(by_alias=True, mode="serialization")
