"""Short tracking clips around a match moment, for animations that are not live (the home page's 3D hero)."""

from __future__ import annotations

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
    return {
        "match_id": record.match_id,
        "moment": {
            "type": "goal",
            "match_ms": goal.match_ms,
            "team_id": goal.team_id,
            "player_id": goal.player_id,
            "goals": len(goals),
        },
        "frames": [tracking.frame(i).model_dump(mode="json") for i in range(start, end + 1)],
    }
