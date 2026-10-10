"""Command line for the simulator.

python -m pitchlens.sim.cli generate --seed 42 --storyline comeback
python -m pitchlens.sim.cli summary data/matches/SYN-NHR-SVC-42
python -m pitchlens.sim.cli replay data/matches/SYN-NHR-SVC-42 --speed 60
python -m pitchlens.sim.cli dataset --count 50            # full dataset -> data/matches/
python -m pitchlens.sim.cli dataset --samples              # committed samples -> data/samples/
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from pitchlens.clock import minute_label
from pitchlens.ingest.bus import MessageKind
from pitchlens.ingest.memory_bus import MemoryBus
from pitchlens.schema.events import EventType, MatchEvent, MatchMeta

from .dataset import build_dataset
from .replay import Replay
from .rosters import DEFAULT_PATH as ROSTER_PATH
from .rosters import load_league
from .simulator import SimulatedMatch, simulate
from .storylines import STORYLINES, build, default_fixture
from .tracking import Tracking, generate_tracking

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
DEFAULT_OUT = DATA_DIR / "matches"
SAMPLES_OUT = DATA_DIR / "samples"


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


TICKER = {
    EventType.GOAL,
    EventType.CARD,
    EventType.SUBSTITUTION,
    EventType.PERIOD_START,
    EventType.PERIOD_END,
}


def clock_label(event: MatchEvent) -> str:
    return minute_label(event.clock_ms, event.period)


async def ticker(match_dir: Path, speed: float) -> None:
    """Replay a match through the in-memory bus and print key moments as they 'happen'."""
    meta, events = read_match(match_dir)
    bus = MemoryBus()
    replay = Replay(bus, meta, events, speed=speed)
    async with bus.subscribe(meta.match_id) as sub:
        task = asyncio.create_task(replay.run())
        async for message in sub:
            if message.kind is MessageKind.META:
                print(f"{meta.home.name} vs {meta.away.name} at {meta.venue}, x{speed:g}")
            elif message.event is not None and message.event.type in TICKER:
                e = message.event
                who = f"{e.team_id or '':<4} {e.player_id or ''}"
                print(f"{clock_label(e):>7}  {e.type:<14} {who} {' '.join(e.qualifiers)}")
            elif message.kind is MessageKind.CONTROL and message.control == {"action": "finished"}:
                break
        await task


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
    rep_ = sub.add_parser("replay", help="replay a match in (accelerated) real time")
    rep_.add_argument("match_dir", type=Path)
    rep_.add_argument("--speed", type=float, default=60.0)
    ds = sub.add_parser("dataset", help="build the reproducible synthetic dataset")
    ds.add_argument("--count", type=int, default=50)
    ds.add_argument("--out", type=Path, default=None)
    ds.add_argument("--no-tracking", action="store_true")
    ds.add_argument("--samples", action="store_true", help="the 5 storyline matches, gzipped, no tracking")
    ds.add_argument("--roster", type=Path, default=ROSTER_PATH)
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
    elif args.cmd == "dataset":
        league = load_league(args.roster)
        if args.samples:
            manifest = build_dataset(league, args.out or SAMPLES_OUT, count=5, tracking=False, gzipped=True)
        else:
            manifest = build_dataset(
                league, args.out or DEFAULT_OUT, count=args.count, tracking=not args.no_tracking
            )
        for key, row in manifest["summary_organic_matches"].items():
            ref = row["reference"]
            print(f"{key:16s} mean {row['mean']:>9}  range {row['min']}-{row['max']}  reference {ref or '-'}")
        print(f"{manifest['count']} matches written")
    elif args.cmd == "replay":
        asyncio.run(ticker(args.match_dir, args.speed))
    else:
        print(summarize(*read_match(args.match_dir)))


if __name__ == "__main__":
    main()
