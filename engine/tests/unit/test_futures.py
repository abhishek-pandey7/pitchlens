"""Futures (docs/rust-expansion-plan.md §3): the NumPy model, its agreement with the Rust kernel, the input
builder, the futures_swing insight, counterfactual futures, the live session's runs and the agent tool.

Runs in both core modes; the Rust comparisons skip when the extension isn't built."""

from __future__ import annotations

import asyncio
import math

import pytest

from pitchlens import core
from pitchlens.agents.tools import MatchTools, MatchView
from pitchlens.explain.engine import ExplainEngine, explain_match
from pitchlens.ingest.bus import BusMessage, MessageKind
from pitchlens.interpret import futures as F
from pitchlens.interpret.state import MatchState, build_state
from pitchlens.interpret.win_prob import remaining_minutes, win_probability_py
from pitchlens.live.session import MatchSession
from pitchlens.narrate.grounding import validate
from pitchlens.narrate.templates import NarrationContext, render
from pitchlens.render.cues import build_cue
from pitchlens.schema.futures import Futures, FuturesEffects, FuturesInput
from pitchlens.schema.insights import InsightType
from pitchlens.schema.overlays import CueTemplate
from pitchlens.schema.profiles import SUPPORTED_LANGUAGES, Persona, Profile
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture

OFF = FuturesEffects(game_state=False, momentum=False, discipline=False)
native = getattr(core.native, "simulate_futures", None)
needs_rust = pytest.mark.skipif(native is None, reason="the Rust core is not loaded")


def match(storyline: str = "comeback", seed: int = 7):
    league = generate_league()
    home, away = default_fixture(league, storyline)
    return simulate(league, home, away, seed=seed, storyline=build(league, storyline, home, away))


MATCH = match()


def inp(
    score=(0, 0), minute=0.0, *, effects=None, paths=10_000, seed=1, reds=(0, 0), momentum=0.0, tilt=0.5
) -> FuturesInput:
    period = 1 if minute < 45 else 2
    return FuturesInput(
        score=list(score),
        minute=minute,
        minutes_left=remaining_minutes(minute, period, None),
        rate_90=[1.45, 1.15],
        reds=list(reds),
        momentum=momentum,
        field_tilt=tilt,
        red_rate_90=[F.red_card_rate_90(2, 10, minute), F.red_card_rate_90(1, 14, minute)],
        effects=effects or FuturesEffects(),
        paths=paths,
        seed=seed,
    )


def sigma(p: float, *ns: int) -> float:
    return math.sqrt(sum(p * (1 - p) / n for n in ns)) + 1e-9


# ------------------------------------------------------------------------------------------ the model
@pytest.mark.parametrize(
    ("score", "minute", "reds"), [((0, 0), 0.0, (0, 0)), ((1, 0), 60.0, (0, 0)), ((0, 2), 30.0, (0, 1))]
)
def test_numpy_model_with_effects_off_is_the_closed_form(score, minute, reds) -> None:
    i = inp(score, minute, effects=OFF, paths=40_000, reds=reds)
    got = F.simulate_py(i)
    wp = win_probability_py(
        *score, home_rate_90=1.45, away_rate_90=1.15, minutes_left=i.minutes_left, home_reds=reds[0],
        away_reds=reds[1],
    )  # fmt: skip
    for key, want in (("home", wp.home), ("draw", wp.draw), ("away", wp.away)):
        assert abs(got[key] - want) <= 4 * sigma(want, i.paths), (key, got[key], want)


def test_numpy_model_sense_checks_and_shape() -> None:
    late = inp((0, 0), 89.0, paths=20_000)
    late = late.model_copy(update={"minutes_left": remaining_minutes(89.0, 2, 4.0)})
    assert F.simulate_py(late)["draw"] > 0.85
    rout = F.simulate_py(inp((3, 0), 60.0, paths=20_000))
    assert rout["comeback"][0] == "away" and rout["comeback"][2] < 0.02
    even = F.simulate_py(inp((0, 0), 30.0, paths=20_000))
    red = F.simulate_py(inp((0, 0), 30.0, paths=20_000, reds=(1, 0)))
    assert red["home"] < even["home"] - 0.03
    f = F.simulate_py(inp((1, 0), 20.0))
    assert abs(f["home"] + f["draw"] + f["away"] - 1) < 1e-12
    assert len(f["scores"]) == F.TOP_SCORES and abs(sum(s[2] for s in f["scores"]) + f["other"] - 1) < 1e-9
    assert f["scores"] == sorted(f["scores"], key=lambda s: (-s[2], s[0], s[1]))
    assert 20 < f["next_goal"][3] < 95
    assert F.simulate_py(inp((1, 0), 20.0)) | {"ms": 0} == f | {"ms": 0}  # same seed, same futures
    assert F.simulate_py(inp((1, 0), 20.0, seed=2)) | {"ms": 0} != f | {"ms": 0}


