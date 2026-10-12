"""Counterfactual win probability (§23 P1, T-92): "what if" for the moments that nearly changed a match.

Pure maths on the same win-probability model as the live numbers, re-run with one thing changed:

- **a big chance** (missed, saved or blocked): the team's win probability now, and if it had gone in
- **a red card**: the dismissed team's win probability now, and if they still had eleven players

The result is evidence on the insight (`from` = what happened, `to` = the what-if), so it is grounded
like any other number, narrated in each fan's language and shown on the overlay's "What if" strip.
"""

from __future__ import annotations

from pitchlens.interpret.state import MatchState
from pitchlens.schema.insights import Evidence

SCORED = "what_if_win_prob"  # if the chance had gone in
ELEVEN = "no_red_win_prob"  # if the dismissed team still had eleven players


def _evidence(metric: str, team_id: str, actual: float, what_if: float) -> Evidence:
    return Evidence.model_validate(
        {"metric": metric, "team": team_id, "from": round(actual, 3), "to": round(what_if, 3)}
    )


def if_scored(state: MatchState, team_id: str) -> Evidence:
    """The team's win probability now, and with one more goal (the chance converted)."""
    home = state.is_home(team_id)
    h, a = state.score
    actual = state.compute_win_probability().for_team(home)
    what_if = state.compute_win_probability(score=(h + 1, a) if home else (h, a + 1)).for_team(home)
    return _evidence(SCORED, team_id, actual, what_if)


def if_eleven(state: MatchState, team_id: str) -> Evidence:
    """The dismissed team's win probability now, and with its red card taken away."""
    home = state.is_home(team_id)
    reds = [state.teams[state.home_id].red_cards, state.teams[state.away_id].red_cards]
    side = 0 if home else 1
    reds[side] = max(0, reds[side] - 1)
    actual = state.compute_win_probability().for_team(home)
    what_if = state.compute_win_probability(reds=(reds[0], reds[1])).for_team(home)
    return _evidence(ELEVEN, team_id, actual, what_if)
