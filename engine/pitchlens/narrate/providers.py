"""Choose the LLM provider from configuration (LLM_PROVIDER = mock | foundry_claude | azure_openai)."""

from __future__ import annotations

import logging
import os

from .mock import MockProvider
from .provider import LLMProvider

log = logging.getLogger("pitchlens")


def build_provider(name: str) -> LLMProvider:
    if name == "foundry_claude":
        from .foundry_claude import FoundryClaudeProvider

        return FoundryClaudeProvider(
            fast_model=os.environ.get("FOUNDRY_FAST_MODEL", "claude-opus-5-5"),
            quality_model=os.environ.get("FOUNDRY_QUALITY_MODEL", "claude-opus-5-5"),
            fallback_model=os.environ.get("FOUNDRY_FALLBACK_MODEL", "claude-opus-5"),
        )
    if name == "azure_openai":
        from .azure_openai import AzureOpenAIProvider

        return AzureOpenAIProvider()
    if name != "mock":
        log.warning("unknown LLM_PROVIDER %r; using the mock provider", name)
    return MockProvider()
