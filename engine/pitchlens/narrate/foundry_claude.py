"""Claude on Microsoft Foundry (T-51).

- Both tiers default to `claude-opus-5-5`. Thinking is always on for this model, so tiers differ by
  effort: `low` for live lines (latency), `medium` for recaps. `max_tokens` leaves room for thinking.
- JSON comes back through structured outputs (`output_config.format`), not prompt-parsing alone.
- Safety-classifier refusals (HTTP 200, `stop_reason: "refusal"`) are retried on a fallback model by the
  SDK's client-side middleware (server-side fallbacks are not offered on Foundry). If the chain still
  refuses, or anything else fails, we raise LLMError and the narrator uses grounded template text.
- Auth: `ANTHROPIC_FOUNDRY_API_KEY` if set, otherwise Microsoft Entra ID (managed identity in Azure,
  `az login` locally) via azure-identity, so no secrets are needed in the deployment.
"""

from __future__ import annotations

import os
import time
from typing import Any

import anthropic

from .provider import LLMError, LLMRequest, LLMResponse, Tier

AI_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
EFFORT = {Tier.FAST: "low", Tier.QUALITY: "medium"}
THINKING_HEADROOM = {Tier.FAST: 3000, Tier.QUALITY: 12_000}  # thinking counts toward max_tokens


def entra_token_provider() -> Any:
    from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

    return get_bearer_token_provider(DefaultAzureCredential(), AI_SERVICES_SCOPE)


class FoundryClaudeProvider:
    name = "foundry_claude"

    def __init__(
        self,
        *,
        fast_model: str = "claude-opus-5-5",
        quality_model: str = "claude-opus-5-5",
        fallback_model: str = "claude-opus-5",
        timeout_s: float = 30.0,
        client: Any | None = None,
    ) -> None:
        self.models = {Tier.FAST: fast_model, Tier.QUALITY: quality_model}
        if client is None:
            auth: dict[str, Any] = {}
            if not os.environ.get("ANTHROPIC_FOUNDRY_API_KEY"):
                auth["azure_ad_token_provider"] = entra_token_provider()
            client = anthropic.AsyncAnthropicFoundry(
                **auth,
                timeout=timeout_s,
                max_retries=2,
                middleware=[anthropic.BetaRefusalFallbackMiddleware([{"model": fallback_model}])],
            )
        self.client = client

    async def generate(self, request: LLMRequest) -> LLMResponse:
        output_config: dict[str, Any] = {"effort": EFFORT[request.tier]}
        if request.json_schema:
            output_config["format"] = {"type": "json_schema", "schema": request.json_schema}
        start = time.perf_counter()
        try:
            response = await self.client.beta.messages.create(
                model=self.models[request.tier],
                max_tokens=request.max_tokens + THINKING_HEADROOM[request.tier],
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                output_config=output_config,
            )
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Foundry returned {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise LLMError(f"Foundry unreachable: {exc}") from exc
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise LLMError(f"declined by safety classifier ({category})")
        if response.stop_reason == "max_tokens":
            raise LLMError("reply truncated at max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), "")
        usage = response.usage
        return LLMResponse(
            text=text,
            model=response.model,
            latency_ms=int((time.perf_counter() - start) * 1000),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )
