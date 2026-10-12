"""Player-focus mode (§11, T-57): a running card for one player.

Involvement, passing, attacking, defending and running numbers, a touch map, and the player's moments in
the fan's language. The ranker already boosts the focused player's insights 3x
(`ranker.PLAYER_FOCUS_BOOST`); the card is what the fan sees beside the feed.
"""

from __future__ import annotations

from typing import Any

from pitchlens.clock import minute_label
from pitchlens.interpret.state import ZONE_COLS, ZONE_ROWS, MatchState
from pitchlens.narrate.templates import NarrationContext, render
from pitchlens.schema.insights import Insight
from pitchlens.schema.profiles import Profile

TIMELINE_KEEP = 12


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def focus_card(
    state: MatchState, insights: list[Insight], player_id: str, profile: Profile | None
) -> dict[str, Any]:
    """The card for `player_id` (KeyError if the player is not in this match)."""
    p = state.players[player_id]
    team = state.teams[p.team_id]
    shirt = next(
        (lp.shirt for lp in state.meta.lineups.get(p.team_id, []) if lp.player_id == player_id), None
    )
    phys = state.physical.players.get(player_id)
    tracked = state.last_frame is not None
    ctx = NarrationContext.from_state(state)
    moments = [i for i in insights if player_id in i.entities.players]
    timeline = [
        {
            "insight_id": i.insight_id,
            "minute": minute_label(i.clock_ms, i.period),
            "type": i.type.value,
            "text": render(i, ctx, profile).text,
        }
        for i in moments[-TIMELINE_KEEP:]
    ]
    return {
        "player_id": player_id,
        "name": p.name,
        "short_name": p.short_name,
        "shirt": shirt,
        "position": str(p.position),
        "team": {"club_id": team.club_id, "name": team.name},
        "on_pitch": p.on_ms is not None and p.off_ms is None,
        "minutes": round(p.minutes(state.match_ms), 1),
        "involvement": {
            "touches": p.touches,
            "passes": p.passes,
            "pass_accuracy": _r(p.pass_accuracy, 3),
            "mean_pass_difficulty": _r(p.mean_pass_difficulty, 1),
            "hardest_completed_pass": _r(p.hardest_completed_pass, 1),
            "key_passes": p.key_passes,
            "progressive_passes": p.progressive_passes,
            "lines_broken": p.lines_broken,
        },
        "attack": {
            "goals": p.goals,
            "assists": p.assists,
            "shots": p.shots,
            "shots_on_target": p.shots_on_target,
            "xg": _r(p.xg),
            "max_shot_speed_kmh": _r(p.max_shot_speed_kmh, 1) or None,
        },
        "defence": {
            "tackles": p.tackles,
            "interceptions": p.interceptions,
            "recoveries": p.recoveries,
            "pressures": p.pressures,
            "blocks": p.blocks + p.clearances,
        },
        # Physical numbers need tracking frames; without them the card says so instead of showing zeros.
        "physical": (
            {
                "distance_km": _r(phys.distance_m / 1000 if phys else 0.0),
                "top_speed_kmh": _r(phys.top_speed_kmh if phys else 0.0, 1),
                "speed_kmh": _r(phys.speed_kmh if phys else 0.0, 1),
                "sprints": phys.sprints if phys else 0,
            }
            if tracked
            else None
        ),
        # Row-major counts in the player's attacking direction: own goal on the left, opponent's on the right.
        "zones": {"cols": ZONE_COLS, "rows": ZONE_ROWS, "counts": list(p.zones)},
        "timeline": timeline,
        "latest": timeline[-1] if timeline else None,
        "match_minute": minute_label(state.clock_ms, state.period),
    }
