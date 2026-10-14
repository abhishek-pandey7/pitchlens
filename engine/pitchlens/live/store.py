"""Where matches come from: stored datasets on disk, freshly simulated matches, or external feeds."""

from __future__ import annotations

import gzip
import json
import logging
import os
import threading
from collections import defaultdict
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from pitchlens import core
from pitchlens.schema.events import MatchEvent, MatchMeta
from pitchlens.sim.rosters import League, load_league
from pitchlens.sim.simulator import simulate
from pitchlens.sim.storylines import build, default_fixture
from pitchlens.sim.tracking import Tracking, generate_tracking

log = logging.getLogger(__name__)
# Rebuilt tracking for stored matches is cached here (gitignored), so each match is built once.
TRACKING_CACHE = Path(
    os.environ.get("PITCHLENS_TRACKING_CACHE")
    or Path(__file__).resolve().parents[3] / "data" / ".cache" / "tracking"
)


@dataclass
class MatchRecord:
    meta: MatchMeta
    events: list[MatchEvent] = field(default_factory=list)
    tracking: Tracking | None = None
    source: str = "stored"  # stored | generated | external
    path: Path | None = None  # the stored match's directory

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
    return MatchRecord(meta=meta, events=read_events(events_path), tracking=tracking, path=match_dir)


def cache_path(match_id: str, n_events: int, cache: Path | None = None) -> Path:
    return (cache or TRACKING_CACHE) / f"{match_id}-{n_events}.npz"


def build_tracking_cache(match_dir: str, cache: str) -> str | None:
    """Worker-process entry point: rebuild a stored match's tracking and save it to the cache. It runs in
    its own process, so a build (tens of seconds of pure Python) never slows the engine's event loop."""
    record = load_match_dir(Path(match_dir))
    if record is None:
        return None
    try:
        tracking = generate_tracking(record.meta, record.events, load_league())
    except (StopIteration, KeyError, ValueError):  # clubs or players outside this league
        return None
    target = cache_path(record.match_id, len(record.events), Path(cache))
    target.parent.mkdir(parents=True, exist_ok=True)
    tracking.save(target)
    return str(target)


class MatchStore:
    def __init__(self, data_dirs: list[Path], league: League | None = None) -> None:
        self.data_dirs = data_dirs
        self._league = league
        self._records: dict[str, MatchRecord] = {}
        self._scanned = False
        self._tracking_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._builder: ProcessPoolExecutor | None = None
        self._building: dict[str, Future[str | None]] = {}

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

    # ------------------------------------------------------------------ tracking for stored matches
    # The committed samples carry events only, to keep the repository small. Their 5 Hz tracking is
    # rebuilt deterministically from the events and the league's rosters, once, and cached on disk.

    def load_cached_tracking(self, record: MatchRecord) -> bool:
        """Tracking from memory or the disk cache (fast). Never builds. External feeds never use the
        cache: it holds tracking rebuilt for our own simulator's matches."""
        if record.tracking is not None:
            return True
        if record.source == "external":
            return False
        cached = cache_path(record.match_id, len(record.events))
        with self._tracking_locks[record.match_id]:
            if record.tracking is None and cached.exists():
                try:
                    record.tracking = Tracking.load(cached)
                except (OSError, ValueError, KeyError):
                    cached.unlink(missing_ok=True)
        return record.tracking is not None

    def request_tracking(self, record: MatchRecord) -> bool:
        """Ask the background builder for a stored match's tracking (non-blocking). True if queued."""
        if record.tracking is not None or record.source != "stored" or record.path is None:
            return False
        if cache_path(record.match_id, len(record.events)).exists():
            return False
        building = self._building.get(record.match_id)
        if building is not None and not building.done():
            return False
        if self._builder is None:
            self._builder = ProcessPoolExecutor(max_workers=1)  # one build at a time, off the event loop
        self._building[record.match_id] = self._builder.submit(
            build_tracking_cache, str(record.path), str(TRACKING_CACHE)
        )
        return True

    def tracking_ready(self, record: MatchRecord) -> bool:
        """Players and ball for this match now, if that is cheap; True when `record.tracking` is set.

        With the Rust core a build takes tens of milliseconds, so it happens inline on first use (faster
        than reading the disk cache). With the Python fallback a build takes seconds: the disk cache is
        used if present, otherwise a background build is queued for a later request."""
        if record.tracking is not None:
            return True
        if record.source == "external":
            return False
        if core.RUST:
            return self.ensure_tracking(record)
        if self.load_cached_tracking(record):
            return True
        self.request_tracking(record)
        return False

    def ensure_tracking(self, record: MatchRecord) -> bool:
        """Load or build tracking now, in this process (blocking). The Rust core builds without the disk
        cache, which only pays off for the slow Python build."""
        if record.tracking is not None:
            return True
        if record.source == "external" or not record.events:
            return False
        if not core.RUST and self.load_cached_tracking(record):
            return True
        with self._tracking_locks[record.match_id]:
            if record.tracking is not None:
                return True
            try:
                record.tracking = generate_tracking(record.meta, record.events, self.league)
            except (StopIteration, KeyError, ValueError):  # clubs or players outside this league
                return False
            if core.RUST:
                return True
            try:
                target = cache_path(record.match_id, len(record.events))
                target.parent.mkdir(parents=True, exist_ok=True)
                record.tracking.save(target)
            except OSError as exc:  # a read-only disk only costs the cache
                log.warning("could not cache tracking for %s: %s", record.match_id, exc)
            return True

    def warm_tracking(self) -> int:
        """Queue background builds for every stored match not yet cached (call at startup). Nothing to do
        with the Rust core, which builds on first use."""
        if core.RUST:
            return 0
        return sum(self.request_tracking(r) for r in self.list() if not self.load_cached_tracking(r))

    def close(self) -> None:
        if self._builder is not None:
            self._builder.shutdown(wait=False, cancel_futures=True)
            self._builder = None

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
