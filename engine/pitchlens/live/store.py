"""Where matches come from: stored datasets on disk, freshly simulated matches, or external feeds."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path

from pitchlens.schema.events import MatchEvent, MatchMeta
from pitchlens.sim.rosters import League, load_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture
from pitchlens.sim.tracking import Tracking, generate_tracking


@dataclass
class MatchRecord:
    meta: MatchMeta
    events: list[MatchEvent] = field(default_factory=list)
    tracking: Tracking | None = None
    source: str = "stored"  # stored | generated | external

    @property
    def match_id(self) -> str:
        return self.meta.match_id

    def summary(self) -> dict[str, object]:
        goals = [e for e in self.events if e.type == "goal"]
        home = sum(1 for g in goals if g.team_id == self.meta.home.club_id)
        return {
            "match_id": self.match_id,
            "home": {"club_id": self.meta.home.club_id, "name": self.meta.home.name},
            "away": {"club_id": self.meta.away.club_id, "name": self.meta.away.name},
            "storyline": self.meta.storyline,
            "seed": self.meta.seed,
            "source": self.source,
            "events": len(self.events),
            "has_tracking": self.tracking is not None,
            "final_score": f"{home}-{len(goals) - home}" if self.events else None,
        }


def read_events(path: Path) -> list[MatchEvent]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:  # type: ignore[operator]
        return [MatchEvent.model_validate_json(line) for line in fh if line.strip()]


def load_match_dir(match_dir: Path) -> MatchRecord | None:
    meta_path = match_dir / "meta.json"
    events_path = next(
        (p for p in (match_dir / "events.jsonl", match_dir / "events.jsonl.gz") if p.exists()), None
    )
    if not meta_path.exists() or events_path is None:
        return None
    meta = MatchMeta.model_validate(json.loads(meta_path.read_text(encoding="utf-8")))
    tracking_path = match_dir / "tracking.npz"
    tracking = Tracking.load(tracking_path) if tracking_path.exists() else None
    return MatchRecord(meta=meta, events=read_events(events_path), tracking=tracking)


class MatchStore:
    def __init__(self, data_dirs: list[Path], league: League | None = None) -> None:
        self.data_dirs = data_dirs
        self._league = league
        self._records: dict[str, MatchRecord] = {}
        self._scanned = False

    @property
    def league(self) -> League:
        if self._league is None:
            self._league = load_league()
        return self._league

    def _scan(self) -> None:
        if self._scanned:
            return
        self._scanned = True
        for root in self.data_dirs:
            if not root.is_dir():
                continue
            for match_dir in sorted(p for p in root.iterdir() if p.is_dir()):
                if match_dir.name not in self._records and (record := load_match_dir(match_dir)):
                    self._records[record.match_id] = record

    def list(self) -> list[MatchRecord]:
        self._scan()
        return list(self._records.values())

    def get(self, match_id: str) -> MatchRecord | None:
        self._scan()
        return self._records.get(match_id)

    def add(self, record: MatchRecord) -> MatchRecord:
        self._records[record.match_id] = record
        return record

    def generate(
        self,
        *,
        seed: int,
        storyline: str | None = None,
        home: str | None = None,
        away: str | None = None,
        tracking: bool = True,
    ) -> MatchRecord:
        league = self.league
        default_home, default_away = default_fixture(league, storyline or "")
        home, away = home or default_home, away or default_away
        spec = build(league, storyline, home, away) if storyline else None
        match = simulate(league, home, away, seed=seed, storyline=spec)
        trk = generate_tracking(match.meta, match.events, league) if tracking else None
        return self.add(MatchRecord(meta=match.meta, events=match.events, tracking=trk, source="generated"))
