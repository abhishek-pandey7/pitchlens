"""OpenAI-compatible chat endpoints for free local development: Groq, Ollama, OpenRouter, GitHub Models.

Azure credits are kept for the final deployment (Foundry). During development any endpoint that speaks
the OpenAI chat-completions API can narrate. The `groq` preset defaults to the gpt-oss models, which
support strict JSON-schema output and a `reasoning_effort` control.

Free tiers are rate limited (Groq: 30 requests and 8K tokens per minute), so calls pass through a
client-side `RateBudget`. A live line that would have to wait more than a couple of seconds for budget
fails fast with LLMError, and the narrator serves the grounded template text instead of queueing behind
HTTP 429s.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import openai

from .provider import LLMError, LLMRequest, LLMResponse, Tier

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
REASONING_EFFORT = {Tier.FAST: "low", Tier.QUALITY: "medium"}
REASONING_HEADROOM = {Tier.FAST: 1000, Tier.QUALITY: 4000}  # reasoning tokens count toward the limit
MAX_WAIT_S = {Tier.FAST: 2.0, Tier.QUALITY: 30.0}  # live lines are not worth waiting for
WINDOW_S = 60.0


class RateBudget:
    """Sliding one-minute window over requests and tokens, settled with real usage after each call."""

    def __init__(
        self, rpm: int, tpm: int, *, clock: Callable[[], float] = time.monotonic, sleep: Any = asyncio.sleep
    ) -> None:
        self.rpm, self.tpm = rpm, tpm
        self._clock, self._sleep = clock, sleep
        self._window: deque[list[float]] = deque()  # [timestamp, tokens]

    def _prune(self, now: float) -> None:
        while self._window and now - self._window[0][0] >= WINDOW_S:
            self._window.popleft()

    async def acquire(self, tokens: int, max_wait_s: float) -> list[float]:
        deadline = self._clock() + max_wait_s
        while True:
            now = self._clock()
            self._prune(now)
            used = sum(entry[1] for entry in self._window)
            if len(self._window) < self.rpm and (used + tokens <= self.tpm or not self._window):
                entry = [now, float(tokens)]
                self._window.append(entry)
                return entry
            wait = self._window[0][0] + WINDOW_S - now
            if now + wait > deadline:
                raise LLMError(f"rate budget exhausted ({self.rpm} req / {self.tpm} tokens per minute)")
            await self._sleep(wait)

    @staticmethod
    def settle(entry: list[float], tokens: int) -> None:
        entry[1] = float(tokens)


def estimate_tokens(request: LLMRequest) -> int:
    return (len(request.system) + len(request.user)) // 4 + request.max_tokens // 2


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str = "openai_compat",
        base_url: str | None = None,
        api_key: str | None = None,
        fast_model: str = "",
        quality_model: str | None = None,
        structured: str = "json_schema",  # json_schema | json_object | none
        reasoning: bool = False,  # send reasoning_effort and hide the reasoning text
        rpm: int | None = None,
        tpm: int | None = None,
        timeout_s: float = 30.0,
        client: Any | None = None,
        budget: RateBudget | None = None,
    ) -> None:
        self.name = name
        self.models = {Tier.FAST: fast_model, Tier.QUALITY: quality_model or fast_model}
        self.structured, self.reasoning = structured, reasoning
        self.max_wait_s = dict(MAX_WAIT_S)  # scripts may raise it to wait for budget
        self.budget = budget or (RateBudget(rpm or 10_000, tpm or 10_000_000) if rpm or tpm else None)
        if client is None:
            client = openai.AsyncOpenAI(
                base_url=base_url, api_key=api_key or "unused", timeout=timeout_s, max_retries=1
            )
        self.client = client

    async def generate(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {}
        max_tokens = request.max_tokens
        if request.json_schema and self.structured == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "narration", "schema": request.json_schema, "strict": True},
            }
        elif request.json_schema and self.structured == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        if self.reasoning:
            kwargs["reasoning_effort"] = REASONING_EFFORT[request.tier]
            kwargs["extra_body"] = {"include_reasoning": False}
            max_tokens += REASONING_HEADROOM[request.tier]

        entry = None
        if self.budget:
            entry = await self.budget.acquire(estimate_tokens(request), self.max_wait_s[request.tier])
        start = time.perf_counter()
        try:
            response = await self.client.chat.completions.create(
                model=self.models[request.tier],
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                max_completion_tokens=max_tokens,
                **kwargs,
            )
        except openai.APIStatusError as exc:
            raise LLMError(f"{self.name} returned {exc.status_code}: {exc.message}") from exc
        except openai.APIConnectionError as exc:  # includes timeouts
            raise LLMError(f"{self.name} unreachable: {exc}") from exc
        usage = response.usage
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        if entry is not None:
            RateBudget.settle(entry, input_tokens + output_tokens)
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMError(f"model refused: {choice.message.refusal}")
        if choice.finish_reason == "length":
            raise LLMError("reply truncated at max_completion_tokens")
        return LLMResponse(
            text=choice.message.content or "",
            model=response.model,
            latency_ms=int((time.perf_counter() - start) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def groq_provider() -> OpenAICompatibleProvider:
    """Groq's free tier. Needs GROQ_API_KEY (console.groq.com); the defaults suit the free limits."""
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise LLMError("GROQ_API_KEY is not set")
    return OpenAICompatibleProvider(
        name="groq",
        base_url=GROQ_BASE_URL,
        api_key=key,
        fast_model=os.environ.get("GROQ_FAST_MODEL", "openai/gpt-oss-20b"),
        quality_model=os.environ.get("GROQ_QUALITY_MODEL", "openai/gpt-oss-120b"),
        structured="json_schema",
        reasoning=True,
        rpm=int(os.environ.get("GROQ_RPM", "30")),
        tpm=int(os.environ.get("GROQ_TPM", "8000")),
    )


def generic_provider() -> OpenAICompatibleProvider:
    """Any OpenAI-compatible endpoint (Ollama, OpenRouter, GitHub Models, ...), configured by env."""
    rpm, tpm = os.environ.get("OPENAI_COMPAT_RPM"), os.environ.get("OPENAI_COMPAT_TPM")
    return OpenAICompatibleProvider(
        base_url=os.environ.get("OPENAI_COMPAT_BASE_URL"),
        api_key=os.environ.get("OPENAI_COMPAT_API_KEY"),
        fast_model=os.environ.get("OPENAI_COMPAT_FAST_MODEL", ""),
        quality_model=os.environ.get("OPENAI_COMPAT_QUALITY_MODEL"),
        structured=os.environ.get("OPENAI_COMPAT_STRUCTURED", "json_schema"),
        rpm=int(rpm) if rpm else None,
        tpm=int(tpm) if tpm else None,
    )
