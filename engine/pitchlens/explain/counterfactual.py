"""Counterfactual win probability (§23 P1, T-92): "what if" for the moments that nearly changed a match.

Pure maths on the same win-probability model as the live numbers, re-run with one thing changed:

- **a big chance** (missed, saved or blocked): the team's win probability now, and if it had gone in
- **a red card**: the dismissed team's win probability now, and if they still had eleven players

The result is evidence on the insight (`from` = what happened, `to` = the what-if), so it is grounded
like any other number, narrated in each fan's language and shown on the overlay's "What if" strip.

Each what-if also gets its futures (interpret/futures.py): the rest of the match simulated from the
hypothetical, as the team's share of simulated endings won and the most likely final score ("if that
chance had gone in: home win 61%, most likely 2-1"), next to the closed-form number. These runs use
fewer paths than the live futures (`WHAT_IF_PATHS`) because they run inside the explain stage.
"""

from __future__ import annotations

from pitchlens.interpret.futures import default_paths, run_futures
from pitchlens.interpret.state import MatchState
from pitchlens.schema.futures import Futures
from pitchlens.schema.insights import Evidence

SCORED = "what_if_win_prob"  # if the chance had gone in
ELEVEN = "no_red_win_prob"  # if the dismissed team still had eleven players
SCORED_FUTURES = "what_if_futures"  # evidence what_if_futures_win and what_if_futures_score
ELEVEN_FUTURES = "no_red_futures"  # no_red_futures_win and no_red_futures_score
WHAT_IF_PATHS = 20_000


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


def _paths() -> int:
    return min(default_paths(), WHAT_IF_PATHS)


def _futures_evidence(prefix: str, team_id: str, futures: Futures, is_home: bool) -> list[Evidence]:
    out = [Evidence(metric=f"{prefix}_win", team=team_id, value=round(futures.win(is_home), 3))]
    if futures.scores:
        top = futures.scores[0]
        out.append(Evidence(metric=f"{prefix}_score", value=f"{top.home}-{top.away}"))
    return out


def futures_if_scored(state: MatchState, team_id: str) -> list[Evidence]:
    """The futures with the chance converted: the team's share of simulated endings won, and the most
    likely final score."""
    home = state.is_home(team_id)
    h, a = state.score
    futures = run_futures(state, paths=_paths(), score=(h + 1, a) if home else (h, a + 1))
    return _futures_evidence(SCORED_FUTURES, team_id, futures, home)


def futures_if_eleven(state: MatchState, team_id: str) -> list[Evidence]:
    """The futures with the dismissed team's red card taken away."""
    home = state.is_home(team_id)
    reds = [state.teams[state.home_id].red_cards, state.teams[state.away_id].red_cards]
    side = 0 if home else 1
    reds[side] = max(0, reds[side] - 1)
    futures = run_futures(state, paths=_paths(), reds=(reds[0], reds[1]))
    return _futures_evidence(ELEVEN_FUTURES, team_id, futures, home)
