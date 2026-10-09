"""Command line for the simulator.

python -m pitchlens.sim.cli generate --seed 42 --storyline comeback
python -m pitchlens.sim.cli summary data/matches/SYN-NHR-SVC-42
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from pitchlens.schema.events import EventType, MatchEvent, MatchMeta

from .rosters import DEFAULT_PATH as ROSTER_PATH
from .rosters import load_league
from .simulator import SimulatedMatch, simulate
from .storylines import STORYLINES, build, default_fixture
from .tracking import Tracking, generate_tracking

DEFAULT_OUT = Path(__file__).resolve().parents[3] / "data" / "matches"


def write_match(match: SimulatedMatch, out_dir: Path) -> Path:
    match_dir = out_dir / match.meta.match_id
    match_dir.mkdir(parents=True, exist_ok=True)
    (match_dir / "meta.json").write_text(
        match.meta.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    with (match_dir / "events.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for event in match.events:
            fh.write(event.model_dump_json(exclude_none=True) + "\n")
    return match_dir


def read_tracking(match_dir: Path) -> Tracking:
    return Tracking.load(match_dir / "tracking.npz")


def read_match(match_dir: Path) -> tuple[MatchMeta, list[MatchEvent]]:
    meta = MatchMeta.model_validate(json.loads((match_dir / "meta.json").read_text(encoding="utf-8")))
    with (match_dir / "events.jsonl").open(encoding="utf-8") as fh:
        events = [MatchEvent.model_validate_json(line) for line in fh if line.strip()]
    return meta, events


def summarize(meta: MatchMeta, events: list[MatchEvent]) -> str:
    counts = Counter(e.type for e in events)
    passes = [e for e in events if e.type is EventType.PASS]
    completed = sum(1 for e in passes if e.outcome == "complete")
    goals = [e for e in events if e.type is EventType.GOAL]
    lines = [
        f"{meta.match_id}  {meta.home.name} vs {meta.away.name}  "
        f"(storyline: {meta.storyline}, seed {meta.seed})",
        f"events: {len(events)}  passes: {len(passes)}  completion: {completed / max(len(passes), 1):.0%}  "
        f"shots: {counts[EventType.SHOT]}  fouls: {counts[EventType.FOUL]}  cards: {counts[EventType.CARD]}",
        f"duration: {events[-1].match_ms / 60000:.1f} min of play",
        "goals: "
        + (
            ", ".join(
                f"{e.clock_ms // 60000 + 1}' {e.team_id} {e.player_id} ({e.qualifiers[0].split(':')[1]})"
                for e in goals
            )
            or "none"
        ),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pitchlens-sim")
    sub = parser.add_subparsers(dest="cmd", required=True)
    gen = sub.add_parser("generate", help="simulate one match")
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--storyline", choices=sorted(STORYLINES), default=None)
    gen.add_argument("--home")
    gen.add_argument("--away")
    gen.add_argument("--out", type=Path, default=DEFAULT_OUT)
    gen.add_argument("--roster", type=Path, default=ROSTER_PATH)
    gen.add_argument("--no-tracking", action="store_true", help="skip the 5 Hz tracking file")
    summ = sub.add_parser("summary", help="summarize a generated match")
    summ.add_argument("match_dir", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "generate":
        league = load_league(args.roster)
        home, away = default_fixture(league, args.storyline or "")
        home, away = args.home or home, args.away or away
        spec = build(league, args.storyline, home, away) if args.storyline else None
        match = simulate(league, home, away, seed=args.seed, storyline=spec)
        match_dir = write_match(match, args.out)
        if not args.no_tracking:
            generate_tracking(match.meta, match.events, league).save(match_dir / "tracking.npz")
        print(summarize(match.meta, match.events))
        print(f"wrote {match_dir}")
    else:
        print(summarize(*read_match(args.match_dir)))


if __name__ == "__main__":
    main()
