"""FastAPI application (§14). Run locally with:

uvicorn pitchlens.api.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pitchlens import __version__, telemetry
from pitchlens.config import Settings
from pitchlens.live.hub import Hub
from pitchlens.live.store import MatchStore
from pitchlens.narrate.provider import LLMProvider
from pitchlens.narrate.providers import build_provider

from .routes import matches, misc, streams

API_PREFIX = "/api/v1"
log = logging.getLogger("pitchlens")


def provider_from(settings: Settings) -> LLMProvider:
    """The configured LLM provider (mock offline; Claude on Foundry or Azure OpenAI in Azure)."""
    return build_provider(settings.llm_provider)


def create_app(settings: Settings | None = None, *, hub: Hub | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        telemetry.configure(settings.appinsights_connection_string)
        app.state.settings = settings
        app.state.hub = hub or Hub(MatchStore(settings.data_dirs), provider=provider_from(settings))
        yield
        await app.state.hub.shutdown()

    app = FastAPI(
        title="PitchLens API",
        version=__version__,
        description="Explainable, personalized match intelligence from synthetic football events.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    for router in (misc.router, matches.router, streams.router):
        app.include_router(router, prefix=API_PREFIX)
    return app


app = create_app()
