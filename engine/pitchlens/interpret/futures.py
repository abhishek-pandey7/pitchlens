"""Futures (docs/rust-expansion-plan.md §3): the rest of the match simulated many times.

The model lives in the Rust kernel (`engine/core/kernel/src/futures.rs`, which documents it in full): steps
of 0.25 minutes to full time; goals at each side's current rate, raised for a trailing side and lowered
for a leading one as time runs out (game state), lifted by momentum and field tilt that fade with a
half-life of a few minutes, and further red cards at each side's card and foul rate, each applying the
win probability's x0.7 / x1.2 multipliers. With every effect off it is the closed-form win probability.

- `futures_input(state)` builds the kernel's input from a `MatchState` (with what-if overrides).
- `simulate(input)` runs it: 100,000 paths in the Rust core when it is loaded, otherwise `simulate_py`,
  a NumPy version of the same model over 10,000 paths. The two agree within Monte Carlo error (they use
  different random generators), not bit for bit.
- `run_futures(state)` returns the `Futures` schema model for the match as it stands.

Every run is seeded from the match id and the latest event's sequence number, so a replay or a seek
gives the same futures.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from pitchlens import core
from pitchlens.schema.futures import Comeback, Futures, FuturesEffects, FuturesInput, NextGoal, ScoreChance

from .win_prob import RED_CARD_OPPONENT, RED_CARD_OWN, remaining_minutes

if TYPE_CHECKING:
    from .state import MatchState

DEFAULT_PATHS = 100_000  # Rust
FALLBACK_PATHS = 10_000  # NumPy
CHUNK_PATHS = 2048

# The model's constants: twins of engine/core/kernel/src/futures.rs (test_futures checks they agree).
STEP_MINUTES = 0.25
GAME_STATE_TRAILING_BOOST = 0.30
GAME_STATE_LEADING_DROP = 0.15
GAME_STATE_TWO_GOAL_SCALE = 1.5
GAME_STATE_RAMP_MINUTES = 90.0
MOMENTUM_LIFT = 0.35
MOMENTUM_WEIGHT = 0.7
TILT_WEIGHT = 0.3
MOMENTUM_HALF_LIFE_MINUTES = 4.0
RED_CARDS_PER_90 = 0.08
CARDS_PER_90 = 2.0
FOULS_PER_90 = 11.0
DISCIPLINE_PRIOR_MINUTES = 30.0
DISCIPLINE_MIN_INDEX = 0.25
DISCIPLINE_MAX_INDEX = 4.0
MAX_REDS = 3
TOP_SCORES = 10
GAME_STATES = 5  # goal difference from home's view: <= -2, -1, 0, 1, >= 2
SEED_BITS = 53
_MASK64 = (1 << 64) - 1


def native_simulate() -> Any:
    """The Rust core's `simulate_futures`, or None without the core (or with an older build)."""
    return getattr(core.native, "simulate_futures", None)


def default_paths() -> int:
    return DEFAULT_PATHS if native_simulate() is not None else FALLBACK_PATHS


# ---------------------------------------------------------------------------------------------- inputs
def red_card_rate_90(cards: int, fouls: int, minutes_played: float) -> float:
    """A side's red cards per 90 minutes from here: the average rate scaled by how often the side has
    been booked and has fouled, each blended with 30 minutes of average behaviour."""
    played = max(minutes_played, 0.0)
    span = (played + DISCIPLINE_PRIOR_MINUTES) / 90.0
    prior = DISCIPLINE_PRIOR_MINUTES / 90.0
    card_index = (cards + CARDS_PER_90 * prior) / (CARDS_PER_90 * span)
    foul_index = (fouls + FOULS_PER_90 * prior) / (FOULS_PER_90 * span)
    index = min(max(0.5 * card_index + 0.5 * foul_index, DISCIPLINE_MIN_INDEX), DISCIPLINE_MAX_INDEX)
    return RED_CARDS_PER_90 * index


def _splitmix64(z: int) -> int:
    z = (z + 0x9E3779B97F4A7C15) & _MASK64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
    return z ^ (z >> 31)


def futures_seed(match_id: str, seq: int) -> int:
    """FNV-1a of the match id mixed with the event's sequence number, kept to 53 bits (JSON-safe)."""
    h = 0xCBF29CE484222325
    for b in match_id.encode("utf-8"):
        h = ((h ^ b) * 0x100000001B3) & _MASK64
    return _splitmix64(h ^ _splitmix64(seq & _MASK64)) & ((1 << SEED_BITS) - 1)


def futures_input(
    state: MatchState,
    *,
    paths: int | None = None,
    effects: FuturesEffects | None = None,
    score: tuple[int, int] | None = None,
    minute: float | None = None,
    reds: tuple[int, int] | None = None,
    momentum: float | None = None,
    field_tilt: float | None = None,
) -> FuturesInput:
    """The kernel's input for the match as it stands; the keyword overrides make a what-if (the minute
    override moves the clock: the period follows from it and the added time is the default estimate)."""
    home, away = state.teams[state.home_id], state.teams[state.away_id]
    rate_h, rate_a, left = state.scoring_inputs()
    clock = state.clock_ms / 60_000
    period = state.period
    if minute is not None:
        clock, period = float(minute), 1 if minute < 45 else 2
        left = remaining_minutes(clock, period, None)
    elif state.finished:
        left = 0.0
    # The simulation's clock: the first half's stoppage time is not modelled, so the second half follows.
    start = min(clock, 45.0) if period == 1 else clock
    played = max(0.0, state.match_ms / 60_000)
    now = state.match_ms
    return FuturesInput(
        score=list(score if score is not None else state.score),
        minute=round(start, 4),
        minutes_left=left,
        rate_90=[rate_h, rate_a],
        reds=list(reds if reds is not None else (home.red_cards, away.red_cards)),
        momentum=state.dynamics.momentum(now) if momentum is None else momentum,
        field_tilt=state.dynamics.field_tilt(state.home_id, now) if field_tilt is None else field_tilt,
        red_rate_90=[
            red_card_rate_90(home.yellow_cards + home.red_cards, home.fouls, played),
            red_card_rate_90(away.yellow_cards + away.red_cards, away.fouls, played),
        ],
        effects=effects or FuturesEffects(),
        paths=paths or default_paths(),
        seed=futures_seed(state.match_id, state.last_seq),
    )


# ---------------------------------------------------------------------------------------------- running
def simulate(inp: FuturesInput) -> tuple[dict[str, Any], str]:
    """Run the model: (raw result as the kernel returns it, backend)."""
    native = native_simulate()
    if native is not None:
        raw = native(
            inp.score,
            inp.minute,
            inp.minutes_left,
            inp.rate_90,
            inp.reds,
            inp.momentum,
            inp.field_tilt,
            inp.red_rate_90,
            inp.paths,
            inp.seed,
            game_state=inp.effects.game_state,
            momentum_effect=inp.effects.momentum,
            discipline=inp.effects.discipline,
        )
        return raw, "rust"
    return simulate_py(inp), "python"


def to_futures(
    raw: dict[str, Any],
    inp: FuturesInput,
    *,
    backend: str,
    match_id: str,
    team_ids: tuple[str, str],
    match_ms: int,
    clock_ms: int,
    period: int,
    hypothetical: bool = False,
) -> Futures:
    comeback = None
    if raw["comeback"] is not None:
        side, avoid, win = raw["comeback"]
        team = team_ids[0] if side == "home" else team_ids[1]
        comeback = Comeback(team=team, side=side, avoid_defeat=round(avoid, 4), win=round(win, 4))
    nh, na, nn, median = raw["next_goal"]
    return Futures(
        match_id=match_id,
        match_ms=match_ms,
        clock_ms=clock_ms,
        period=period,
        score=list(inp.score),
        backend=backend,  # type: ignore[arg-type]
        hypothetical=hypothetical,
        paths=raw["paths"],
        home=round(raw["home"], 4),
        draw=round(raw["draw"], 4),
        away=round(raw["away"], 4),
        scores=[ScoreChance(home=h, away=a, p=round(p, 4)) for h, a, p in raw["scores"]],
        other=round(raw["other"], 4),
        comeback=comeback,
        next_goal=NextGoal(
            home=round(nh, 4),
            away=round(na, 4),
            none=round(nn, 4),
            median_minute=None if median is None else round(median, 1),
        ),
        goals_left=[round(raw["goals_left"][0], 3), round(raw["goals_left"][1], 3)],
        ms=round(raw["ms"], 2),
        input=inp,
    )


def run_input(state: MatchState, inp: FuturesInput, *, hypothetical: bool = False) -> Futures:
    """Simulate `inp` and label the result with `state`'s match and moment."""
    raw, backend = simulate(inp)
    return to_futures(
        raw,
        inp,
        backend=backend,
        match_id=state.match_id,
        team_ids=(state.home_id, state.away_id),
        match_ms=state.match_ms,
        clock_ms=state.clock_ms,
        period=state.period,
        hypothetical=hypothetical,
    )


