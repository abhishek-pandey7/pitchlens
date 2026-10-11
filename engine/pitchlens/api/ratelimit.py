"""Rate limiting on public endpoints (T-77): a sliding one-minute window per client and endpoint group.

A pure ASGI middleware: it only looks at the method and path, so streams (SSE) and reads pass straight
through. Groups and their default limits per client per minute (0 turns a group off):

    agents   POST …/ask, …/recap              RATE_LIMIT_AGENTS   20   (model calls cost money)
    ingest   POST …/ingest                    RATE_LIMIT_INGEST   600  (partner event feeds)
    mcp      POST /mcp                        RATE_LIMIT_MCP      120
    writes   any other POST / PUT / DELETE    RATE_LIMIT_WRITES   120

Behind a proxy (Azure Container Apps), set TRUST_PROXY_HEADERS=1 to key clients by X-Forwarded-For.
Limits are per replica; the cost caps in the agent desk bound spending overall.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

Scope = MutableMapping[str, Any]
ASGIApp = Callable[[Scope, Callable[[], Awaitable[Any]], Callable[[Any], Awaitable[None]]], Awaitable[None]]
WINDOW_S = 60.0


@dataclass(frozen=True)
class Rule:
    name: str
    methods: frozenset[str]
    path: re.Pattern[str]
    per_minute: int


def default_rules(env: MutableMapping[str, str] | None = None) -> list[Rule]:
    env = env if env is not None else os.environ
    writes = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def limit(name: str, default: int) -> int:
        return int(env.get(name, str(default)))

    post = frozenset({"POST"})
    return [  # first match wins
        Rule("agents", post, re.compile(r"/matches/[^/]+/(ask|recap)$"), limit("RATE_LIMIT_AGENTS", 20)),
        Rule("ingest", post, re.compile(r"/ingest$"), limit("RATE_LIMIT_INGEST", 600)),
        Rule("mcp", post, re.compile(r"^/mcp"), limit("RATE_LIMIT_MCP", 120)),
        Rule("writes", writes, re.compile(r".*"), limit("RATE_LIMIT_WRITES", 120)),
    ]


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        rules: list[Rule] | None = None,
        *,
        trust_proxy_headers: bool | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.rules = rules if rules is not None else default_rules()
        if trust_proxy_headers is None:
            trust_proxy_headers = os.environ.get("TRUST_PROXY_HEADERS", "") in ("1", "true", "yes")
        self.trust_proxy_headers = trust_proxy_headers
        self.clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._last_sweep = clock()

    def _rule(self, method: str, path: str) -> Rule | None:
        return next((r for r in self.rules if method in r.methods and r.path.search(path)), None)

    def _client(self, scope: Scope) -> str:
        if self.trust_proxy_headers:
            for name, value in scope.get("headers") or []:
                if name == b"x-forwarded-for":
                    return value.decode("latin-1").split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _sweep(self, now: float) -> None:
        """Forget idle clients so the table does not grow without bound."""
        if now - self._last_sweep < WINDOW_S:
            return
        self._last_sweep = now
        for key in [k for k, q in self._hits.items() if not q or now - q[-1] >= WINDOW_S]:
            del self._hits[key]

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rule = self._rule(scope["method"], scope["path"])
        if rule is None or rule.per_minute <= 0:
            await self.app(scope, receive, send)
            return
        now = self.clock()
        self._sweep(now)
        hits = self._hits.setdefault((rule.name, self._client(scope)), deque())
        while hits and now - hits[0] >= WINDOW_S:
            hits.popleft()
        if len(hits) >= rule.per_minute:
            retry = max(1, int(WINDOW_S - (now - hits[0])) + 1)
            body = json.dumps(
                {"detail": f"Too many requests: at most {rule.per_minute} a minute here. Try again shortly."}
            ).encode()
            await send({
                "type": "http.response.start", "status": 429,
                "headers": [(b"content-type", b"application/json"), (b"retry-after", str(retry).encode())],
            })  # fmt: skip
            await send({"type": "http.response.body", "body": body})
            return
        hits.append(now)
        await self.app(scope, receive, send)
