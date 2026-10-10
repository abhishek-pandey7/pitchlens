"""Runtime configuration from environment variables (§16). Defaults run fully offline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    bus: str = "memory"
    store: str = "memory"
    llm_provider: str = "mock"
    data_dirs: list[Path] = field(
        # Full dataset first (it has tracking), committed samples as the fallback.
        default_factory=lambda: [REPO_ROOT / "data" / "matches", REPO_ROOT / "data" / "samples"]
    )
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
    default_speed: float = 10.0
    appinsights_connection_string: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        dirs = _list(env.get("PITCHLENS_DATA_DIRS", ""))
        return cls(
            bus=env.get("BUS", "memory"),
            store=env.get("STORE", "memory"),
            llm_provider=env.get("LLM_PROVIDER", "mock"),
            data_dirs=[Path(d) for d in dirs] if dirs else cls().data_dirs,
            cors_origins=_list(env.get("CORS_ORIGINS", "http://localhost:3000")),
            default_speed=float(env.get("PITCHLENS_DEFAULT_SPEED", "10")),
            appinsights_connection_string=env.get("APPLICATIONINSIGHTS_CONNECTION_STRING") or None,
        )
