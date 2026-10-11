"""Chat clients for the agent layer (§10A, T-120).

Microsoft Agent Framework agents talk to models through a chat client. This module builds one per tier
from the same `LLM_PROVIDER` switch the narrator uses, so agents run on Groq's free tier in
development and on Microsoft Foundry (Azure OpenAI deployments) in the final deployment:

    groq | openai_compat   OpenAIChatCompletionClient pointed at an OpenAI-compatible endpoint
    azure_openai           OpenAIChatCompletionClient on Azure OpenAI / Foundry (key or Entra ID)
    mock                   OfflineChatClient: deterministic, no network (tests, CI, keyless runs)

Our own thin wrapper keeps Agent Framework API churn out of the agents (plan §22).
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatContext,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
    chat_middleware,
)

from pitchlens.narrate.openai_compat import GROQ_BASE_URL, RateBudget, groq_budget
from pitchlens.narrate.provider import LLMError, Tier

AI_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
VERIFIED_MARKER = "VERIFIED ANSWER:"  # the Companion's input ends with this marker and the approved answer
# An agent turn makes several model calls of 2-4K tokens each, which a free tier (Groq: 8K tokens a
# minute) cannot serve in one burst. Agents wait for budget up to this long, then give up cleanly.
AGENT_MAX_WAIT_S = float(os.environ.get("AGENT_MAX_WAIT_S", "70"))
# Caps per agent turn. When one is hit the framework asks for a text-only answer.
TOOL_LIMITS = {"max_iterations": 6, "max_function_calls": 8}


def budget_middleware(budget: RateBudget, max_wait_s: float = AGENT_MAX_WAIT_S) -> Any:
    """Apply a shared request/token budget to every model call an agent makes."""

    @chat_middleware
    async def _budget(context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        chars = sum(len(m.text or "") for m in context.messages)
        entry = await budget.acquire(chars // 4 + 1000, max_wait_s)
        await call_next()
        usage = getattr(context.result, "usage_details", None) or {}
        total = usage.get("total_token_count") if isinstance(usage, dict) else None
        if total:
            RateBudget.settle(entry, int(total))

    return _budget


def chat_client(tier: Tier = Tier.FAST, provider: str | None = None, *, role: str = "analyst") -> Any:
    """A chat client for the configured provider; falls back to the offline client without credentials."""
    name = provider or os.environ.get("LLM_PROVIDER", "mock")
    if name == "groq" and os.environ.get("GROQ_API_KEY"):
        from agent_framework.openai import OpenAIChatCompletionClient

        model = os.environ.get("GROQ_QUALITY_MODEL" if tier is Tier.QUALITY else "GROQ_FAST_MODEL")
        return OpenAIChatCompletionClient(
            model or "openai/gpt-oss-120b",
            api_key=os.environ["GROQ_API_KEY"],
            base_url=GROQ_BASE_URL,
            middleware=[budget_middleware(groq_budget())],
            function_invocation_configuration=TOOL_LIMITS,
        )
    if name == "openai_compat" and os.environ.get("OPENAI_COMPAT_BASE_URL"):
        from agent_framework.openai import OpenAIChatCompletionClient

        model = os.environ.get(
            "OPENAI_COMPAT_QUALITY_MODEL" if tier is Tier.QUALITY else "OPENAI_COMPAT_FAST_MODEL"
        )
        return OpenAIChatCompletionClient(
            model or os.environ.get("OPENAI_COMPAT_FAST_MODEL", ""),
            api_key=os.environ.get("OPENAI_COMPAT_API_KEY") or "unused",
            base_url=os.environ["OPENAI_COMPAT_BASE_URL"],
            function_invocation_configuration=TOOL_LIMITS,
        )
    if name == "azure_openai" and os.environ.get("AZURE_OPENAI_ENDPOINT"):
        from agent_framework.openai import OpenAIChatCompletionClient

        fast = os.environ.get("AZURE_OPENAI_FAST_DEPLOYMENT", "")
        deployment = fast
        if tier is Tier.QUALITY:
            deployment = os.environ.get("AZURE_OPENAI_QUALITY_DEPLOYMENT") or fast
        auth: dict[str, Any] = (
            {"api_key": os.environ["AZURE_OPENAI_API_KEY"]} if os.environ.get("AZURE_OPENAI_API_KEY") else {}
        )
        if not auth:
            from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

            auth["credential"] = get_bearer_token_provider(DefaultAzureCredential(), AI_SERVICES_SCOPE)
        return OpenAIChatCompletionClient(
            deployment,
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            function_invocation_configuration=TOOL_LIMITS,
            **auth,
        )
    return OfflineChatClient(role=role)


# ---------------------------------------------------------------- offline client
Step = str | tuple[str, dict[str, Any]] | Callable[[Sequence[Message]], "str | tuple[str, dict[str, Any]]"]


class OfflineChatClient(FunctionInvocationLayer, BaseChatClient):  # type: ignore[misc]
    """A deterministic stand-in for a model.

    With a `script`, it plays the steps in order: a string is a final answer, `(tool, args)` is a tool
    call, and a callable decides from the conversation so far. Without a script, it behaves like a
    cautious analyst: it calls `get_match_state` once, then answers from that result. Every number in
    the answer therefore comes from a tool, so offline runs exercise the whole grounded pipeline.
    As the Fact-Checker (`role="checker"`) it approves; as the Companion (`role="companion"`) it
    returns the verified answer it was given unchanged.
    """

    def __init__(self, script: Sequence[Step] | None = None, *, role: str = "analyst", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.script = list(script) if script is not None else None
        self.role = role
        self.requests: list[list[Message]] = []

    def _inner_get_response(
        self, *, messages: Sequence[Message], stream: bool, options: Any, **kwargs: Any
    ) -> Awaitable[ChatResponse]:
        self.requests.append(list(messages))
        if stream:
            raise LLMError("the offline client does not stream")
        step: Any = self._next(messages)
        if callable(step):
            step = step(messages)

        async def respond() -> ChatResponse:
            if isinstance(step, tuple):
                tool, args = step
                call = Content.from_function_call(f"call-{len(self.requests)}", tool, arguments=args)
                return ChatResponse(messages=Message("assistant", [call]), model="offline")
            return ChatResponse(messages=Message("assistant", [Content.from_text(step)]), model="offline")

        return respond()

    def _next(self, messages: Sequence[Message]) -> Step:
        if self.script is not None:
            if not self.script:
                raise LLMError("offline script exhausted")
            return self.script.pop(0)
        if self.role == "checker":
            return json.dumps({"verdict": "approve", "problems": []})
        if self.role == "tactical":
            results = [c for m in messages for c in m.contents if c.type == "function_result"]
            return (
                ("get_metric", {"metric": "press_rate"}) if not results else offline_note(results[-1].result)
            )
        if self.role == "companion":
            last = next((m.text for m in reversed(messages) if m.role == "user"), "") or ""
            return last.split(VERIFIED_MARKER, 1)[-1].strip()
        results = [c for m in messages for c in m.contents if c.type == "function_result"]
        if not results:
            return ("get_match_state", {})
        return offline_answer(results[-1].result)


def offline_answer(result: Any) -> str:
    """A plain, fully grounded answer built from a get_match_state result."""
    try:
        data = json.loads(result) if isinstance(result, str) else result
        wp = data["win_probability"]
        home, away = data["home"]["team"], data["away"]["team"]
        return (
            f"At {data['minute']} it is {data['score']}. Expected goals: {home} {data['home']['xg']}, "
            f"{away} {data['away']['xg']}. Win probability: {home} {round(wp[home] * 100)}%, "
            f"{away} {round(wp[away] * 100)}%."
        )
    except (KeyError, TypeError, ValueError):
        return "I can only answer with live match data, and none is available yet."


def offline_note(result: Any) -> str:
    """A grounded tactical note from a get_metric(press_rate) result: who is pressing harder."""
    try:
        data = json.loads(result) if isinstance(result, str) else result
        (team_a, a), (team_b, b) = sorted(data["value"].items(), key=lambda kv: -float(kv[1]))
        note = f"{team_a} are pressing harder: press rate {a} against {b} for {team_b}."
        return json.dumps({"found": True, "team": team_a, "note": note})
    except (KeyError, TypeError, ValueError):
        return json.dumps({"found": False})
