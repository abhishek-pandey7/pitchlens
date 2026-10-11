"""Director (T-125): the programme gate's rules, and Director turns in auto and approve mode. Offline."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pitchlens.agents.clients import OfflineChatClient
from pitchlens.agents.desk import AgentDesk
from pitchlens.agents.director import Director
from pitchlens.live.hub import Hub
from pitchlens.live.store import MatchStore
from pitchlens.render.cues import CueScheduler
from pitchlens.render.programme import Action, Mode, ProgrammeGate
from pitchlens.schema.overlays import CueTemplate, CueText, OverlayCue, Region


class Clock:
    def __init__(self) -> None:
        self.ms = 60 * 60_000

    def __call__(self) -> tuple[int, int, int]:
        return self.ms, self.ms, 2


def cue(cue_id: str, *, region: Region = Region.LOWER_THIRD, priority: int = 50) -> OverlayCue:
    return OverlayCue(
        cue_id=cue_id, profile_id="p", match_id="m", start_ms=0, duration_ms=6000, priority=priority,
        region=region, template=CueTemplate.LOWER_THIRD, text=CueText(lang="en", headline=f"story {cue_id}"),
    )  # fmt: skip


def gate(mode: Mode, max_per_min: int = 6) -> tuple[ProgrammeGate, list[OverlayCue], Clock]:
    aired: list[OverlayCue] = []
    clock = Clock()
    return ProgrammeGate(CueScheduler(max_per_min), aired.append, clock, mode=mode), aired, clock


# ---------------------------------------------------------------- the gate
def test_open_gate_and_must_show_cues_never_wait() -> None:
    g, aired, _ = gate(Mode.OFF)
    g.offer(cue("a"))
    assert [c.cue_id for c in aired] == ["a"] and not g.pending and not g.log  # off: nothing to log
    g.set_mode(Mode.APPROVE)
    g.offer(cue("goal", priority=100))
    assert aired[-1].cue_id == "goal" and g.log[0]["by"] == "rules" and g.log[0]["reason"] == "always on air"


def test_auto_decisions_follow_the_scheduler_rules() -> None:
    g, aired, clock = gate(Mode.AUTO)
    wakes: list[int] = []
    g.on_offer = lambda: wakes.append(1)
    g.offer(cue("a"))
    g.offer(cue("b"))
    assert set(g.pending) == {"a", "b"} and len(wakes) == 2 and g.offers == 2 and not aired

    clock.ms += 5_000
    assert g.decide("a", Action.AIR, by="director", reason="fresh").result == "aired"
    assert aired[0].start_ms == clock.ms  # aired now, not back-dated to the insight
    refused = g.decide("b", Action.AIR, by="director")
    assert refused.result == "refused" and "lower_third busy" in refused.detail and "b" in g.pending
    assert g.decide("b", Action.HOLD, by="director", reason="later").result == "held"
    assert g.undecided()[0].held  # held cues come back to the Director
    assert g.decide("b", Action.DROP, by="producer").result == "dropped" and not g.pending
    assert g.decide("zzz", Action.AIR, by="director").result == "unknown"
    assert [e["result"] for e in g.log] == ["dropped", "held", "refused", "aired"]  # unknown ids: not logged


def test_approve_mode_waits_for_the_producer() -> None:
    g, aired, _ = gate(Mode.APPROVE)
    g.offer(cue("a"))
    assert g.propose("a", Action.AIR, "big moment").result == "proposed" and not aired
    assert g.undecided() == []  # proposed: not the Director's any more
    assert g.snapshot()["pending"][0]["proposal"] == "air"
    assert g.approve("a").result == "aired" and aired[0].cue_id == "a"
    assert g.log[0]["by"] == "producer" and g.log[0]["reason"] == "approved: big moment"


def test_stale_cues_expire_and_switching_off_releases_the_rest() -> None:
    g, aired, clock = gate(Mode.APPROVE)
    g.offer(cue("old"))
    clock.ms += 4 * 60_000
    g.offer(cue("new", region=Region.TOP_RIGHT))
    assert list(g.pending) == ["new"] and g.log[0]["result"] == "expired"
    g.set_mode(Mode.OFF)
    assert [c.cue_id for c in aired] == ["new"] and not g.pending


def test_fallback_airs_only_undecided_cues() -> None:
    g, aired, _ = gate(Mode.AUTO)
    g.offer(cue("a"))
    g.offer(cue("b", region=Region.TOP_RIGHT))
    g.decide("b", Action.HOLD, by="director")
    g.fallback("director unavailable")
    assert [c.cue_id for c in aired] == ["a"] and list(g.pending) == ["b"]


# ---------------------------------------------------------------- the Director on a live replay
async def run_replay(hub: Hub, match_id: str, *, from_min: int = 70) -> None:
    session = hub.session(match_id)
    await hub.start_replay(match_id, speed=600, frames=False, from_ms=from_min * 60_000)
    for _ in range(600):
        await asyncio.sleep(0.05)
        if session.state.finished:
            break
    await session.drain_narrations()


async def test_auto_mode_director_puts_cues_on_air() -> None:
    hub = Hub(MatchStore(data_dirs=[]), narrate=False)
    desk = AgentDesk(lambda: hub, notes=False)
    desk.director.min_gap_s = 0
    record = hub.store.generate(seed=7, storyline="comeback", tracking=False)
    try:
        programme = desk.direct(record.match_id, "p-analyst-en", Mode.AUTO)
        assert programme["mode"] == "auto" and programme["profile_id"] == "p-analyst-en"
        await run_replay(hub, record.match_id)
        state = desk.programme(record.match_id)
        aired = [e for e in state["log"] if e["by"] == "director" and e["result"] == "aired"]
        assert aired, state["log"]
        traces = [t for t in desk.traces(record.match_id) if t["kind"] == "director"]
        assert traces and traces[0]["steps"][0]["tool_calls"][0]["tool"] == "list_pending_cues"
        channel = hub.live_session(record.match_id).channels["p-analyst-en"]  # type: ignore[union-attr]
        assert any(o.event == "cue" for o in channel.log)  # the programme gets its cues via the Director
    finally:
        await hub.shutdown()


async def test_approve_mode_holds_cues_for_the_producer() -> None:
    hub = Hub(MatchStore(data_dirs=[]), narrate=False)
    desk = AgentDesk(lambda: hub, notes=False)
    desk.director.min_gap_s = 0
    record = hub.store.generate(seed=7, storyline="comeback", tracking=False)
    try:
        desk.direct(record.match_id, "p-analyst-en", Mode.APPROVE)
        await run_replay(hub, record.match_id, from_min=88)
        state = desk.programme(record.match_id)
        proposed = [p for p in state["pending"] if p["proposal"] == "air"]
        assert proposed, state
        decided = desk.produce(record.match_id, proposed[0]["cue_id"], "approve")
        assert decided["outcome"]["result"] in ("aired", "refused")
        assert decided["log"][0]["by"] == "producer"
    finally:
        await hub.shutdown()


async def test_a_failing_director_falls_back_to_the_rules() -> None:
    hub = Hub(MatchStore(data_dirs=[]), narrate=False)
    traces: list[dict[str, Any]] = []
    director = Director(
        client=lambda role, tier: OfflineChatClient([], role=role),  # exhausted script: every call fails
        on_trace=lambda match_id, trace: traces.append(trace),
        min_gap_s=0,
    )
    record = hub.store.generate(seed=7, storyline="comeback", tracking=False)
    try:
        session = hub.session(record.match_id)
        g = director.configure(session, "p-analyst-en", Mode.AUTO)
        await run_replay(hub, record.match_id)
        assert traces and traces[0]["steps"][0]["error"]
        assert any(e["by"] == "rules" and e["reason"].startswith("director unavailable") for e in g.log)
        assert json.dumps(g.snapshot())  # JSON-able for the API
    finally:
        await hub.shutdown()


def test_a_proposal_stands_until_the_producer_acts() -> None:
    g, _, _ = gate(Mode.APPROVE)
    g.offer(cue("a"))
    g.propose("a", Action.AIR, "fresh")
    second = g.propose("a", Action.HOLD, "wait")
    assert not second.ok and "already proposed: air" in second.detail
    assert g.pending["a"].proposal is Action.AIR and len(g.log) == 1
