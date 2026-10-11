"""Shared pieces for the eval suites: seeded matches, resumable result files, run metadata."""

from __future__ import annotations

import functools
import json
import os
import platform
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pitchlens.agents.tools import MatchView
from pitchlens.explain.engine import explain_match
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture
from pitchlens.sim.tracking import generate_tracking

RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "results"
# Seeded matches the suites draw from: one per storyline, so every detector and insight type shows up.
MATCHES = [
    ("comeback", 7),
    ("red_card_turn", 4),
    ("pressure_surge", 5),
    ("star_player", 3),
    ("chaos_derby", 9),
]


@functools.cache
def match_view(storyline: str, seed: int, tracking: bool = False) -> MatchView:
    """A full seeded match, analysed by the same code path as the live pipeline (cached per process)."""
    league = generate_league()
    home, away = default_fixture(league, storyline)
    match = simulate(league, home, away, seed=seed, storyline=build(league, storyline, home, away))
    frames: list[Any] = []
    if tracking:
        trk = generate_tracking(match.meta, match.events, league)
        frames = [trk.frame(i) for i in range(len(trk))]
    return MatchView(*explain_match(match.meta, match.events, frames))


def provider_info() -> dict[str, Any]:
    name = os.environ.get("LLM_PROVIDER", "mock")
    model = {
        "groq": os.environ.get("GROQ_FAST_MODEL") or "openai/gpt-oss-120b",
        "azure_openai": os.environ.get("AZURE_OPENAI_FAST_DEPLOYMENT", ""),
        "openai_compat": os.environ.get("OPENAI_COMPAT_FAST_MODEL", ""),
    }.get(name, "offline")
    if name == "groq" and not os.environ.get("GROQ_API_KEY"):
        name, model = "mock", "offline"
    return {"provider": name, "model": model, "python": platform.python_version()}


class ProviderUnavailable(RuntimeError):
    """The model could not be reached (rate limit, daily cap, outage): stop and resume the run later,
    rather than record a provider failure as an agent's verdict."""


PROVIDER_ERRORS = ("rate limit", "rate_limit", "429", "budget", "quota", "too many requests", "timed out")


def provider_error(text: str | None) -> bool:
    return bool(text) and any(marker in text.lower() for marker in PROVIDER_ERRORS)  # type: ignore[union-attr]


class Results:
    """One suite's results as JSON lines, one per case, so an interrupted run resumes where it stopped
    (free tiers cap requests and tokens per day)."""

    def __init__(self, suite: str, directory: Path = RESULTS_DIR, *, fresh: bool = False) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{suite}.jsonl"
        self.meta_path = directory / f"{suite}.meta.json"
        if fresh:
            self.path.unlink(missing_ok=True)
            self.meta_path.unlink(missing_ok=True)
        self.done = {row["case_id"]: row for row in self.rows()}

    def rows(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return iter(())
        return (json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line)

    def add(self, row: dict[str, Any]) -> None:
        self.done[row["case_id"]] = row
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def write_meta(self, **extra: Any) -> None:
        meta = {**provider_info(), "updated": time.strftime("%Y-%m-%d %H:%M"), **extra}
        self.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load(suite: str, directory: Path = RESULTS_DIR) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path, meta = directory / f"{suite}.jsonl", directory / f"{suite}.meta.json"
    rows = (
        [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x] if path.exists() else []
    )
    latest = {row["case_id"]: row for row in rows}  # a resumed run may repeat a case: the last one counts
    return list(latest.values()), json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "n/a"
