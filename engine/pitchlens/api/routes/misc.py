"""Health, profiles, external ingest and the published JSON Schemas."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from pitchlens import __version__
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
    }


@router.get("/profiles", tags=["profiles"])
def list_profiles(profiles: ProfileStore = Depends(get_profiles)) -> list[Profile]:
    return profiles.list()


@router.post("/profiles", status_code=201, tags=["profiles"])
def create_profile(body: dict[str, Any], profiles: ProfileStore = Depends(get_profiles)) -> Profile:
    body.pop("profile_id", None)
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
def put_profile(profile_id: str, body: Profile, profiles: ProfileStore = Depends(get_profiles)) -> Profile:
    if body.profile_id != profile_id:
        raise HTTPException(422, "profile_id in the body must match the URL")
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
