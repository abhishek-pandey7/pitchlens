"""Shared fixtures for integration tests: a real uvicorn server in a background thread."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn

from pitchlens.api.main import API_PREFIX, create_app
from pitchlens.config import Settings
from pitchlens.live.hub import Hub
from pitchlens.live.store import MatchStore


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server() -> Iterator[tuple[str, Hub]]:
    store = MatchStore(data_dirs=[])
    hub = Hub(store)
    app = create_app(Settings(data_dirs=[]), hub=hub)
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    assert srv.started, "server did not start"
    yield f"http://127.0.0.1:{port}{API_PREFIX}", hub
    srv.should_exit = True
    thread.join(timeout=10)
