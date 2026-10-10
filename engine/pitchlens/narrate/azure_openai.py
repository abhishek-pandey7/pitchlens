"""Azure OpenAI (T-51), the alternative provider behind the same LLMProvider interface.

Structured output via a strict JSON-schema response format; no sampling parameters (grounding is the
validator's job). Model refusals, truncation, HTTP errors and timeouts become LLMError, so the narrator
falls back to grounded template text. Auth: `AZURE_OPENAI_API_KEY` if set, otherwise Microsoft Entra ID.
"""

from __future__ import annotations

import os
import time
from typing import Any

import openai

from .provider import LLMError, LLMRequest, LLMResponse, Tier

AI_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
DEFAULT_API_VERSION = "2024-10-21"


class AzureOpenAIProvider:
    name = "azure_openai"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        fast_deployment: str | None = None,
        quality_deployment: str | None = None,
        api_version: str | None = None,
        timeout_s: float = 30.0,
        client: Any | None = None,
    ) -> None:
        fast = fast_deployment or os.environ.get("AZURE_OPENAI_FAST_DEPLOYMENT", "")
        self.deployments = {
            Tier.FAST: fast,
            Tier.QUALITY: quality_deployment or os.environ.get("AZURE_OPENAI_QUALITY_DEPLOYMENT") or fast,
        }
        if client is None:
            auth: dict[str, Any] = {}
            if not os.environ.get("AZURE_OPENAI_API_KEY"):
                from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

                auth["azure_ad_token_provider"] = get_bearer_token_provider(
                    DefaultAzureCredential(), AI_SERVICES_SCOPE
                )
            client = openai.AsyncAzureOpenAI(
                azure_endpoint=endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT"),
                api_version=api_version or os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION),
                timeout=timeout_s,
                max_retries=2,
                **auth,
            )
        self.client = client

    async def generate(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {}
        if request.json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "narration", "schema": request.json_schema, "strict": True},
            }
        start = time.perf_counter()
        try:
            response = await self.client.chat.completions.create(
                model=self.deployments[request.tier],
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                max_completion_tokens=request.max_tokens,
                **kwargs,
            )
        except openai.APIStatusError as exc:
            raise LLMError(f"Azure OpenAI returned {exc.status_code}: {exc.message}") from exc
        except openai.APIConnectionError as exc:  # includes timeouts
            raise LLMError(f"Azure OpenAI unreachable: {exc}") from exc
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMError(f"model refused: {choice.message.refusal}")
        if choice.finish_reason == "length":
            raise LLMError("reply truncated at max_completion_tokens")
        usage = response.usage
        return LLMResponse(
            text=choice.message.content or "",
            model=response.model,
            latency_ms=int((time.perf_counter() - start) * 1000),
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
