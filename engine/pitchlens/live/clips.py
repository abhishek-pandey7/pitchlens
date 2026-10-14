"""Short tracking clips around a match moment, for animations that are not live (the home page's 3D hero)."""

from __future__ import annotations

import json
from typing import Any

from pitchlens.live.store import MatchRecord

MAX_CLIP_MS = 60_000


class ClipError(LookupError):
    pass


def goal_clip(
    record: MatchRecord, index: int = 0, before_ms: int = 10_000, after_ms: int = 4_000
) -> dict[str, Any]:
    """Players and ball from `before_ms` ahead of a goal to `after_ms` after it. `index` picks the goal
    (0 the first, -1 the last). Needs the record's tracking to be loaded."""
    return json.loads(goal_clip_json(record, index, before_ms, after_ms))


def goal_clip_json(
    record: MatchRecord, index: int = 0, before_ms: int = 10_000, after_ms: int = 4_000
) -> str:
    """`goal_clip` as the compact JSON text the API sends. The frames come pre-written from
    `Tracking.frames_json` (the Rust core when built) and are spliced in, never parsed or re-serialised."""
    if record.tracking is None:
        raise ClipError("no tracking for this match yet")
    goals = [e for e in record.events if e.type == "goal"]
    if not goals:
        raise ClipError("no goals in this match")
    try:
        goal = goals[index]
    except IndexError as exc:
        raise ClipError(f"goal {index} out of range (the match has {len(goals)})") from exc
    before_ms, after_ms = max(0, before_ms), max(0, after_ms)
    if before_ms + after_ms > MAX_CLIP_MS:
        raise ClipError(f"clips are at most {MAX_CLIP_MS // 1000} s long")
    tracking = record.tracking
    start = tracking.index_at(goal.match_ms - before_ms)
    end = tracking.index_at(goal.match_ms + after_ms)
    head = {
        "match_id": record.match_id,
        "moment": {
            "type": "goal",
            "match_ms": goal.match_ms,
            "team_id": goal.team_id,
            "player_id": goal.player_id,
            "goals": len(goals),
        },
    }
    frames = ",".join(tracking.frames_json(start, end + 1))
    # The same text as json.dumps(..., separators=(",", ":"), ensure_ascii=False), FastAPI's rendering.
    return json.dumps(head, separators=(",", ":"), ensure_ascii=False)[:-1] + f',"frames":[{frames}]}}'
