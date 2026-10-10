"""Football clock formatting shared by the CLI, narration and overlays."""

from __future__ import annotations


def minute_label(clock_ms: int, period: int) -> str:
    """Broadcast-style minute: 1'..45', 45+2', 46'..90', 90+4' (minute n covers (n-1):00 to n:00)."""
    minute = clock_ms // 60_000
    regular = 45 * period
    return f"{regular}+{minute - regular + 1}'" if minute >= regular else f"{minute + 1}'"
