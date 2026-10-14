"""The core switch: Rust when installed (or required), the Python reference otherwise."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from pitchlens import core


def reload_with(monkeypatch: pytest.MonkeyPatch, mode: str):
    monkeypatch.setenv("PITCHLENS_CORE", mode)
    return importlib.reload(core)


def test_python_mode_never_loads_rust(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = reload_with(monkeypatch, "python")
    assert not mod.RUST and mod.backend() == {"backend": "python", "mode": "python", "version": None}
    reload_with(monkeypatch, "auto")


def test_unknown_mode_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="PITCHLENS_CORE must be one of"):
        reload_with(monkeypatch, "fortran")
    reload_with(monkeypatch, "auto")


def test_generate_tracking_falls_back_to_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Python reference runs when Rust is off, or when the installed core predates build_tracking."""
    from pitchlens.sim import tracking

    sentinel = object()
    monkeypatch.setattr(tracking, "generate_tracking_py", lambda meta, events, league: sentinel)
    monkeypatch.setattr(core, "RUST", False)
    assert tracking.generate_tracking(None, [], None) is sentinel  # type: ignore[arg-type]
    monkeypatch.setattr(core, "RUST", True)
    monkeypatch.setattr(core, "native", SimpleNamespace(version=lambda: "0.0.0"))
    assert tracking.generate_tracking(None, [], None) is sentinel  # type: ignore[arg-type]


def test_auto_uses_rust_when_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = reload_with(monkeypatch, "auto")
    try:
        import pitchlens_core  # noqa: F401
    except ImportError:
        assert mod.backend()["backend"] == "python"
    else:
        assert mod.RUST and mod.backend()["version"]
