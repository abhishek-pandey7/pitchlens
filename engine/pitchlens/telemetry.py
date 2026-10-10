"""Observability (§17, T-42): OpenTelemetry spans per pipeline stage plus an in-process latency recorder.

Spans are no-ops unless an SDK is configured: `configure()` wires Azure Monitor (Application Insights)
when a connection string is present and the `azure` extra is installed. The latency recorder always runs;
it powers the studio's latency HUD and the `/latency` endpoint without any external service.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace

tracer = trace.get_tracer("pitchlens")
log = logging.getLogger("pitchlens")

WINDOW = 2000  # most recent samples kept per stage


class LatencyRecorder:
    """Rolling per-stage latency samples in milliseconds."""

    def __init__(self) -> None:
        self._samples: defaultdict[str, deque[float]] = defaultdict(lambda: deque(maxlen=WINDOW))

    def record(self, stage: str, ms: float) -> None:
        self._samples[stage].append(ms)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        with tracer.start_as_current_span(name):
            yield
        self.record(name, (time.perf_counter() - start) * 1000)

    def summary(self) -> dict[str, dict[str, float]]:
        out = {}
        for name, samples in self._samples.items():
            if not samples:
                continue
            ordered = sorted(samples)
            out[name] = {
                "count": len(ordered),
                "p50_ms": round(ordered[len(ordered) // 2], 3),
                "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
                "max_ms": round(ordered[-1], 3),
            }
        return out


def configure(connection_string: str | None) -> bool:
    """Send traces to Application Insights when configured. Returns True if an exporter was installed."""
    if not connection_string:
        return False
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor  # type: ignore[import-not-found]
    except ImportError:
        log.warning("APPLICATIONINSIGHTS_CONNECTION_STRING set but the 'azure' extra is not installed")
        return False
    configure_azure_monitor(connection_string=connection_string)
    return True