def test_finished_match_has_one_future() -> None:
    i = inp((2, 1), 90.0).model_copy(update={"minutes_left": 0.0})
    f = F.simulate_py(i)
    assert (f["home"], f["draw"], f["away"]) == (1.0, 0.0, 0.0)
    assert f["scores"] == [(2, 1, 1.0)] and f["next_goal"] == (0.0, 0.0, 1.0, None)


def test_seed_is_json_safe_and_differs_per_event() -> None:
    a, b = F.futures_seed("SYN-1", 10), F.futures_seed("SYN-1", 11)
    assert a != b and 0 <= a < 2**53 and F.futures_seed("SYN-1", 10) == a
    assert F.futures_seed("SYN-2", 10) != a


# ------------------------------------------------------------------------------------------ Rust parity
@needs_rust
def test_python_constants_seed_and_discipline_match_the_kernel() -> None:
    constants = core.native.futures_constants()
    for name, value in constants.items():
        if name in ("DEFAULT_PATHS", "MAX_PATHS"):
            continue
        assert getattr(F, name) == value, name
    assert constants["DEFAULT_PATHS"] == F.DEFAULT_PATHS
    for match_id, seq in (("TEST", 1), ("SYN-NHR-SVC-1000", 2718), ("é", 0)):
        assert F.futures_seed(match_id, seq) == core.native.futures_seed(match_id, seq)
    for cards, fouls, played in ((0, 0, 0.0), (3, 14, 61.5), (6, 30, 20.0), (0, 1, 90.0)):
        assert F.red_card_rate_90(cards, fouls, played) == core.native.red_card_rate_90(cards, fouls, played)


@needs_rust
def test_numpy_fallback_agrees_with_rust_within_three_sigma_at_20_states() -> None:
    states = [
        ((h, a), minute, reds, mom)
        for (h, a), reds in (((0, 0), (0, 0)), ((1, 0), (0, 0)), ((0, 2), (1, 0)), ((2, 2), (0, 0)))
        for minute, mom in ((0.0, 0.0), (25.0, 0.4), (50.0, -0.3), (75.0, 0.6), (88.0, 0.0))
    ]
    assert len(states) == 20
    worst = 0.0
    for k, (score, minute, reds, mom) in enumerate(states):
        i = inp(score, minute, reds=reds, momentum=mom, tilt=0.5 + mom / 4, seed=k)
        rust_in = i.model_copy(update={"paths": 100_000})
        rs, _ = F.simulate(rust_in)
        py = F.simulate_py(i)
        for key in ("home", "draw", "away"):
            s = sigma(rs[key], 100_000, 10_000)
            worst = max(worst, abs(rs[key] - py[key]) / s)
            assert abs(rs[key] - py[key]) <= 3 * s + 1e-9, (score, minute, key, rs[key], py[key])
        for side in range(2):
            mean = rs["goals_left"][side]
            assert abs(mean - py["goals_left"][side]) <= 3 * math.sqrt(mean / 10_000) + 0.01
    assert worst < 3


@needs_rust
def test_rust_runs_are_deterministic_and_fast() -> None:
    i = inp((1, 1), 55.0, paths=100_000)
    a, _ = F.simulate(i)
    b, _ = F.simulate(i)
    assert {**a, "ms": 0} == {**b, "ms": 0}
    c, _ = F.simulate(i.model_copy(update={"seed": i.seed + 1}))
    assert {**a, "ms": 0} != {**c, "ms": 0}
    assert a["ms"] < 1000  # 100k paths: milliseconds (generous for loaded CI machines)


# ------------------------------------------------------------------------------------------ inputs
def test_input_builder_reads_the_match_and_takes_overrides() -> None:
    state = MatchState(MATCH.meta)
    for e in MATCH.events:
        if e.match_ms > 70 * 60_000:
            break
        state.apply(e)
    i = F.futures_input(state)
    rate_h, rate_a, left = state.scoring_inputs()
    assert i.score == list(state.score) and i.rate_90 == [rate_h, rate_a] and i.minutes_left == left
    assert i.minute == pytest.approx(state.clock_ms / 60_000, abs=1e-4)
    assert i.momentum == state.dynamics.momentum(state.match_ms)
    assert i.seed == F.futures_seed(state.match_id, state.last_seq) and i.paths == F.default_paths()
    what_if = F.futures_input(state, score=(3, 0), minute=30, reds=(0, 1), momentum=-0.5, paths=500)
    assert what_if.score == [3, 0] and what_if.minutes_left == remaining_minutes(30, 1, None)
    assert (what_if.reds, what_if.momentum, what_if.paths) == ([0, 1], -0.5, 500)
    f = F.run_futures(state, paths=2000, score=(3, 0))
    assert f.hypothetical and f.input is not None and f.score == [3, 0] and f.home > 0.9


def test_futures_at_full_time_are_the_result() -> None:
    state, *_ = build_state(MATCH.meta, MATCH.events)
    f = F.run_futures(state)
    assert (f.home, f.draw, f.away) == (0.0, 0.0, 1.0)  # the comeback: 2-3
    assert f.scores[0].home == 2 and f.scores[0].away == 3 and f.next_goal.median_minute is None
    assert f.backend == ("rust" if native else "python")


# ------------------------------------------------------------------------------------------ explain
def futures(state: MatchState, home: float, draw: float, *, behind: str | None = None, avoid: float = 0.0):
    comeback = None
    if behind:
        side = "home" if behind == state.home_id else "away"
        comeback = {"team": behind, "side": side, "avoid_defeat": avoid, "win": avoid / 2}
    return Futures.model_validate(
        {
            "match_id": state.match_id, "match_ms": state.match_ms, "clock_ms": state.clock_ms,
            "period": state.period, "score": list(state.score), "backend": "rust", "paths": 100_000,
            "home": home, "draw": draw, "away": round(1 - home - draw, 4),
            "scores": [{"home": 2, "away": 1, "p": 0.21}, {"home": 1, "away": 1, "p": 0.18}], "other": 0.61,
            "comeback": comeback, "next_goal": {"home": 0.5, "away": 0.4, "none": 0.1, "median_minute": 71.0},
            "goals_left": [0.8, 0.6], "ms": 4.0,
        }
    )  # fmt: skip


def test_futures_swing_insight_is_grounded_in_every_language() -> None:
    state = MatchState(MATCH.meta)
    for e in MATCH.events[:1500]:
        state.apply(e)
    engine = ExplainEngine(state)
    assert engine.on_futures(futures(state, 0.40, 0.30)) == []  # nothing to compare with yet
    assert engine.on_futures(futures(state, 0.48, 0.27)) == []  # an 8-point move: not a swing
    (insight,) = engine.on_futures(futures(state, 0.66, 0.20))
    assert insight.type is InsightType.FUTURES_SWING and insight.headline_key == "futures_swing.win_up"
    assert insight.entities.teams == [state.home_id]
    metrics = {e.metric: e for e in insight.evidence}
    assert (metrics["futures_win"].from_, metrics["futures_win"].to) == (0.48, 0.66)
    assert metrics["futures_top_score"].value == "2-1" and metrics["futures_paths"].value == 100_000
    assert engine.on_futures(futures(state, 0.40, 0.30)) == []  # cooldown: the same team, too soon
    hypothetical = futures(state, 0.1, 0.1).model_copy(update={"hypothetical": True})
    assert engine.on_futures(hypothetical) == []
    ctx = NarrationContext.from_state(state)
    for lang in SUPPORTED_LANGUAGES:
        for persona in (Persona.CASUAL, Persona.ANALYST):
            text = render(insight, ctx, Profile(profile_id="p", persona=persona, language=lang)).text
            assert "{" not in text and "66" in text, (lang, text)
            assert validate(text, insight, ctx, lang).passed, (lang, text)
    cue = build_cue(insight, Profile(profile_id="p", language="en"), headline="x")
    assert cue.template is CueTemplate.FUTURES_BAR and cue.data["futures_home"] == 0.66


def test_comeback_swing_and_a_falling_win() -> None:
    state = MatchState(MATCH.meta)
    for e in MATCH.events[:1500]:
        state.apply(e)
    engine = ExplainEngine(state)
    engine.on_futures(futures(state, 0.2, 0.3, behind=state.home_id, avoid=0.5))
    (insight,) = engine.on_futures(futures(state, 0.12, 0.18, behind=state.home_id, avoid=0.3))
    assert insight.headline_key == "futures_swing.comeback_down"  # avoid-defeat moved 20 points


def test_big_chances_and_red_cards_carry_futures_for_the_hypothetical() -> None:
    red_match = match("red_card_turn", 3)
    _, insights = explain_match(red_match.meta, red_match.events)
    chances = [i for i in insights if i.type is InsightType.BIG_CHANCE]
    reds = [i for i in insights if i.headline_key in ("card.red", "card.second_yellow")]
    assert chances and reds
    for insight, prefix in [(i, "what_if_futures") for i in chances] + [(i, "no_red_futures") for i in reds]:
        metrics = {e.metric: e for e in insight.evidence}
        assert 0 <= metrics[f"{prefix}_win"].value <= 1 and "-" in metrics[f"{prefix}_score"].value
    ctx = NarrationContext.from_meta(red_match.meta)
    for lang in SUPPORTED_LANGUAGES:
        text = render(chances[0], ctx, Profile(profile_id="p", persona=Persona.ANALYST, language=lang)).text
        assert validate(text, chances[0], ctx, lang).passed, (lang, text)


# ------------------------------------------------------------------------------------------ live session
async def test_session_runs_futures_off_the_loop_and_pushes_them() -> None:
    session = MatchSession(MATCH.meta, bus=None)  # type: ignore[arg-type]
    sub = session.subscribe()
    for e in MATCH.events:
        session.handle(
            BusMessage(kind=MessageKind.EVENT, match_id=MATCH.meta.match_id, match_ms=e.match_ms, event=e)
        )
        if session._futures_task is not None and not session._futures_task.done():
            await session._futures_task  # let each run land (a replay at speed would skip some)
    await session.drain_narrations()
    state = session.state
    history = state.futures_history
    assert state.futures is not None and state.futures.backend == ("rust" if native else "python")
    assert len(history) >= 19  # every 5 match minutes at least, plus goals, cards and subs
    assert [h["match_ms"] for h in history] == sorted(h["match_ms"] for h in history)
    assert history[-1]["result"] == [0.0, 0.0, 1.0]  # the comeback: 2-3 at full time
    snapshot = state.snapshot()["dynamics"]["futures"]
    assert snapshot["input"] is None and snapshot["paths"] == F.default_paths()
    out = [sub.queue.get_nowait() for _ in range(sub.queue.qsize())]
    states = [o for o in out if o is not None and o.event == "state" and '"futures":{' in o.data]
    assert states  # futures reach clients in state messages
    swings = [i for i in session.engine.insights if i.type is InsightType.FUTURES_SWING]
    assert swings  # a comeback moves the futures


async def test_session_reset_drops_runs_in_flight() -> None:
    session = MatchSession(MATCH.meta, bus=None)  # type: ignore[arg-type]
    e = MATCH.events[0]
    session.handle(
        BusMessage(kind=MessageKind.EVENT, match_id=MATCH.meta.match_id, match_ms=e.match_ms, event=e)
    )
    task = session._futures_task
    assert task is not None
    session.reset()  # a seek: the run in flight describes a state that is gone
    await asyncio.gather(task, return_exceptions=True)
    assert session.state.futures is None and session.state.futures_history == []


# ------------------------------------------------------------------------------------------ agent tool
def test_get_futures_tool_answers_can_they_still_win() -> None:
    state = MatchState(MATCH.meta)
    for e in MATCH.events:
        if e.clock_ms > 75 * 60_000 and e.period == 2:
            break
        state.apply(e)
    tools = MatchTools(lambda: MatchView(state))
    out = tools.get_futures()
    home, away = state.teams[state.home_id].name, state.teams[state.away_id].name
    assert set(out["result"]) == {home, "draw", away}
    assert abs(sum(out["result"].values()) - 1) < 1e-3
    assert out["simulated_endings"] == F.default_paths() and out["most_likely_final_scores"]
    assert isinstance(out["comeback"], (dict, str))
    assert tools.ledger.calls[-1].tool == "get_futures"
    assert "get_futures" in [fn.__name__ for fn in tools.functions()]
