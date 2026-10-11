"""The eval suites (T-130) stay runnable: case generation, offline runs, resume, and the report. Offline."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from pitchlens.agents.runtime import supported_numbers
from pitchlens.evals import ask, director, factcheck, report
from pitchlens.evals.common import Results, load, match_view
from pitchlens.narrate.grounding import unsupported_numbers


@pytest.fixture
def results_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("pitchlens.evals.common.RESULTS_DIR", tmp_path)
    original = Results.__init__

    def init(self: Results, suite: str, directory: Path = tmp_path, *, fresh: bool = False) -> None:
        original(self, suite, directory, fresh=fresh)

    monkeypatch.setattr(Results, "__init__", init)
    return tmp_path


def test_factcheck_cases_are_balanced_and_correct_drafts_are_grounded() -> None:
    cases = factcheck.build_cases()
    kinds = Counter(c.kind for c in cases)
    assert kinds["good"] == 50 and sum(kinds.values()) == 100
    assert all(8 <= kinds[k] <= 12 for k in factcheck.KINDS)
    for case in cases:
        allowed = supported_numbers(factcheck._ledger(case.facts), case.question)
        bad_numbers = unsupported_numbers(case.draft, allowed)
        if case.kind in ("good", "wrong_team", "wrong_direction", "wrong_metric"):
            assert not bad_numbers, (case.kind, case.draft)  # only the judge can catch these
        if case.kind == "invented_number":
            assert bad_numbers, case.draft  # the rules stage must catch these
    assert factcheck.build_cases()[7].draft == cases[7].draft  # deterministic


async def test_factcheck_offline_run_resumes_and_scores(results_dir: Path) -> None:
    await factcheck.run(limit=6)
    first, _ = load("factcheck", results_dir)
    assert len(first) == 6
    await factcheck.run(limit=4)  # resumes after the first six
    rows, meta = load("factcheck", results_dir)
    assert len(rows) == 10 and len({r["case_id"] for r in rows}) == 10 and meta["provider"] == "mock"
    s = factcheck.summarise(rows)
    assert s["false_reject"][0] == 0  # the offline judge approves; correct drafts pass the rules
    invented = [r for r in rows if r["kind"] == "invented_number"]
    assert all(r["flagged"] and r["by"] == "rules" for r in invented)


async def test_ask_cases_and_offline_run(results_dir: Path) -> None:
    cases = ask.build_cases(match_view(*ask.MATCH, tracking=True))
    assert len(cases) == 30 and len({c.case_id for c in cases}) == 30
    assert Counter(c.kind for c in cases) == {"fact": 23, "open": 4, "guard": 3}
    final = next(c for c in cases if c.case_id == "final-score")
    assert final.check and final.check("It finished 2-3.") and not final.check("It finished 2-2.")
    speed = next(c for c in cases if c.case_id == "fastest-shot")
    assert speed.check and not speed.check("Someone hit it at 50 km/h.")

    await ask.run(limit=2)
    rows, _ = load("ask", results_dir)
    assert [r["case_id"] for r in rows] == ["final-score", "winner-scorer"]
    assert rows[0]["passed"] and rows[0]["verified"]  # the offline analyst reads get_match_state


async def test_director_offline_pacing_and_report(results_dir: Path, tmp_path: Path) -> None:
    row = await director.programme_run(director.Mode.AUTO, speed=600, min_gap_s=0)
    assert row["aired"] and row["peak_per_min"] <= row["limit_per_min"]
    assert row["urgent_max_delay_ms"] == 0  # goals never wait for the Director
    assert row["turns"] and row["turn_failures"] == 0

    await factcheck.run(limit=4)
    doc = report.write(results_dir, tmp_path / "eval.md")
    text = doc.read_text(encoding="utf-8")
    assert "## Fact-Checker: seeded hallucinations" in text and "No results yet" in text
