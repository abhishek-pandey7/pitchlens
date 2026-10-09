"""Export JSON Schemas for every public model so the web app (and partners) share one contract.

Usage: python -m pitchlens.schema.export_jsonschema [out_dir]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from .events import MatchEvent, MatchMeta, TrackingFrame
from .insights import Insight
from .overlays import OverlayCue
from .profiles import Narration, Profile

PUBLIC_MODELS: dict[str, type[BaseModel]] = {
    "match_meta": MatchMeta,
    "match_event": MatchEvent,
    "tracking_frame": TrackingFrame,
    "insight": Insight,
    "overlay_cue": OverlayCue,
    "profile": Profile,
    "narration": Narration,
}

DEFAULT_OUT = Path(__file__).resolve().parents[3] / "web" / "lib" / "schemas"


def export(out_dir: Path = DEFAULT_OUT) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in PUBLIC_MODELS.items():
        path = out_dir / f"{name}.schema.json"
        schema = model.model_json_schema(by_alias=True, mode="serialization")
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")
        written.append(path)
    return written


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    for path in export(out):
        print(path)


if __name__ == "__main__":
    main()
