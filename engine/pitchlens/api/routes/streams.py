"""Server-Sent Events: the live stream of a match (§14).

Event names: `sync` (full picture on connect / after a seek), `reset`, `state` (~2 Hz), `event`
(enriched), `insight`, `alert` (physical thresholds), `frame` (opt-in, ~25 Hz) and `control`.
Every message except frames carries an `id`; reconnect with `Last-Event-ID` to resume without gaps.
Query flags: `frames=true` opts into tracking frames; `events=false` skips raw match events, which fan
views and overlays don't need (about 90% of the traffic).

`/sync` returns the snapshot a new subscriber would get, with the id of the last message it covers, so a
relay (the Rust broadcast edge) can join a channel mid-match without gaps or duplicates.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sse_starlette import EventSourceResponse

from pitchlens.api.deps import get_hub, get_profiles, session_or_404
from pitchlens.live.hub import Hub
from pitchlens.personalize.profiles import ProfileStore

router = APIRouter(tags=["streams"])

PING_SECONDS = 15


@router.get("/matches/{match_id}/sync")
async def sync(  # async: runs on the event loop, so the snapshot is atomic with publishing
    match_id: str,
    profile_id: str | None = None,
    frames: bool = False,
    events: bool = True,
    hub: Hub = Depends(get_hub),
    profiles: ProfileStore = Depends(get_profiles),
) -> Response:
    """`{"last_id": N, "sync": <payload>}`. `sync` is byte for byte the data of the `sync` message that
    `/stream` would send a new subscriber right now, and it covers every message with id <= N on this
    channel. `frames` and `events` are accepted so a relay can pass its stream query through; they don't
    change the snapshot, only which messages follow it."""
    del frames, events
    session = session_or_404(hub, match_id)
    if profile_id is not None and profiles.get(profile_id) is None:
        raise HTTPException(404, f"unknown profile {profile_id!r}")
    last_id, payload = session.snapshot(profile_id)
    return Response(
        f'{{"last_id":{last_id},"sync":{payload}}}',
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/matches/{match_id}/stream")
async def stream(
    match_id: str,
    request: Request,
    profile_id: str | None = None,
    frames: bool = False,
    events: bool = True,
    last_event_id: str | None = Header(default=None),
    hub: Hub = Depends(get_hub),
    profiles: ProfileStore = Depends(get_profiles),
) -> EventSourceResponse:
    session = session_or_404(hub, match_id)
    if profile_id is not None and profiles.get(profile_id) is None:
        raise HTTPException(404, f"unknown profile {profile_id!r}")
    resume = int(last_event_id) if last_event_id and last_event_id.isdigit() else None
    sub = session.subscribe(profile_id=profile_id, frames=frames, events=events, last_event_id=resume)

    async def events() -> AsyncIterator[dict[str, str]]:
        try:
            while True:
                out = await sub.queue.get()
                if out is None:
                    return
                message = {"event": out.event, "data": out.data}
                if out.id is not None:
                    message["id"] = str(out.id)
                yield message
        finally:
            session.unsubscribe(sub)

    return EventSourceResponse(events(), ping=PING_SECONDS, headers={"X-Accel-Buffering": "no"})
