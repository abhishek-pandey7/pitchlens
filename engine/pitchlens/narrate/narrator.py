"""Facts first, words second (§10): turn an Insight into a Narration for one fan profile.

1. Template text is always available instantly (and is grounded by construction).
2. The LLM rewrites it for the persona and language from the same evidence.
3. The grounding validator checks the rewrite; one retry with the violations as feedback, then the
   template is used. A narration never reaches a viewer without passing grounding.
"""

from __future__ import annotations

import time

from pitchlens.schema.insights import Insight
from pitchlens.schema.profiles import Narration, Profile

from .grounding import validate
from .prompts import build_request, parse_response
from .provider import LLMError, LLMProvider, Tier
from .templates import NarrationContext, render


def narration_id(insight: Insight, profile: Profile) -> str:
    return f"n-{insight.insight_id}-{profile.profile_id}"


class Narrator:
    def __init__(self, provider: LLMProvider, *, max_attempts: int = 2) -> None:
        self.provider = provider
        self.max_attempts = max_attempts
        self.stats = {"llm_passed": 0, "llm_retried": 0, "template_fallback": 0, "provider_errors": 0}

    def template(self, insight: Insight, profile: Profile, ctx: NarrationContext) -> Narration:
        text = render(insight, ctx, profile).text
        return Narration(
            narration_id=narration_id(insight, profile),
            insight_id=insight.insight_id,
            profile_id=profile.profile_id,
            lang=profile.language,
            text=text,
            source="template",
            grounding=validate(text, insight, ctx, profile.language),
            latency_ms=0,
        )

    async def narrate(
        self, insight: Insight, profile: Profile, ctx: NarrationContext, *, tier: Tier = Tier.FAST
    ) -> Narration:
        start = time.perf_counter()
        feedback: list[str] | None = None
        for attempt in range(self.max_attempts):
            request = build_request(insight, ctx, profile, tier=tier, feedback=feedback)
            try:
                response = await self.provider.generate(request)
            except LLMError:
                self.stats["provider_errors"] += 1
                break
            text, _ = parse_response(response.text)
            grounding = validate(text, insight, ctx, profile.language)
            if grounding.passed and text:
                self.stats["llm_passed"] += 1
                self.stats["llm_retried"] += attempt > 0
                return Narration(
                    narration_id=narration_id(insight, profile),
                    insight_id=insight.insight_id,
                    profile_id=profile.profile_id,
                    lang=profile.language,
                    text=text,
                    source="llm",
                    grounding=grounding,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                )
            feedback = grounding.violations or ["the line was empty"]
        self.stats["template_fallback"] += 1
        fallback = self.template(insight, profile, ctx)
        return fallback.model_copy(update={"latency_ms": int((time.perf_counter() - start) * 1000)})
