"""Fact-Checker eval: precision and recall against seeded hallucinations (§10A, T-124 targets).

For 50 real insights from five seeded matches, the suite writes one correct draft from the evidence and
one draft with a single seeded error, rotating through five kinds:

    invented_number   a number that no tool returned               (the rules stage should catch it)
    wrong_team        the right number credited to the other team   (only the judge can catch it)
    wrong_direction   "rose" where the metric fell, or the reverse  (judge)
    wrong_minute      the right fact at the wrong minute            (rules or judge)
    wrong_metric      the right number under the wrong metric       (judge)

Each draft goes through `fact_check` exactly as an agent's answer would, with the insight as the only
fact. Targets (plan T-124): at least 95% of seeded errors caught, fewer than 5% of correct drafts rejected.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from agent_framework import Agent

from pitchlens.agents.clients import chat_client
from pitchlens.agents.factcheck import FACT_CHECKER_INSTRUCTIONS, fact_check
from pitchlens.agents.runtime import AgentRun, supported_numbers
from pitchlens.agents.tools import Ledger, MatchTools, MatchView, ToolCall
from pitchlens.clock import minute_label
from pitchlens.narrate.grounding import unsupported_numbers
from pitchlens.narrate.provider import Tier
from pitchlens.schema.insights import Evidence, Insight

from .common import MATCHES, ProviderUnavailable, Results, match_view

SUITE = "factcheck"
N_INSIGHTS = 50
KINDS = ("invented_number", "wrong_team", "wrong_direction", "wrong_minute", "wrong_metric")
# A plausible wrong metric for each one (same kind of quantity where possible).
SWAP = {
    "ppda": "field tilt", "field_tilt": "possession", "possession": "field tilt", "press_rate": "ppda",
    "win_prob": "possession", "xg": "shots", "momentum": "control index", "control": "momentum",
    "tempo": "control index", "shot_speed_kmh": "sprint speed", "top_speed_kmh": "shot speed",
    "distance_km": "top speed", "pass_difficulty": "pass accuracy", "assist_pass_difficulty": "xg",
}  # fmt: skip


@dataclass
class Case:
    case_id: str
    match: str
    insight_id: str
    insight_type: str
    kind: str  # good | one of KINDS
    question: str
    draft: str
    facts: dict[str, Any]


def label(metric: str) -> str:
    """How a draft names a metric: "shot_speed_kmh" -> "shot speed" (the unit is written after the value)."""
    return re.sub(r"_(kmh|km|m)$", "", metric).replace("_", " ")


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _num(v: float) -> str:
    return f"{v:g}"


def _subject(e: Evidence, view: MatchView) -> str:
    s = view.state
    if e.player and e.player in s.players:
        return f"{_possessive(s.players[e.player].name)} "
    if e.team and e.team in s.teams:
        return f"{_possessive(s.teams[e.team].name)} "
    return ""


def _clauses(insight: Insight, view: MatchView) -> list[tuple[Evidence, str]]:
    """Correct clauses for the insight's numeric evidence (at most two)."""
    out = []
    for e in insight.evidence:
        metric = label(e.metric)
        if e.from_ is not None and e.to is not None and e.from_ != e.to:
            verb = "rose" if e.to > e.from_ else "fell"
            out.append((e, f"{_subject(e, view)}{metric} {verb} from {_num(e.from_)} to {_num(e.to)}"))
        elif isinstance(e.value, (int, float)) and not isinstance(e.value, bool):
            unit = f" {e.unit}" if e.unit else ""
            out.append((e, f"{_subject(e, view)}{metric} was {_num(float(e.value))}{unit}"))
        if len(out) == 2:
            break
    return out


def _applicable(insight: Insight, clauses: list[tuple[Evidence, str]], view: MatchView) -> list[str]:
    kinds = ["invented_number", "wrong_minute"]
    if any(e.team in view.state.teams and not e.player for e, _ in clauses):
        kinds.append("wrong_team")
    if any(" rose " in c or " fell " in c for _, c in clauses):
        kinds.append("wrong_direction")
    if any(e.metric in SWAP for e, _ in clauses):
        kinds.append("wrong_metric")
    return kinds


def _seed(kind: str, draft: str, minute: str, clauses: list[tuple[Evidence, str]], view: MatchView,
          allowed: set[float]) -> str:  # fmt: skip
    s = view.state
    if kind == "invented_number":
        numbers = re.findall(r"\d+(?:\.\d+)?", draft.split(", ", 1)[1])
        target = numbers[-1]
        value = float(target)
        factor = 1.37
        while True:  # a value no tool returned, written with the same precision
            fake = round(
                value * factor + (3 if value < 1 else 0), len(target.split(".")[1]) if "." in target else 0
            )
            text = f"{fake:g}"
            if unsupported_numbers(text, allowed):
                break
            factor += 0.21
        head, tail = draft.rsplit(target, 1)
        return f"{head}{text}{tail}"
    if kind == "wrong_team":
        names = [t.name for t in s.teams.values()]
        for name in names:
            if _possessive(name) in draft:
                other = next(n for n in names if n != name)
                return draft.replace(_possessive(name), _possessive(other), 1)
    if kind == "wrong_direction":
        if " rose " in draft:
            return draft.replace(" rose ", " fell ", 1)
        return draft.replace(" fell ", " rose ", 1)
    if kind == "wrong_minute":
        match = re.match(r"(\d+)", minute)
        base = int(match.group(1)) if match else 45
        wrong = base + 17 if base < 70 else base - 23
        return draft.replace(f"At {minute}", f"At {wrong}'", 1)
    if kind == "wrong_metric":
        for e, _ in clauses:
            if e.metric in SWAP:
                return draft.replace(label(e.metric), SWAP[e.metric], 1)
    raise ValueError(f"{kind} does not apply to {draft!r}")


