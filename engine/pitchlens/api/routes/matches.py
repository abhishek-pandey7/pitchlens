"""Matches: list, generate, replay control, state, insights, series, players, latency, external feeds."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from pitchlens.api.deps import get_hub, get_profiles, session_or_404
from pitchlens.api.models import ControlRequest, CreateMatch, ExternalMatch, ReplayRequest
from pitchlens.live.clips import ClipError, goal_clip_json
from pitchlens.live.hub import Hub, HubError
from pitchlens.personalize.player_focus import focus_card
from pitchlens.personalize.profiles import ProfileStore
from pitchlens.sim.storylines import STORYLINES

router = APIRouter(prefix="/matches", tags=["matches"])

# Endpoints that touch live sessions or replays are `async` so they run on the event loop: sessions
# start asyncio tasks and replays use asyncio events, neither of which is thread-safe. Only CPU-heavy
# match generation runs in FastAPI's worker threads.


@router.get("")
def list_matches(hub: Hub = Depends(get_hub)) -> list[dict[str, Any]]:
    return [{**r.summary(), "replay": hub.replay_state(r.match_id)} for r in hub.store.list()]


@router.get("/storylines")
def storylines() -> list[str]:
    return sorted(STORYLINES)


@router.post("", status_code=201)
def create_match(body: CreateMatch, hub: Hub = Depends(get_hub)) -> dict[str, Any]:
    if body.storyline and body.storyline not in STORYLINES:
        raise HTTPException(422, f"unknown storyline; choose from {sorted(STORYLINES)}")
    clubs = {c.club_id for c in hub.store.league.clubs}
    for club in (body.home, body.away):
        if club and club not in clubs:
            raise HTTPException(422, f"unknown club {club!r}")
    record = hub.store.generate(
        seed=body.seed, storyline=body.storyline, home=body.home, away=body.away, tracking=body.tracking
    )
    return record.summary()


@router.post("/external", status_code=201)
async def register_external(body: ExternalMatch, hub: Hub = Depends(get_hub)) -> dict[str, Any]:
    return hub.register_external(body.meta).summary()


@router.get("/{match_id}")
def get_match(match_id: str, hub: Hub = Depends(get_hub)) -> dict[str, Any]:
    try:
        record = hub.record(match_id)
    except HubError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        **record.summary(),
        "meta": record.meta.model_dump(mode="json"),
        "replay": hub.replay_state(match_id),
    }


@router.get("/{match_id}/clips/goal")
async def goal_clip(
    match_id: str, index: int = 0, before_ms: int = 10_000, after_ms: int = 4_000, hub: Hub = Depends(get_hub)
) -> Response:
    """Tracking frames around a goal (`index` 0 the first, -1 the last), for non-live animations. The body
    is written once (frames by the Rust core when built), the same bytes FastAPI would render."""
    try:
        record = hub.record(match_id)
    except HubError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not await asyncio.to_thread(hub.store.load_cached_tracking, record):
        hub.store.request_tracking(record)  # a stored sample: ready on a later request
    try:
        body = await asyncio.to_thread(goal_clip_json, record, index, before_ms, after_ms)
    except ClipError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(body, media_type="application/json")


@router.post("/{match_id}/replay", status_code=202)
async def replay(match_id: str, body: ReplayRequest, hub: Hub = Depends(get_hub)) -> dict[str, Any]:
    try:
        await hub.start_replay(match_id, speed=body.speed, from_ms=body.from_ms, frames=body.frames)
    except HubError as exc:
        raise HTTPException(404, str(exc)) from exc
    return hub.replay_state(match_id)


@router.post("/{match_id}/control")
async def control(match_id: str, body: ControlRequest, hub: Hub = Depends(get_hub)) -> dict[str, Any]:
    try:
        hub.control(match_id, body.action, to_ms=body.to_ms, speed=body.speed)
    except HubError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return hub.replay_state(match_id)


@router.get("/{match_id}/state")
async def state(match_id: str, hub: Hub = Depends(get_hub)) -> dict[str, Any]:
    return session_or_404(hub, match_id).state.snapshot()


@router.get("/{match_id}/insights")
async def insights(
    match_id: str, since_ms: int = 0, min_salience: float = 0.0, hub: Hub = Depends(get_hub)
) -> list[dict[str, Any]]:
    session = session_or_404(hub, match_id)
    return [
        i.model_dump(mode="json", by_alias=True)
        for i in session.engine.insights
        if i.match_ms >= since_ms and i.salience >= min_salience
    ]


@router.get("/{match_id}/series")
async def series(match_id: str, since_ms: int = 0, hub: Hub = Depends(get_hub)) -> list[dict[str, Any]]:
    return [r for r in session_or_404(hub, match_id).state.series if r["match_ms"] >= since_ms]


@router.get("/{match_id}/players/{player_id}")
async def player(match_id: str, player_id: str, hub: Hub = Depends(get_hub)) -> dict[str, Any]:
    state = session_or_404(hub, match_id).state
    stats = state.players.get(player_id)
    if stats is None:
        raise HTTPException(404, f"unknown player {player_id!r}")
    phys = state.physical.players.get(player_id)
    physical = {k: v for k, v in dataclasses.asdict(phys).items() if not k.startswith("_")} if phys else None
    return {
        **dataclasses.asdict(stats),
        "pass_accuracy": stats.pass_accuracy,
        "mean_pass_difficulty": stats.mean_pass_difficulty,
        "minutes": round(stats.minutes(state.match_ms), 1),
        "physical": physical,
    }


@router.get("/{match_id}/players/{player_id}/focus")
async def player_focus(
    match_id: str,
    player_id: str,
    profile_id: str | None = None,
    hub: Hub = Depends(get_hub),
    profiles: ProfileStore = Depends(get_profiles),
) -> dict[str, Any]:
    """Player-focus card (T-57): the player's numbers so far, a touch map in their attacking direction,
    and their moments in the fan's language (`profile_id`)."""
    session = session_or_404(hub, match_id)
    try:
        return focus_card(session.state, session.engine.insights, player_id, profiles.get(profile_id or ""))
    except KeyError:
        raise HTTPException(404, f"unknown player {player_id!r}") from None


@router.get("/{match_id}/latency")
async def latency(match_id: str, hub: Hub = Depends(get_hub)) -> dict[str, Any]:
    session = session_or_404(hub, match_id)
    return {"stages": session.latency.summary(), "subscribers": len(session.subscribers)}
