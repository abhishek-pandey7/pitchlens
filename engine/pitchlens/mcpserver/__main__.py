"""Run the PitchLens MCP server over stdio for a local MCP client (VS Code / GitHub Copilot, Claude).

python -m pitchlens.mcpserver            # serves the matches in PITCHLENS_DATA_DIRS (default: data/)
"""

from __future__ import annotations

from pitchlens.config import Settings
from pitchlens.live.hub import Hub
from pitchlens.live.store import MatchStore

from .server import build_server


def main() -> None:
    settings = Settings.from_env()
    hub = Hub(MatchStore(settings.data_dirs), narrate=False)
    build_server(lambda: hub).run("stdio")


if __name__ == "__main__":
    main()