def build_cases(n_insights: int = N_INSIGHTS) -> list[Case]:
    """Deterministic: the same 50 insights and drafts every run."""
    pools: list[tuple[str, MatchView, list[Insight]]] = []
    for storyline, seed in MATCHES:
        view = match_view(storyline, seed)
        seen: set[str] = set()
        picked = []
        for insight in sorted(view.insights, key=lambda i: -i.salience):
            if insight.headline_key in seen or not _clauses(insight, view):
                continue
            seen.add(insight.headline_key)
            picked.append(insight)
        pools.append((f"{storyline}-{seed}", view, picked))

    cases: list[Case] = []
    counts: Counter[str] = Counter()
    i = 0
    while len(cases) < 2 * n_insights and any(p for _, _, p in pools):
        name, view, picked = pools[i % len(pools)]
        i += 1
        if not picked:
            continue
        insight = picked.pop(0)
        tools = MatchTools(lambda view=view: view)
        row = tools.insight_row(insight)
        minute = minute_label(insight.clock_ms, insight.period)
        clauses = _clauses(insight, view)
        draft = f"At {minute}, " + "; ".join(c for _, c in clauses) + "."
        facts = {"count": 1, "insights": [row]}
        allowed = supported_numbers(_ledger(facts), f"What happened at {minute}?")
        kind = min(_applicable(insight, clauses, view), key=lambda k: (counts[k], KINDS.index(k)))
        counts[kind] += 1
        question = f"What happened at {minute}?"
        base = f"{name}-{insight.insight_id}"
        cases.append(
            Case(f"{base}-good", name, insight.insight_id, insight.type.value, "good", question, draft, facts)
        )
        bad = _seed(kind, draft, minute, clauses, view, allowed)
        cases.append(
            Case(f"{base}-{kind}", name, insight.insight_id, insight.type.value, kind, question, bad, facts)
        )
    return cases


def _ledger(facts: dict[str, Any]) -> Ledger:
    return Ledger([ToolCall("get_insights", {"limit": 1}, facts, 0.0)])


async def run(*, limit: int | None = None, fresh: bool = False, client: Any = None) -> Results:
    results = Results(SUITE, fresh=fresh)
    make = client or (lambda role, tier: chat_client(tier, role=role))
    checker = Agent(make("checker", Tier.FAST), FACT_CHECKER_INSTRUCTIONS, name="fact_checker")
    cases = build_cases()
    todo = [c for c in cases if c.case_id not in results.done][: limit if limit is not None else None]
    for case in todo:
        ledger = _ledger(case.facts)
        agent_run = AgentRun("analyst", case.question, case.draft, ledger, 0.0)
        agent_run.unsupported = unsupported_numbers(case.draft, supported_numbers(ledger, case.question))
        start = time.perf_counter()
        check = await fact_check(checker, case.question, agent_run)
        if check.by == "rules (judge unavailable)":
            # A provider outage (often a free-tier daily cap) is not a verdict: stop, resume later.
            raise ProviderUnavailable(check.problems[0] if check.problems else "judge unavailable")
        results.add({
            "case_id": case.case_id, "match": case.match, "insight_id": case.insight_id,
            "insight_type": case.insight_type, "kind": case.kind, "draft": case.draft,
            "verdict": check.verdict, "by": check.by, "problems": check.problems,
            "flagged": check.verdict != "approve", "ms": round((time.perf_counter() - start) * 1000),
            "tokens": check.tokens,
        })  # fmt: skip
    results.write_meta(cases=len(cases))
    return results


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    good = [r for r in rows if r["kind"] == "good"]
    bad = [r for r in rows if r["kind"] != "good"]
    by_kind = {
        k: (sum(r["flagged"] for r in bad if r["kind"] == k), sum(1 for r in bad if r["kind"] == k))
        for k in KINDS
    }
    caught_by = Counter(r["by"] for r in bad if r["flagged"])
    return {
        "good": len(good),
        "bad": len(bad),
        "recall": (sum(r["flagged"] for r in bad), len(bad)),
        "false_reject": (sum(r["flagged"] for r in good), len(good)),
        "by_kind": by_kind,
        "caught_by": dict(caught_by),
        "judge_unavailable": sum(1 for r in rows if r["by"] == "rules (judge unavailable)"),
        "tokens": sum(int(r.get("tokens") or 0) for r in rows),
        "ms_p50": sorted(r["ms"] for r in rows)[len(rows) // 2] if rows else 0,
    }
