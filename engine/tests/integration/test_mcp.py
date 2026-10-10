"""PitchLens MCP server (T-128): in-memory and over the engine's mounted /mcp endpoint."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp.client.client import Client

from pitchlens.live.hub import Hub
from pitchlens.live.store import MatchStore
from pitchlens.mcpserver.server import build_server, tool_names


def payload(result: Any) -> dict[str, Any]:
    return json.loads(result.content[0].text)


@pytest.fixture(scope="module")
def hub() -> Hub:
    hub = Hub(MatchStore(data_dirs=[]), narrate=False)
    hub.store.generate(seed=7, storyline="comeback", tracking=False)
    return hub


async def test_in_memory_client_sees_tools_and_answers(hub: Hub) -> None:
    async with Client(build_server(lambda: hub)) as client:
        listed = await client.list_tools()
        assert [t.name for t in listed.tools] == tool_names()
        schema = next(t for t in listed.tools if t.name == "get_window").input_schema
        assert {"match_id", "metric", "from_minute", "to_minute"} <= set(schema["properties"])

        matches = payload(await client.call_tool("list_matches", {}))["matches"]
        match_id = matches[0]["match_id"]
        assert matches[0]["live"] is False

        state = payload(await client.call_tool("get_match_state", {"match_id": match_id}))
        assert state["score"] == "Northbridge Harriers 2-3 Southvale City"
        unknown = payload(await client.call_tool("get_match_state", {"match_id": "nope"}))
        assert "unknown match" in unknown["unavailable"]
        bad = payload(await client.call_tool("get_metric", {"match_id": match_id, "metric": "shots"}))
        assert "unknown metric" in bad["unavailable"]


async def test_mounted_endpoint_on_the_engine(server: tuple[str, Hub]) -> None:
    base, _ = server
    root = base.removesuffix("/api/v1")
    created = httpx.post(f"{base}/matches", json={"seed": 3, "storyline": "comeback"}, timeout=120).json()
    async with Client(f"{root}/mcp") as client:
        assert len((await client.list_tools()).tools) == len(tool_names())
        result = payload(
            await client.call_tool("what_if", {"match_id": created["match_id"], "away_goals": 1})
        )
        assert result["what_if"]["score"] != result["actual"]["score"]
    assert httpx.get(f"{base}/health").status_code == 200  # the REST API still routes first
