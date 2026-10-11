"""PitchLens MCP server (§10A, T-128): live match intelligence for any MCP client.

GitHub Copilot, Claude, a Foundry agent or any other Model Context Protocol client can ask PitchLens the
same questions our own agents do, through the same deterministic tools (`agents/tools.py`). Two ways
to run it:

    mounted on the engine     https://<engine>/mcp  (streamable HTTP; live sessions + stored matches)
    stand-alone over stdio    python -m pitchlens.mcpserver  (stored matches, for a local VS Code)

Every tool takes a `match_id` (see `list_matches`). Results are JSON. Every number comes from the
engine, never from a model.
"""

from __future__ import annotations

import functools
import inspect
import typing
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from pitchlens.agents.tools import MatchTools, MatchView, ToolError
from pitchlens.agents.views import MatchViews
from pitchlens.live.hub import Hub

INSTRUCTIONS = (
    "PitchLens explains synthetic football matches with deterministic metrics. Call list_matches "
    "first, then pass a match_id to the other tools. Every number comes from the engine; quote "
    "numbers from tool results only. Probabilities and shares are 0..1."
)


def _with_match_id(method_name: str, template: Callable[..., Any], views: MatchViews) -> Callable[..., Any]:
    """An async MCP tool: `(match_id, <the analyst tool's own parameters>)` -> the tool's result."""
    original = inspect.signature(template)

    async def call(match_id: str, **kwargs: Any) -> dict[str, Any]:
        try:
            tools = MatchTools(await views.view(match_id))
            return getattr(tools, method_name)(**kwargs)
        except ToolError as exc:
            return {"unavailable": str(exc)}

    # tools.py uses postponed annotations (strings); resolve them to real types in its own namespace.
    hints = typing.get_type_hints(template, include_extras=True)
    match_param = inspect.Parameter("match_id", inspect.Parameter.KEYWORD_ONLY, annotation=str)
    params = [match_param] + [
        p.replace(kind=inspect.Parameter.KEYWORD_ONLY, annotation=hints.get(p.name, Any))
        for p in original.parameters.values()
    ]
    call.__signature__ = original.replace(parameters=params, return_annotation=dict[str, Any])  # type: ignore[attr-defined]
    call.__annotations__ = {
        "match_id": str,
        **{p.name: p.annotation for p in params[1:]},
        "return": dict[str, Any],
    }
    call.__name__ = method_name
    call.__doc__ = template.__doc__
    return call


def build_server(hub: Callable[[], Hub], views: MatchViews | None = None) -> MCPServer:
    views = views or MatchViews(hub)
    server = MCPServer("pitchlens", title="PitchLens match intelligence", instructions=INSTRUCTIONS)

    def list_matches() -> dict[str, Any]:
        """Matches PitchLens knows about (stored, generated or live), with teams and storyline."""
        h = hub()
        return {
            "matches": [
                {**r.summary(), "live": h.live_session(r.match_id) is not None} for r in h.store.list()
            ]
        }

    server.add_tool(list_matches, name="list_matches")
    # The analyst tools, bound to a match per call. Signatures come from an unbound template instance.
    template = MatchTools(lambda: MatchView(None))  # type: ignore[arg-type]
    for fn in template.functions():
        server.add_tool(_with_match_id(fn.__name__, fn, views), name=fn.__name__)
    return server


@functools.cache
def tool_names() -> list[str]:
    template = MatchTools(lambda: MatchView(None))  # type: ignore[arg-type]
    return ["list_matches", *(fn.__name__ for fn in template.functions())]
