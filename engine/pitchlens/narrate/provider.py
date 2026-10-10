"""The LLM provider interface (§10). Narration code depends only on this protocol.

Implementations: `mock` (offline, deterministic, in this package), and `azure_openai` / `foundry_claude`
(T-51). Requests carry no sampling parameters: current Claude models reject `temperature` and friends,
and grounding is enforced by the validator, not by sampling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class Tier(StrEnum):
    FAST = "fast"  # live lines: low latency, low effort
    QUALITY = "quality"  # recaps and pundit sheets: more effort


@dataclass(frozen=True)
class LLMRequest:
    system: str  # stable per persona + language, so providers can cache it
    user: str  # the per-moment part: evidence JSON and instructions
    tier: Tier = Tier.FAST
    max_tokens: int = 400
    json_schema: dict[str, Any] | None = None  # structured output shape, when the provider supports it
    payload: dict[str, Any] = field(default_factory=dict)  # the structured input `user` was rendered from


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class LLMError(RuntimeError):
    """A provider failed (network, refusal, quota). Callers fall back to template text."""


class LLMProvider(Protocol):
    name: str

    async def generate(self, request: LLMRequest) -> LLMResponse: ...
