"""The Rust win probability (`pitchlens_core.win_probability`) against the Python reference
(`win_probability_py`) over every state of five matches: every call `MatchState` makes while it plays
each match through, plus what-if variants of each (docs/rust-expansion-plan.md §3.4).

The port follows Python's operations one for one; `pow` and `exp` come from the C library on both sides.
On Linux (CI) Rust and CPython call the same glibc functions, so the results are bit-identical. Elsewhere
the C libraries differ: on Windows the GNU toolchain links mingw-w64's `pow`, CPython the UCRT's, and the
two differ by up to a few units in the last place, so there the check is a measured bound instead.

Skipped when the extension isn't built (or is an older build without `win_probability`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

pitchlens_core = pytest.importorskip("pitchlens_core")
if not hasattr(pitchlens_core, "win_probability"):
    pytest.skip("pitchlens_core has no win_probability (rebuild the core)", allow_module_level=True)

from pitchlens.interpret import state as state_module  # noqa: E402
from pitchlens.interpret.state import build_state  # noqa: E402
from pitchlens.interpret.win_prob import win_probability_py  # noqa: E402
from pitchlens.live.store import load_match_dir  # noqa: E402
from pitchlens.sim.rosters import generate_league  # noqa: E402
from pitchlens.sim.simulator import simulate  # noqa: E402
from pitchlens.sim.storylines import build, default_fixture  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[3] / "data" / "samples"
# The same operations in the same order, but not always bit for bit: the C libraries' `pow`/`exp` can
# round differently in the last bit (mingw-w64 against the UCRT on Windows: up to 2.2e-16), and the Rust
# compiler simplifies some calls after unrolling the 0..10 grid (Linux CI: up to 1.1e-16). The bound leaves
# room for other inputs while still catching any real divergence, which would be orders of magnitude larger.
BOUND = 1e-14


def match_calls() -> list[dict[str, Any]]:
    """Every win-probability call made while five matches play through, with what-if variants."""
    calls: list[dict[str, Any]] = []

    def record(home_goals: int, away_goals: int, **kw: Any) -> Any:
        calls.append({"home_goals": home_goals, "away_goals": away_goals, **kw})
        return win_probability_py(home_goals, away_goals, **kw)

    matches = []
    for match_id in ("SYN-NHR-SVC-1000", "SYN-SGV-ELR-1003"):
        record_ = load_match_dir(SAMPLES / match_id)
        if record_ is not None:
            matches.append((record_.meta, record_.events))
    league = generate_league()
    for storyline, seed in (("comeback", 7), ("red_card_turn", 3), ("chaos_derby", 11)):
        home, away = default_fixture(league, storyline)
        m = simulate(league, home, away, seed=seed, storyline=build(league, storyline, home, away))
        matches.append((m.meta, m.events))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(state_module, "win_probability", record)
        for meta, events in matches:
            build_state(meta, events)
    variants = []
    for c in calls[:: max(1, len(calls) // 3000)]:
        variants.append({**c, "home_goals": c["home_goals"] + 1})
        variants.append({**c, "home_reds": c.get("home_reds", 0) + 1, "away_reds": c.get("away_reds", 0) + 2})
    assert len(matches) >= 5
    return calls + variants


def test_rust_win_probability_matches_python_over_five_matches() -> None:
    calls = match_calls()
    assert len(calls) > 10_000
    diffs = []
    for c in calls:
        py = win_probability_py(
            c["home_goals"], c["away_goals"], **{k: v for k, v in c.items() if "goals" not in k}
        )
        rs = pitchlens_core.win_probability(
            c["home_goals"],
            c["away_goals"],
            c["home_rate_90"],
            c["away_rate_90"],
            c["minutes_left"],
            c.get("home_reds", 0),
            c.get("away_reds", 0),
        )
        diffs.append(np.abs(np.array(rs) - np.array([py.home, py.draw, py.away])).max())
    worst = float(max(diffs))
    assert worst <= BOUND, f"Rust and Python differ by up to {worst:g} (bound {BOUND:g})"
