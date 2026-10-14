"""Which implementation runs the hot paths: the Rust core (`pitchlens_core`) or the Python reference.

`PITCHLENS_CORE` chooses: `auto` (default) uses Rust when the extension is installed, `rust` requires
it, `python` never uses it. Build the extension with `python scripts/build_core.py` (see
docs/rust-core-plan.md). Every Rust function has a Python twin, so the engine runs either way.
"""

from __future__ import annotations

import os
from types import ModuleType

MODES = ("auto", "rust", "python")


def _load() -> tuple[ModuleType | None, str]:
    mode = os.environ.get("PITCHLENS_CORE", "auto").strip().lower() or "auto"
    if mode not in MODES:
        raise RuntimeError(f"PITCHLENS_CORE must be one of {MODES}, not {mode!r}")
    if mode == "python":
        return None, mode
    try:
        import pitchlens_core  # type: ignore[import-not-found]
    except ImportError as exc:
        if mode == "rust":
            raise RuntimeError(
                "PITCHLENS_CORE=rust but the pitchlens_core extension is not installed; "
                "build it with `python scripts/build_core.py`"
            ) from exc
        return None, mode
    return pitchlens_core, mode


native, MODE = _load()
RUST = native is not None


def backend() -> dict[str, str | None]:
    """For /health: which core is serving and why."""
    return {
        "backend": "rust" if RUST else "python",
        "mode": MODE,
        "version": native.version() if native is not None else None,
    }
