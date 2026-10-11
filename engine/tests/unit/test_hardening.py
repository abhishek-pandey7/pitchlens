"""Hardening (T-77): rate limits on public endpoints, agent cost caps, readiness. Offline."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from pitchlens.agents.desk import AgentDesk, CostCaps, DeskBusy
from pitchlens.api.ratelimit import RateLimitMiddleware, default_rules
from pitchlens.live.hub import Hub
from pitchlens.live.store import MatchStore
from pitchlens.personalize.profiles import PRESETS
from pitchlens.render.programme import Mode


def limited_app(env: dict[str, str], clock: list[float], **kwargs: Any) -> FastAPI:
    app = FastAPI()

    @app.post("/api/v1/matches/{match_id}/ask")
    def ask(match_id: str) -> dict[str, str]:
        return {"ok": match_id}

    @app.post("/api/v1/profiles")
    def profile() -> dict[str, str]:
        return {"ok": "profile"}

    @app.get("/api/v1/matches")
    def matches() -> list[str]:
        return []

    app.add_middleware(RateLimitMiddleware, rules=default_rules(env), clock=lambda: clock[0], **kwargs)
    return app


async def test_agent_endpoints_are_limited_per_client_and_window() -> None:
    clock = [1000.0]
    app = limited_app({"RATE_LIMIT_AGENTS": "3", "RATE_LIMIT_WRITES": "0"}, clock)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        codes = [(await client.post("/api/v1/matches/m/ask")).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        refused = await client.post("/api/v1/matches/m/ask")
        assert int(refused.headers["retry-after"]) > 0 and "a minute" in refused.json()["detail"]
        reads = [(await client.get("/api/v1/matches")).status_code for _ in range(10)]
        writes = [(await client.post("/api/v1/profiles")).status_code for _ in range(10)]
        assert set(reads) == {200} and set(writes) == {200}  # reads are free; a limit of 0 is off
        clock[0] += 61
        assert (await client.post("/api/v1/matches/m/ask")).status_code == 200  # the window moved on


async def test_proxy_header_keys_clients_only_when_trusted() -> None:
    clock = [0.0]
    trusted = limited_app({"RATE_LIMIT_AGENTS": "1"}, clock, trust_proxy_headers=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=trusted), base_url="http://t") as client:
        for ip in ("1.1.1.1", "2.2.2.2"):  # two fans behind the same proxy each get their own budget
            r = await client.post("/api/v1/matches/m/ask", headers={"x-forwarded-for": f"{ip}, 10.0.0.1"})
            assert r.status_code == 200
    untrusted = limited_app({"RATE_LIMIT_AGENTS": "1"}, clock, trust_proxy_headers=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=untrusted), base_url="http://t") as client:
        codes = [
            (await client.post("/api/v1/matches/m/ask", headers={"x-forwarded-for": ip})).status_code
            for ip in ("1.1.1.1", "2.2.2.2")
        ]
        assert codes == [200, 429]  # a spoofed header does not buy a new budget


def test_cost_caps_per_match_and_per_day() -> None:
    caps = CostCaps(per_match=1000, per_day=1500)
    caps.add("a", 900)
    assert caps.exceeded("a") is None
    caps.add("a", 200)
    assert caps.exceeded("a") == "the agents' budget for this match is used up" and caps.exceeded("b") is None
    caps.add("b", 500)
    assert caps.exceeded("c") == "the agents' daily budget is used up"


async def test_over_the_cap_agents_stand_down() -> None:
    hub = Hub(MatchStore(data_dirs=[]), narrate=False)
    desk = AgentDesk(lambda: hub, notes=False)
    desk.caps = CostCaps(per_match=10, per_day=0)
    desk.director.budget = desk.caps.exceeded
    record = hub.store.generate(seed=7, storyline="comeback", tracking=False)
    try:
        desk.caps.add(record.match_id, 50)
        with pytest.raises(DeskBusy, match="budget for this match"):
            await desk.ask(record.match_id, "Who won?", PRESETS[1])
        with pytest.raises(DeskBusy):
            await desk.recap(record.match_id, "ft", PRESETS[1])

        # The Director stands down too: in auto mode, cues air by the scheduler's rules.
        session = hub.session(record.match_id)
        gate = desk.director.configure(session, "p-analyst-en", Mode.AUTO)
        await hub.start_replay(record.match_id, speed=600, frames=False, from_ms=80 * 60_000)
        while not session.state.finished:
            await asyncio.sleep(0.05)
        await session.drain_narrations()
        log = list(gate.log)
        assert log and all(e["by"] == "rules" for e in log)
        assert any(e["reason"] == "the agents' budget for this match is used up" for e in log)
    finally:
        await hub.shutdown()
