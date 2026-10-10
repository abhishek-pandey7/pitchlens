import gzip
import hashlib
import json
from pathlib import Path

from pitchlens.schema.events import MatchEvent, MatchMeta
from pitchlens.sim.dataset import build_dataset, plan_fixtures
from pitchlens.sim.rosters import generate_league
from pitchlens.sim.storylines import STORYLINES

LEAGUE = generate_league()
SAMPLES = Path(__file__).resolve().parents[3] / "data" / "samples"


def test_fixture_plan_starts_with_every_storyline_and_never_self_plays() -> None:
    fixtures = plan_fixtures(LEAGUE, 30)
    assert [f.storyline for f in fixtures[: len(STORYLINES)]] == sorted(STORYLINES)
    assert all(f.home != f.away for f in fixtures)
    assert len({f.seed for f in fixtures}) == 30


def test_build_is_byte_for_byte_reproducible(tmp_path: Path) -> None:
    a = build_dataset(LEAGUE, tmp_path / "a", count=6, tracking=False, gzipped=True)
    b = build_dataset(LEAGUE, tmp_path / "b", count=6, tracking=False, gzipped=True)
    assert [m["files"] for m in a["matches"]] == [m["files"] for m in b["matches"]]
    assert a["matches"][-1]["storyline"] == "organic"


def test_committed_samples_match_their_manifest() -> None:
    """data/samples is generated; rebuild with `python -m pitchlens.sim.cli dataset --samples`."""
    manifest = json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["count"] == len(STORYLINES)
    for match in manifest["matches"]:
        for entry in match["files"].values():
            rel, digest = entry.split("  sha256:")
            assert hashlib.sha256((SAMPLES / rel).read_bytes()).hexdigest() == digest, rel
        match_dir = SAMPLES / match["match_id"]
        meta = MatchMeta.model_validate_json((match_dir / "meta.json").read_text(encoding="utf-8"))
        with gzip.open(match_dir / "events.jsonl.gz", "rt", encoding="utf-8") as fh:
            events = [MatchEvent.model_validate_json(line) for line in fh]
        assert meta.storyline == match["storyline"] and len(events) == match["stats"]["events"]


def test_committed_samples_are_current(tmp_path: Path) -> None:
    """Fails if the simulator changed but data/samples was not regenerated."""
    manifest = json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))
    fresh = build_dataset(LEAGUE, tmp_path, count=5, tracking=False, gzipped=True)
    assert [m["files"] for m in fresh["matches"]] == [m["files"] for m in manifest["matches"]]