def run_futures(state: MatchState, *, paths: int | None = None, **overrides: Any) -> Futures:
    """Futures for the match as it stands, or for a what-if when overrides are given."""
    inp = futures_input(state, paths=paths, **overrides)
    return run_input(state, inp, hypothetical=bool(overrides))


# ---------------------------------------------------------------------------------------------- NumPy
class _Model:
    """The kernel's rate tables, vectorised: per-step factors and cumulative hazards per path state."""

    def __init__(self, inp: FuturesInput) -> None:
        left = max(inp.minutes_left, 0.0)
        n = math.ceil(left / STEP_MINUTES) if left > 0 else 0
        self.n = n
        self.start = np.arange(n, dtype=np.float64) * STEP_MINUTES
        self.length = np.minimum(left - self.start, STEP_MINUTES)
        fx = inp.effects
        self.gs = np.ones((2, GAME_STATES, n))
        if fx.game_state and n:
            remaining = left - (self.start + self.length / 2.0)
            urgency = np.clip(1.0 - remaining / GAME_STATE_RAMP_MINUTES, 0.0, 1.0)
            for c, d in enumerate(range(-2, 3)):
                self.gs[0, c] = _game_state_factor(d, urgency)
                self.gs[1, c] = _game_state_factor(-d, urgency)
        self.mom = np.ones((2, n))
        if fx.momentum and n:
            signal = min(
                max(MOMENTUM_WEIGHT * inp.momentum + TILT_WEIGHT * (2.0 * inp.field_tilt - 1.0), -1.0), 1.0
            )
            mid = self.start + self.length / 2.0
            lift = MOMENTUM_LIFT * signal * np.power(0.5, mid / MOMENTUM_HALF_LIFE_MINUTES)
            self.mom[0], self.mom[1] = 1.0 + lift, 1.0 - lift
        self.reds0 = np.array(inp.reds, dtype=np.int64)
        self.levels = np.array(
            [MAX_REDS - r + 1 if fx.discipline and r < MAX_REDS else 1 for r in inp.reds], dtype=np.int64
        )
        self.base = np.array(inp.rate_90) / 90.0
        self.red_per_min = np.array(inp.red_rate_90) / 90.0 if fx.discipline else np.zeros(2)
        l0, l1 = int(self.levels[0]), int(self.levels[1])
        self.states = GAME_STATES * l0 * l1
        s = np.arange(self.states)
        self.cum = np.zeros((self.states, n + 1))
        if n:
            k = np.arange(n)
            rates = self.rates(np.repeat(s, n), np.tile(k, self.states)).reshape(self.states, n, 4)
            self.cum[:, 1:] = np.cumsum(rates.sum(axis=2) * self.length, axis=1)

    def state(self, diff: np.ndarray, levels: np.ndarray) -> np.ndarray:
        c = np.clip(diff, -2, 2) + 2
        return (c * self.levels[0] + levels[:, 0]) * self.levels[1] + levels[:, 1]

    def rates(self, s: np.ndarray, k: np.ndarray) -> np.ndarray:
        """Per-minute rates (home goal, away goal, home red, away red) for states `s` in steps `k`."""
        l0, l1 = self.levels
        ra = s % l1
        rh = (s // l1) % l0
        c = s // (l0 * l1)
        reds_h, reds_a = self.reds0[0] + rh, self.reds0[1] + ra
        mult_h = np.power(RED_CARD_OWN, reds_h) * np.power(RED_CARD_OPPONENT, reds_a)
        mult_a = np.power(RED_CARD_OWN, reds_a) * np.power(RED_CARD_OPPONENT, reds_h)
        out = np.empty((len(s), 4))
        out[:, 0] = self.base[0] * mult_h * self.gs[0, c, k] * self.mom[0, k]
        out[:, 1] = self.base[1] * mult_a * self.gs[1, c, k] * self.mom[1, k]
        out[:, 2] = np.where(rh + 1 < l0, self.red_per_min[0], 0.0)
        out[:, 3] = np.where(ra + 1 < l1, self.red_per_min[1], 0.0)
        return out


def _game_state_factor(d: int, urgency: np.ndarray) -> np.ndarray:
    scale = GAME_STATE_TWO_GOAL_SCALE if abs(d) >= 2 else 1.0
    if d < 0:
        return 1.0 + GAME_STATE_TRAILING_BOOST * scale * urgency
    if d > 0:
        return 1.0 - GAME_STATE_LEADING_DROP * scale * urgency
    return np.ones_like(urgency)


def simulate_py(inp: FuturesInput) -> dict[str, Any]:
    """The futures model in NumPy, vectorised over paths: every live path draws its next event at once.
    Returns the same dict as `pitchlens_core.simulate_futures`."""
    started = time.perf_counter()
    model = _Model(inp)
    n, paths = model.n, inp.paths
    rng = np.random.default_rng(inp.seed)
    x = np.zeros(paths)  # position in steps
    diff = np.full(paths, inp.score[0] - inp.score[1], dtype=np.int64)
    levels = np.zeros((paths, 2), dtype=np.int64)
    goals = np.zeros((paths, 2), dtype=np.int64)
    first_side = np.full(paths, -1, dtype=np.int64)
    first_t = np.full(paths, np.nan)
    live = np.arange(paths)
    cum = model.cum
    while live.size:
        s = model.state(diff[live], levels[live])
        xs = x[live]
        k0 = np.minimum(np.floor(xs).astype(np.int64), max(n - 1, 0))
        if n:
            now = cum[s, k0] + (xs - k0) * (cum[s, k0 + 1] - cum[s, k0])
            now = np.where(xs >= n, cum[s, n], now)
        else:
            now = np.zeros(live.size)
        target = now + rng.standard_exponential(live.size)
        going = target < cum[s, n]
        live, s, target = live[going], s[going], target[going]
        if not live.size:
            break
        k = np.empty(live.size, dtype=np.int64)
        for state in np.unique(s):  # a few states at a time; each row is sorted
            pick = s == state
            k[pick] = np.searchsorted(cum[state], target[pick], side="right") - 1
        k = np.clip(k, 0, n - 1)
        lo, hi = cum[s, k], cum[s, k + 1]
        frac = np.clip((target - lo) / (hi - lo), 0.0, 1.0)
        x[live] = k + frac
        r = model.rates(s, k)
        u = rng.random(live.size) * r.sum(axis=1)
        edges = np.cumsum(r, axis=1)
        kind = (u[:, None] >= edges[:, :3]).sum(axis=1)  # 0 home goal, 1 away goal, 2 home red, 3 away red
        scored = kind < 2
        side = kind[scored]
        who = live[scored]
        np.add.at(goals, (who, side), 1)
        diff[who] += np.where(side == 0, 1, -1)
        new = first_side[who] < 0
        first_side[who[new]] = side[new]
        first_t[who[new]] = (model.start[k[scored]] + frac[scored] * model.length[k[scored]])[new]
        sent = ~scored
        np.add.at(levels, (live[sent], kind[sent] - 2), 1)
    return _tally(inp, goals, first_side, first_t, started)


def _tally(
    inp: FuturesInput, goals: np.ndarray, first_side: np.ndarray, first_t: np.ndarray, started: float
) -> dict[str, Any]:
    paths = inp.paths
    h = inp.score[0] + goals[:, 0]
    a = inp.score[1] + goals[:, 1]
    home = int(np.count_nonzero(h > a)) / paths
    draw = int(np.count_nonzero(h == a)) / paths
    away = int(np.count_nonzero(h < a)) / paths
    pairs, counts = np.unique(np.stack([h, a], axis=1), axis=0, return_counts=True)
    order = sorted(range(len(counts)), key=lambda i: (-counts[i], pairs[i][0], pairs[i][1]))[:TOP_SCORES]
    scores = [(int(pairs[i][0]), int(pairs[i][1]), int(counts[i]) / paths) for i in order]
    other = (paths - sum(int(counts[i]) for i in order)) / paths
    comeback = None
    if inp.score[0] < inp.score[1]:
        comeback = ("home", home + draw, home)
    elif inp.score[1] < inp.score[0]:
        comeback = ("away", away + draw, away)
    times = first_t[first_side >= 0]
    median = None
    if times.size:
        median = inp.minute + float(np.partition(times, times.size // 2)[times.size // 2])
    next_goal = (
        int(np.count_nonzero(first_side == 0)) / paths,
        int(np.count_nonzero(first_side == 1)) / paths,
        int(np.count_nonzero(first_side < 0)) / paths,
        median,
    )
    return {
        "paths": paths,
        "home": home,
        "draw": draw,
        "away": away,
        "scores": scores,
        "other": other,
        "comeback": comeback,
        "next_goal": next_goal,
        "goals_left": (float(goals[:, 0].mean()), float(goals[:, 1].mean())),
        "ms": (time.perf_counter() - started) * 1000,
    }
