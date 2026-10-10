"""Choose the LLM provider from configuration.

LLM_PROVIDER = mock | groq | openai_compat (free development) | foundry_claude | azure_openai (Azure).
"""

from __future__ import annotations

import logging
import os

from .mock import MockProvider
from .provider import LLMError, LLMProvider

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
    if name in ("groq", "openai_compat"):
        from .openai_compat import generic_provider, groq_provider

        try:
            return groq_provider() if name == "groq" else generic_provider()
        except LLMError as exc:
            log.warning("%s; using the mock provider", exc)
            return MockProvider()
    if name != "mock":
        log.warning("unknown LLM_PROVIDER %r; using the mock provider", name)
    return MockProvider()
