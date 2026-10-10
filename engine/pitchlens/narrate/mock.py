"""Offline, deterministic provider: echoes the reference line as structured output.

Used for local development, CI and the demo fallback. `faulty_first` makes the first N replies invent a
statistic, which exercises the grounding validator's retry and template fallback paths.
"""

from __future__ import annotations

import json

from .provider import LLMRequest, LLMResponse


class MockProvider:
    name = "mock"

    def __init__(self, *, faulty_first: int = 0) -> None:
        self.faulty_remaining = faulty_first
        self.calls = 0

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        text = str(request.payload.get("REFERENCE", ""))
        players = list(request.payload.get("ENTITIES", {}).get("players", []))
        if self.faulty_remaining > 0:
            self.faulty_remaining -= 1
            text += " That makes 7 goals in his last 3 games."
        body = json.dumps({"text": text, "emphasis_entities": players[:1]}, ensure_ascii=False)
        return LLMResponse(text=body, model="mock", latency_ms=0)
