"""FastAPI application (§14). Run locally with:

uvicorn pitchlens.api.main:app --reload
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.transport_security import TransportSecuritySettings

from pitchlens import __version__, telemetry
from pitchlens.agents.desk import AgentDesk
from pitchlens.api.ratelimit import RateLimitMiddleware
from pitchlens.config import Settings
from pitchlens.live.hub import Hub
from pitchlens.live.store import MatchStore
from pitchlens.mcpserver.server import build_server
from pitchlens.narrate.provider import LLMProvider
from pitchlens.narrate.providers import build_provider

from .routes import agents, matches, misc, streams

API_PREFIX = "/api/v1"
log = logging.getLogger("pitchlens")


def provider_from(settings: Settings) -> LLMProvider:
    """The configured LLM provider (mock offline; Claude on Foundry or Azure OpenAI in Azure)."""
    return build_provider(settings.llm_provider)


def create_app(settings: Settings | None = None, *, hub: Hub | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    desk = AgentDesk(lambda: app.state.hub)  # Ask the Match and the MCP server share its match cache
    mcp = build_server(lambda: app.state.hub, desk.views)
    # Mounted at exactly /mcp (no redirect to /mcp/). Stateless so any engine replica can answer.
    hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=[f"https://{h}" for h in hosts])
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, transport_security=security if hosts else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        telemetry.configure(settings.appinsights_connection_string)
        app.state.settings = settings
        app.state.hub = hub or Hub(MatchStore(settings.data_dirs), provider=provider_from(settings))
        app.state.agents = desk
        app.state.hub.session_hooks.append(desk.attach)
        async with mcp.session_manager.run():
            yield
        await app.state.hub.shutdown()

    app = FastAPI(
        title="PitchLens API",
        version=__version__,
        description="Explainable, personalized match intelligence from synthetic football events.",
        lifespan=lifespan,
    )
    # Starlette wraps later middleware around earlier ones: CORS stays outermost, so even a 429 from
    # the rate limiter carries CORS headers the browser can read.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,  # an allow-list from CORS_ORIGINS
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "Last-Event-ID", "Authorization", "Mcp-Session-Id"],
        expose_headers=["Retry-After"],
    )
    for router in (misc.router, matches.router, streams.router, agents.router):
        app.include_router(router, prefix=API_PREFIX)
    app.mount("/", mcp_app)  # last, so the API routes match first; serves POST/GET /mcp
    return app


app = create_app()
