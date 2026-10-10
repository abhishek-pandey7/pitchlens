"""The end-to-end smoke test (scripts/e2e_smoke.py) as part of the suite, against an in-thread server."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "e2e_smoke.py"


def load_smoke():
    spec = importlib.util.spec_from_file_location("e2e_smoke", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def test_whole_system_start_to_finish(server) -> None:
    base, _ = server
    report = load_smoke().run_smoke(base)
    report.print()
    failed = [(name, detail) for ok, name, detail in report.rows if not ok]
    assert not failed, failed
