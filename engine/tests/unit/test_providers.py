"""Real-provider adapters, tested offline with fake SDK clients (no network, no credentials)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import openai
import pytest

from pitchlens.narrate.azure_openai import AzureOpenAIProvider
from pitchlens.narrate.foundry_claude import FoundryClaudeProvider
from pitchlens.narrate.mock import MockProvider
from pitchlens.narrate.narrator import Narrator
from pitchlens.narrate.provider import LLMError, LLMRequest, Tier
from pitchlens.narrate.providers import build_provider
from pitchlens.narrate.templates import NarrationContext
from pitchlens.schema.insights import Entities, Evidence, Insight, InsightType
from pitchlens.schema.profiles import Profile

SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}
REQUEST = LLMRequest(system="sys", user="usr", tier=Tier.FAST, max_tokens=400, json_schema=SCHEMA)


# ---------------------------------------------------------------- fakes
class FakeAnthropic:
    def __init__(
        self, *, stop_reason: str = "end_turn", text: str = '{"text": "hi"}', error: Exception | None = None
    ):
        self.calls: list[dict[str, Any]] = []
        self.stop_reason, self.text, self.error = stop_reason, text, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            stop_details=SimpleNamespace(category="cyber") if self.stop_reason == "refusal" else None,
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text=self.text),
            ],
            model=kwargs["model"],
            usage=SimpleNamespace(input_tokens=120, output_tokens=30),
        )


class FakeOpenAI:
    def __init__(
        self,
        *,
        finish_reason: str = "stop",
        content: str = '{"text": "hi"}',
        refusal: str | None = None,
        error: Exception | None = None,
    ):
        self.calls: list[dict[str, Any]] = []
        self.finish_reason, self.content, self.refusal, self.error = finish_reason, content, refusal, error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        message = SimpleNamespace(content=self.content, refusal=self.refusal)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason=self.finish_reason)],
            model="gpt-deployment",
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )


def status_error(module: Any, code: int) -> Exception:
    request = httpx2.Request("POST", "https://example.invalid")
    response = httpx2.Response(code, request=request, json={"error": {"message": "nope"}})
    cls = {429: module.RateLimitError, 500: module.InternalServerError}[code]
    return cls("nope", response=response, body=None)


# ---------------------------------------------------------------- Foundry Claude
async def test_foundry_request_shape_per_tier() -> None:
    fake = FakeAnthropic()
    provider = FoundryClaudeProvider(client=fake)
    response = await provider.generate(REQUEST)
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert call["max_tokens"] > REQUEST.max_tokens  # headroom for always-on thinking
    assert call["system"] == "sys" and call["messages"] == [{"role": "user", "content": "usr"}]
    assert not {"temperature", "top_p", "top_k", "thinking"} & call.keys()
    assert json.loads(response.text) == {"text": "hi"} and response.input_tokens == 120

    await provider.generate(LLMRequest(system="s", user="u", tier=Tier.QUALITY))
    assert fake.calls[1]["output_config"] == {"effort": "medium"}


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (FakeAnthropic(stop_reason="refusal"), "declined"),
        (FakeAnthropic(stop_reason="max_tokens"), "truncated"),
        (FakeAnthropic(error=status_error(anthropic, 429)), "429"),
        (FakeAnthropic(error=status_error(anthropic, 500)), "500"),
        (
            FakeAnthropic(
                error=anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x.invalid"))
            ),
            "unreachable",
        ),
    ],
)
async def test_foundry_failures_become_llm_errors(fake: FakeAnthropic, message: str) -> None:
    with pytest.raises(LLMError, match=message):
        await FoundryClaudeProvider(client=fake).generate(REQUEST)


# ---------------------------------------------------------------- Azure OpenAI
async def test_azure_openai_request_shape() -> None:
    fake = FakeOpenAI()
    provider = AzureOpenAIProvider(client=fake, fast_deployment="live-dep", quality_deployment="recap-dep")
    response = await provider.generate(REQUEST)
    call = fake.calls[0]
    assert call["model"] == "live-dep"
    assert call["response_format"]["json_schema"] == {"name": "narration", "schema": SCHEMA, "strict": True}
    assert call["messages"][0] == {"role": "system", "content": "sys"}
    assert not {"temperature", "top_p"} & call.keys()
    assert response.text == '{"text": "hi"}' and response.output_tokens == 20
    await provider.generate(LLMRequest(system="s", user="u", tier=Tier.QUALITY))
    assert fake.calls[1]["model"] == "recap-dep"


@pytest.mark.parametrize(
    ("fake", "message"),
    [
        (FakeOpenAI(refusal="I can't help with that"), "refused"),
        (FakeOpenAI(finish_reason="length"), "truncated"),
        (FakeOpenAI(error=status_error(openai, 429)), "429"),
        (
            FakeOpenAI(error=openai.APIConnectionError(request=httpx2.Request("POST", "https://x.invalid"))),
            "unreachable",
        ),
    ],
)
async def test_azure_openai_failures_become_llm_errors(fake: FakeOpenAI, message: str) -> None:
    with pytest.raises(LLMError, match=message):
        await AzureOpenAIProvider(client=fake, fast_deployment="d").generate(REQUEST)


# ---------------------------------------------------------------- end to end with the narrator
CTX = NarrationContext(
    "m", "NHR", "SVC", {"NHR": "Northbridge", "SVC": "Southvale"}, {"NHR-10": "Reyes"}, (1, 0)
)
GOAL = Insight(
    insight_id="i-1", match_id="m", match_ms=1_200_000, clock_ms=1_200_000, period=1, type=InsightType.GOAL,
    salience=0.8, entities=Entities(teams=["NHR"], players=["NHR-10"]), headline_key="goal.go_ahead",
    evidence=[Evidence(metric="score", value="1-0")],
)  # fmt: skip


async def test_narrator_uses_grounded_claude_text_and_falls_back_on_refusal() -> None:
    good = FakeAnthropic(
        text=json.dumps({"text": "Reyes strikes! Northbridge lead 1-0.", "emphasis_entities": []})
    )
    narration = await Narrator(FoundryClaudeProvider(client=good)).narrate(GOAL, Profile(profile_id="p"), CTX)
    assert narration.source == "llm" and narration.text.startswith("Reyes strikes")

    invented = FakeAnthropic(
        text=json.dumps({"text": "Reyes scores his 12th of the season!", "emphasis_entities": []})
    )
    narration = await Narrator(FoundryClaudeProvider(client=invented)).narrate(
        GOAL, Profile(profile_id="p"), CTX
    )
    assert narration.source == "template" and len(invented.calls) == 2  # retried once, then grounded fallback

    refused = FakeAnthropic(stop_reason="refusal")
    narrator = Narrator(FoundryClaudeProvider(client=refused))
    narration = await narrator.narrate(GOAL, Profile(profile_id="p"), CTX)
    assert narration.source == "template" and narrator.stats["provider_errors"] == 1


def test_build_provider_defaults_to_mock() -> None:
    assert isinstance(build_provider("mock"), MockProvider)
    assert isinstance(build_provider("something-else"), MockProvider)
