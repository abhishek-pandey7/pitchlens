"""Live check of the configured LLM provider against real match insights (needs Azure credentials).

    LLM_PROVIDER=groq GROQ_API_KEY=... python scripts/llm_smoke.py   (free tier; waits for rate budget)
    LLM_PROVIDER=foundry_claude ANTHROPIC_FOUNDRY_RESOURCE=<resource> python scripts/llm_smoke.py
    LLM_PROVIDER=azure_openai AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_FAST_DEPLOYMENT=... \
        python scripts/llm_smoke.py

Narrates the comeback's key moments for three fans and reports how many lines came from the model,
how many passed grounding first time, fell back to templates, and the latency.
"""

from __future__ import annotations

import asyncio
import bisect
import os
import statistics
import sys
import time

from pitchlens.explain.engine import explain_match
from pitchlens.interpret.state import MatchState
from pitchlens.narrate.narrator import Narrator
from pitchlens.narrate.providers import build_provider
from pitchlens.narrate.templates import NarrationContext
from pitchlens.personalize.profiles import PRESETS
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture


async def main() -> int:
    name = os.environ.get("LLM_PROVIDER", "mock")
    provider = build_provider(name)
    if hasattr(provider, "max_wait_s"):  # free tiers: wait for the rate window instead of skipping lines
        provider.max_wait_s = dict.fromkeys(provider.max_wait_s, 120.0)
    league = generate_league()
    home, away = default_fixture(league, "comeback")
    match = simulate(league, home, away, seed=7, storyline=build(league, "comeback", home, away))
    state, insights = explain_match(match.meta, match.events)
    # The live pipeline narrates with the scoreline at the moment, not the final score: replay it.
    replay, times, scores = MatchState(match.meta), [], []
    for event in match.events:
        replay.apply(event)
        times.append(event.match_ms)
        scores.append(replay.score)

    def ctx_at(match_ms: int) -> NarrationContext:
        return NarrationContext.from_meta(state.meta, scores[bisect.bisect_right(times, match_ms) - 1])

    moments = sorted(insights, key=lambda i: -i.salience)[:8]
    fans = [p for p in PRESETS if p.profile_id in ("p-casual-es", "p-analyst-en", "p-casual-hi")]
    narrator = Narrator(provider)
    latencies = []
    print(f"provider: {provider.name}\n")
    for insight in moments:
        for fan in fans:
            start = time.perf_counter()
            narration = await narrator.narrate(insight, fan, ctx_at(insight.match_ms))
            latencies.append((time.perf_counter() - start) * 1000)
            print(f"[{narration.source:8s}] {fan.profile_id:14s} {insight.headline_key:34s} {narration.text}")
    s = narrator.stats
    total = len(moments) * len(fans)
    print(f"\n{total} narrations: {s['llm_passed']} from the model ({s['llm_retried']} after a retry), "
          f"{s['template_fallback']} template fallbacks, {s['provider_errors']} provider errors")  # fmt: skip
    print(f"latency p50 {statistics.median(latencies):.0f} ms, max {max(latencies):.0f} ms")
    return 0 if name == "mock" or s["llm_passed"] > 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
