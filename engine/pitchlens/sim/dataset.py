"""Synthetic dataset builder (§7, T-15).

`build_dataset` deterministically generates N matches: one per storyline first, then organic fixtures
drawn round-robin from the league. It writes each match (meta, events, optional tracking), a manifest
with seeds and SHA-256 checksums, and aggregate statistics compared with real-football reference ranges.
Running it again with the same arguments reproduces the dataset byte for byte.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pitchlens.interpret import shots as shot_model
from pitchlens.schema.events import EventType, MatchEvent, MatchMeta, Outcome

from .rosters import League
from .simulator import SimulatedMatch, simulate
from .storylines import STORYLINES, build, default_fixture
from .tracking import generate_tracking

DATASET_VERSION = "1.0"
BASE_SEED = 1000

# Approximate per-match ranges for top-flight football, used as a sanity reference (both teams combined).
REFERENCE_RANGES: dict[str, tuple[float, float]] = {
    "passes": (850, 1150),
    "pass_completion": (0.75, 0.86),
    "shots": (22, 28),
    "goals": (2.4, 3.1),
    "xg": (2.3, 3.2),
    "fouls": (18, 27),
    "cards": (3, 5),
    "corners": (8, 12),
    "throw_ins": (35, 50),
    "offsides": (2, 5),
}


@dataclass(frozen=True)
class Fixture:
    index: int
    home: str
    away: str
    seed: int
    storyline: str | None


def plan_fixtures(league: League, count: int) -> list[Fixture]:
    fixtures: list[Fixture] = []
    for i, name in enumerate(sorted(STORYLINES)):
        if len(fixtures) == count:
            break
        home, away = default_fixture(league, name)
        fixtures.append(Fixture(i, home, away, BASE_SEED + i, name))
    clubs = [c.club_id for c in league.clubs]
    pairs = [(h, a) for k in range(1, len(clubs)) for h, a in zip(clubs, clubs[k:] + clubs[:k], strict=True)]
    i = len(fixtures)
    while len(fixtures) < count:
        home, away = pairs[(i * 7) % len(pairs)]
        fixtures.append(Fixture(i, home, away, BASE_SEED + i, None))
        i += 1
    return fixtures


def match_stats(meta: MatchMeta, events: list[MatchEvent]) -> dict[str, float]:
    counts = Counter(e.type for e in events)
    passes = [e for e in events if e.type is EventType.PASS]
    xg = 0.0
    for e in events:
        if e.type is EventType.SHOT and e.start:
            attacks_pos = (e.team_id == meta.home.club_id) == (e.period == 1)
            x, y = (e.start.x, e.start.y) if attacks_pos else (105 - e.start.x, 68 - e.start.y)
            xg += shot_model.xg(
                shot_model.shot_features(
                    x,
                    y,
                    header=e.body_part == "head",
                    under_pressure=e.under_pressure,
                    one_on_one="one_on_one" in e.qualifiers,
                )
            )
    return {
        "events": len(events),
        "passes": len(passes),
        "pass_completion": round(sum(e.outcome is Outcome.COMPLETE for e in passes) / max(len(passes), 1), 4),
        "shots": counts[EventType.SHOT],
        "goals": counts[EventType.GOAL],
        "xg": round(xg, 3),
        "fouls": counts[EventType.FOUL],
        "cards": counts[EventType.CARD],
        "corners": counts[EventType.CORNER],
        "throw_ins": counts[EventType.THROW_IN],
        "offsides": counts[EventType.OFFSIDE],
        "possessions": counts[EventType.POSSESSION_CHANGE],
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_events(path: Path, events: list[MatchEvent], *, gzipped: bool) -> Path:
    payload = "".join(e.model_dump_json(exclude_none=True) + "\n" for e in events).encode("utf-8")
    if gzipped:
        path = path.with_suffix(path.suffix + ".gz")
        # mtime=0 keeps the gzip header (and so the checksum) reproducible.
        with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as fh:
            fh.write(payload)
    else:
        path.write_bytes(payload)
    return path


def write_dataset_match(
    match: SimulatedMatch, league: League, out_dir: Path, *, tracking: bool, gzipped: bool
) -> dict[str, str]:
    match_dir = out_dir / match.meta.match_id
    match_dir.mkdir(parents=True, exist_ok=True)
    meta_path = match_dir / "meta.json"
    meta_path.write_text(match.meta.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    files = {
        "meta": meta_path,
        "events": _write_events(match_dir / "events.jsonl", match.events, gzipped=gzipped),
    }
    if tracking:
        files["tracking"] = generate_tracking(match.meta, match.events, league).save(
            match_dir / "tracking.npz"
        )
    return {kind: f"{p.relative_to(out_dir).as_posix()}  sha256:{_sha256(p)}" for kind, p in files.items()}


def build_dataset(
    league: League, out_dir: Path, *, count: int = 50, tracking: bool = True, gzipped: bool = False
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    matches = []
    per_match: list[dict[str, float]] = []
    for fx in plan_fixtures(league, count):
        spec = build(league, fx.storyline, fx.home, fx.away) if fx.storyline else None
        match = simulate(league, fx.home, fx.away, seed=fx.seed, storyline=spec)
        stats = match_stats(match.meta, match.events)
        per_match.append(stats)
        goals = [e for e in match.events if e.type is EventType.GOAL]
        home_goals = sum(1 for g in goals if g.team_id == fx.home)
        matches.append(
            {
                "match_id": match.meta.match_id,
                "home": fx.home,
                "away": fx.away,
                "seed": fx.seed,
                "storyline": fx.storyline or "organic",
                "score": f"{home_goals}-{len(goals) - home_goals}",
                "stats": stats,
                "files": write_dataset_match(match, league, out_dir, tracking=tracking, gzipped=gzipped),
            }
        )
    organic = [s for s, m in zip(per_match, matches, strict=True) if m["storyline"] == "organic"] or per_match
    summary = {
        key: {
            "mean": round(statistics.mean(s[key] for s in organic), 3),
            "min": min(s[key] for s in organic),
            "max": max(s[key] for s in organic),
            "reference": REFERENCE_RANGES.get(key),
        }
        for key in organic[0]
    }
    manifest = {
        "dataset_version": DATASET_VERSION,
        "roster_version": league.roster_version,
        "roster_seed": league.seed,
        "count": len(matches),
        "summary_organic_matches": summary,
        "matches": matches,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest
