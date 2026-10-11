"""Write docs/eval.md from the saved results of the three suites."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import ask, factcheck
from .common import RESULTS_DIR, load, pct

DOC = Path(__file__).resolve().parents[3] / "docs" / "eval.md"


def _cell(text: Any, width: int = 110) -> str:
    s = str(text).replace("\n", " ").replace("|", "\\|")
    return s if len(s) <= width else s[: width - 1] + "…"


def _meta(meta: dict[str, Any]) -> str:
    if not meta:
        return "_not run yet_"
    return f"`{meta.get('provider')}` / `{meta.get('model')}`, {meta.get('updated')}"


def factcheck_section(
    rows: list[dict[str, Any]], meta: dict[str, Any], directory: Path = RESULTS_DIR
) -> list[str]:
    out = ["## Fact-Checker: seeded hallucinations", "", f"Run: {_meta(meta)}.", ""]
    if not rows:
        return [*out, "No results yet: `python -m pitchlens.evals factcheck`.", ""]
    s = factcheck.summarise(rows)
    caught, bad = s["recall"]
    rejected, good = s["false_reject"]
    out += [
        f"{good} correct drafts and {bad} drafts with one seeded error, written from {good} real insights "
        "across five seeded matches (one per storyline). Each draft goes through `fact_check` exactly as an "
        "agent's answer would, with the insight as the only fact.",
        "",
        "| Measure | Result | Target (plan T-124) |",
        "|---|---|---|",
        f"| Seeded errors caught (recall) | **{caught}/{bad} ({pct(caught, bad)})** | ≥ 95% |",
        f"| Correct drafts rejected | **{rejected}/{good} ({pct(rejected, good)})** | < 5% |",
        f"| Caught by the rules stage / by the model judge | {s['caught_by'].get('rules', 0)} / "
        f"{s['caught_by'].get('model', 0)} | |",
        f"| Judge unavailable (fell back to rules) | {s['judge_unavailable']} | |",
        f"| Judge tokens, total | {s['tokens']:,} | |",
        "",
        "| Seeded error | Caught |",
        "|---|---|",
    ]
    for kind, (n, d) in s["by_kind"].items():
        out.append(f"| `{kind}` | {n}/{d} ({pct(n, d)}) |")
    first, _ = load("factcheck-run1", directory)
    if first:
        f = factcheck.summarise(first)
        out += [
            "",
            f"**First run, and what it changed.** The first run caught {f['recall'][0]}/{f['recall'][1]} "
            f"seeded errors but rejected {f['false_reject'][0]}/{f['false_reject'][1]} correct drafts, all "
            "for what they left out (\"the draft does not mention the goal at 78'\"). The Fact-Checker's "
            "instructions now say to judge only what a draft claims, never what it omits; that fix applies "
            'to every agent it checks. Its two misses relabelled a shot speed as "top speed", arguably '
            'the same quantity, so that seeded error now says "sprint speed". Both runs\' results are '
            "kept in `engine/evals/results/`.",
        ]
    missed = [r for r in rows if r["kind"] != "good" and not r["flagged"]]
    wrongly = [r for r in rows if r["kind"] == "good" and r["flagged"]]
    if missed:
        out += ["", "**Missed errors:**", ""]
        out += [f"- `{r['kind']}`: {_cell(r['draft'], 200)}" for r in missed[:10]]
    if wrongly:
        out += ["", "**Correct drafts rejected:**", ""]
        out += [f"- {_cell(r['draft'], 160)} ({_cell('; '.join(r['problems']), 160)})" for r in wrongly[:10]]
    return [*out, ""]


def ask_section(rows: list[dict[str, Any]], meta: dict[str, Any]) -> list[str]:
    out = ["## Ask the Match: 30 questions", "", f"Run: {_meta(meta)}.", ""]
    if not rows:
        return [*out, "No results yet: `python -m pitchlens.evals ask`.", ""]
    s = ask.summarise(rows)
    out += [
        f"{s['cases']} of 30 questions on the comeback match (seed 7, with tracking). Expected answers are "
        "computed from the tools at run time. A **fact** case passes when the answer contains the expected "
        "value and the run was verified; an **open** case when it was verified and used a fitting tool; a "
        "**guard** case when the answer does not take the bait.",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Passed | **{s['passed']}/{s['cases']} ({pct(s['passed'], s['cases'])})** |",
    ]
    for kind, (n, d) in sorted(s["by_kind"].items()):
        out.append(f"| · {kind} | {n}/{d} |")
    out += [
        f"| Verified by the Fact-Checker (answerable questions) | {s['verified']}/{s['answerable']} |",
        f"| Called an expected tool | {s['tools_ok']}/{s['with_tools']} |",
        f"| Needed a revision round | {s['revised']} |",
        f'| Honest fallback ("couldn\'t verify") | {s["fallback"]} |',
        f"| Latency p50 / p95 | {s['ms_p50'] / 1000:.1f} s / {s['ms_p95'] / 1000:.1f} s |",
        f"| Tokens, total | {s['tokens']:,} |",
        "",
        "| Case | Kind | Pass | Tools | Answer |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        mark = "✓" if r["passed"] else "✗"
        tools = ", ".join(r["tools"]) or "–"
        out.append(f"| `{r['case_id']}` | {r['kind']} | {mark} | {tools} | {_cell(r['answer'])} |")
    return [*out, ""]


def director_section(rows: list[dict[str, Any]], meta: dict[str, Any]) -> list[str]:
    out = ["## Director: pacing and curation", "", f"Run: {_meta(meta)}.", ""]
    if not rows:
        return [*out, "No results yet: `python -m pitchlens.evals director`.", ""]
    by = {r["mode"]: r for r in rows}
    off, auto = by.get("off", {}), by.get("auto", {})
    any_row = auto or off
    out += [
        f"The second half of the comeback replayed at {any_row.get('speed')}× on the "
        f"`{any_row.get('profile')}` "
        f"programme (limit {any_row.get('limit_per_min')} cues a minute), once with the gate open (the "
        "scheduler's rules alone) and once with the Director in auto mode.",
        "",
        "| Measure | Rules only | Director (auto) |",
        "|---|---|---|",
    ]

    def row(label: str, key: str, fmt: str = "{}") -> None:
        out.append(f"| {label} | {fmt.format(off.get(key, '–'))} | {fmt.format(auto.get(key, '–'))} |")

    row("Cues aired", "aired")
    row("Most cues in any 60 s of match time, goals and red cards aside", "peak_per_min")
    row("Goals and red cards aired", "urgent")
    row("Longest delay on a goal or red card (ms of match time)", "urgent_max_delay_ms")
    row("Back-to-back repeats (same type within 3 min)", "repeats")
    row("Director turns", "turns")
    row("Turn failures", "turn_failures")
    out.append(
        f"| Turn latency p50 / p95 | – | {auto.get('turn_ms_p50', 0) / 1000:.1f} s / "
        f"{auto.get('turn_ms_p95', 0) / 1000:.1f} s |"
    )
    row("Director tokens", "tokens")
    if auto.get("decisions"):
        mix = ", ".join(f"{k} {v}" for k, v in sorted(auto["decisions"].items()))
        out += ["", f"**Decisions (auto):** {mix}."]
    if auto.get("reasons"):
        out += ["", "**Sample Director reasons:** " + "; ".join(f"“{x}”" for x in auto["reasons"][:6]) + "."]
    return [*out, ""]


INTRO = """# Agent evaluation

The agent suites from plan §10A (T-130), generated by `python -m pitchlens.evals report` from the
results in `engine/evals/results/`. Every case is reproducible: matches are seeded, expected answers come
from the deterministic tools, and seeded errors are generated from real insights.

```
python -m pitchlens.evals factcheck   # 100 drafts through the Fact-Checker
python -m pitchlens.evals ask         # 30 fan questions through Ask the Match
python -m pitchlens.evals director    # programme pacing, rules vs Director
python -m pitchlens.evals report      # this file
```

Runs resume where they stopped (free tiers cap requests and tokens per day); `--fresh` starts over. The
provider comes from `LLM_PROVIDER`, as in the app. With no model configured, the suites run on the
offline client, which is how CI keeps them working (`tests/unit/test_evals.py`).
"""


def write(directory: Path = RESULTS_DIR, doc: Path = DOC) -> Path:
    parts = [INTRO]
    parts += factcheck_section(*load("factcheck", directory), directory)
    parts += ask_section(*load("ask", directory))
    parts += director_section(*load("director", directory))
    doc.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    return doc
