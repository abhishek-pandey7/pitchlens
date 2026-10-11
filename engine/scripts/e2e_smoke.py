"""End-to-end smoke test: the whole PitchLens system, start to finish, driven like real clients.

    python scripts/e2e_smoke.py                  # boots its own server on a free port
    python scripts/e2e_smoke.py --base-url URL   # tests a running deployment (…/api/v1)

simulate -> replay -> state -> explain -> personalise -> narrate -> cues -> SSE, for several fans at once,
plus seek, Last-Event-ID resume and spoiler-safe checks. Prints a report; exits 1 if any check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx

SPEED = 600
FANS = ["p-casual-es", "p-analyst-en", "p-tactician-en"]
STREAM_TIMEOUT_S = 120


@dataclass
class Sse:
    event: str
    data: Any
    id: int | None = None


@dataclass
class Stream:
    """An SSE client running in a background thread, collecting every message."""

    base: str
    match_id: str
    profile_id: str | None = None
    frames: bool = False
    last_event_id: int | None = None
    stop_after: int | None = None
    messages: list[Sse] = field(default_factory=list)
    connected: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    error: str | None = None

    def start(self) -> Stream:
        threading.Thread(target=self._run, daemon=True).start()
        if not self.connected.wait(10):
            self.error = "did not connect"
        return self

    def _run(self) -> None:
        params: dict[str, Any] = {"frames": str(self.frames).lower()}
        if self.profile_id:
            params["profile_id"] = self.profile_id
        headers = {"Last-Event-ID": str(self.last_event_id)} if self.last_event_id is not None else {}
        try:
            with httpx.stream(
                "GET",
                f"{self.base}/matches/{self.match_id}/stream",
                params=params,
                headers=headers,
                timeout=httpx.Timeout(STREAM_TIMEOUT_S, connect=10),
            ) as r:
                r.raise_for_status()
                self.connected.set()
                current: dict[str, str] = {}
                for line in r.iter_lines():
                    if line:
                        if not line.startswith(":") and ":" in line:
                            key, _, value = line.partition(":")
                            current[key] = value[1:] if value.startswith(" ") else value
                        continue
                    if "event" in current:
                        msg = Sse(
                            current["event"],
                            json.loads(current.get("data") or "null"),
                            int(current["id"]) if "id" in current else None,
                        )
                        self.messages.append(msg)
                        if msg.event == "control" and (msg.data or {}).get("action") == "finished":
                            return
                        if self.stop_after and len([m for m in self.messages if m.id]) >= self.stop_after:
                            return
                    current = {}
        except Exception as exc:
            self.error = repr(exc)
        finally:
            self.connected.set()
            self.finished.set()

    def of(self, event: str) -> list[Sse]:
        return [m for m in self.messages if m.event == event]


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, str]] = []

    def check(self, ok: bool, name: str, detail: Any = "") -> bool:
        self.rows.append((bool(ok), name, str(detail)))
        return bool(ok)

    @property
    def passed(self) -> bool:
        return all(ok for ok, _, _ in self.rows)

    def print(self) -> None:
        width = max(len(n) for _, n, _ in self.rows)
        for ok, name, detail in self.rows:
            print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
        failed = sum(1 for ok, _, _ in self.rows if not ok)
        print(f"\n{len(self.rows) - failed}/{len(self.rows)} checks passed")


def insights_of(stream: Stream, kind: str | None = None) -> list[dict[str, Any]]:
    items = [m.data for m in stream.of("insight")]
    return [i for i in items if kind is None or i["insight"]["type"] == kind]


def max_noncritical_cues_per_minute(cues: list[dict[str, Any]]) -> int:
    times = sorted(c["start_ms"] for c in cues if c["priority"] < 90)
    return max((sum(1 for t in times if s <= t < s + 60_000) for s in times), default=0)


def run_smoke(base: str) -> Report:
    report = Report()
    client = httpx.Client(base_url=base, timeout=120)
    health = client.get("/health").json()
    report.check(health.get("status") == "ok", "health", health)

    # ---------------------------------------------------------------- simulate
    created = client.post("/matches", json={"seed": 7, "storyline": "comeback", "tracking": True}).json()
    mid = created["match_id"]
    report.check(
        created["final_score"] == "2-3" and created["has_tracking"],
        "simulate comeback",
        created["final_score"],
    )
    total_events = created["events"]

    # spoiler-safe fan created through the API
    spoiler = client.post(
        "/profiles", json={"persona": "casual", "language": "en", "spoiler_safe": True}
    ).json()

    # ---------------------------------------------------------------- live replay, 5 concurrent clients
    streams = {"raw": Stream(base, mid, frames=True).start()}
    for fan in [*FANS, spoiler["profile_id"]]:
        streams[fan] = Stream(base, mid, profile_id=fan).start()
    started = time.perf_counter()
    report.check(
        client.post(f"/matches/{mid}/replay", json={"speed": SPEED}).status_code == 202, "start replay"
    )
    for s in streams.values():
        s.finished.wait(STREAM_TIMEOUT_S)
    wall = time.perf_counter() - started
    report.check(
        all(s.error is None for s in streams.values()),
        "streams healthy",
        {k: s.error for k, s in streams.items() if s.error} or f"{wall:.1f}s at {SPEED}x",
    )

    raw = streams["raw"]
    report.check(
        len(raw.of("event")) == total_events,
        "raw: every event streamed",
        f"{len(raw.of('event'))}/{total_events}",
    )
    report.check(
        len(raw.of("frame")) > 100 and len(raw.of("alert")) > 10,
        "raw: tracking frames and physical alerts",
        f"{len(raw.of('frame'))} frames, {len(raw.of('alert'))} alerts",
    )
    raw_goals = [g["insight"]["headline_key"] for g in insights_of(raw, "goal")]
    report.check(
        raw_goals
        == ["goal.go_ahead", "goal.extends_lead", "goal.pulls_back", "goal.equaliser", "goal.go_ahead"],
        "raw: five goal insights in order",
        raw_goals,
    )
    for name, s in streams.items():
        ids = [m.id for m in s.messages if m.id is not None]
        report.check(ids == sorted(set(ids)) and ids, f"{name}: ids strictly increasing", f"{len(ids)} ids")

    # ---------------------------------------------------------------- personalisation
    counts = {fan: len(insights_of(streams[fan])) for fan in FANS}
    report.check(
        counts["p-casual-es"] < counts["p-tactician-en"] <= counts["p-analyst-en"] <= len(insights_of(raw)),
        "feeds shrink with persona thresholds",
        counts,
    )
    for fan in FANS:
        goals = insights_of(streams[fan], "goal")
        report.check(len(goals) == 5, f"{fan}: all five goals delivered", len(goals))
    casual_goals = insights_of(streams["p-casual-es"], "goal")
    southvale = [g for g in casual_goals if g["insight"]["entities"]["teams"] == ["SVC"]]
    report.check(
        all("favorite_club" in g["reasons"] for g in southvale) and len(southvale) == 3,
        "casual-es: Southvale goals boosted as favourite club",
        len(southvale),
    )
    report.check(
        all(g["narration"]["text"].startswith("¡GOOOL!") for g in casual_goals),
        "casual-es: Spanish template text on goals",
        casual_goals[-1]["narration"]["text"],
    )
    tactician_press = insights_of(streams["p-tactician-en"], "pressure_change")
    casual_press = insights_of(streams["p-casual-es"], "pressure_change")
    report.check(
        len(tactician_press) > len(casual_press),
        "tactician sees more pressing insights than casual",
        f"{len(tactician_press)} vs {len(casual_press)}",
    )
    analyst_goal = insights_of(streams["p-analyst-en"], "goal")[-1]["narration"]
    report.check(
        "xG" in analyst_goal["body"], "analyst: evidence detail in goal text", analyst_goal["body"][:70]
    )
    spoiler_goals = insights_of(streams[spoiler["profile_id"]], "goal")
    report.check(
        spoiler_goals and all(g["spoiler"] for g in spoiler_goals),
        "spoiler-safe fan: goals flagged",
        len(spoiler_goals),
    )

    # ---------------------------------------------------------------- narration (LLM path, mock provider)
    for fan in FANS:
        narrations = [m.data for m in streams[fan].of("narration")]
        grounded = all(n["grounding"]["passed"] for n in narrations)
        langs = {n["lang"] for n in narrations}
        expected_lang = fan.rsplit("-", 1)[-1]
        delivered = len(insights_of(streams[fan]))
        report.check(
            narrations and grounded and langs == {expected_lang} and len(narrations) >= 0.9 * delivered,
            f"{fan}: grounded LLM narrations for its feed",
            f"{len(narrations)}/{delivered} insights narrated, langs {sorted(langs)}",
        )

    # ---------------------------------------------------------------- overlay cues
    for fan in FANS:
        cues = [m.data for m in streams[fan].of("cue")]
        profile = client.get(f"/profiles/{fan}").json()
        goal_cues = [c for c in cues if c["priority"] == 100]
        peak = max_noncritical_cues_per_minute(cues)
        report.check(
            len(goal_cues) == 5 and peak <= profile["max_overlays_per_min"],
            f"{fan}: goal cues on air, rate limit respected",
            f"{len(cues)} cues, {len(goal_cues)} goals, peak {peak}/min "
            f"(limit {profile['max_overlays_per_min']})",
        )
    regions = Counter(m.data["region"] for fan in FANS for m in streams[fan].of("cue"))
    report.check(len(regions) >= 3, "cues use several screen regions", dict(regions))

    # ---------------------------------------------------------------- resume with Last-Event-ID
    fan = "p-analyst-en"
    first = Stream(base, mid, profile_id=fan, stop_after=1).start()
    first.finished.wait(10)
    log_ids = [m.id for m in streams[fan].messages if m.id is not None]
    resume_from = log_ids[len(log_ids) // 2]
    resumed = Stream(base, mid, profile_id=fan, last_event_id=resume_from, stop_after=25).start()
    resumed.finished.wait(10)
    got = [m.id for m in resumed.messages if m.id is not None]
    report.check(
        got == list(range(resume_from + 1, resume_from + 1 + len(got))) and len(got) == 25,
        "resume: exact gap replay per fan channel",
        f"from {resume_from}: {got[:3]}…",
    )

    # ---------------------------------------------------------------- seek
    seeker = Stream(base, mid, profile_id="p-casual-es").start()
    client.post(f"/matches/{mid}/replay", json={"speed": SPEED, "frames": False})
    time.sleep(0.5)
    client.post(f"/matches/{mid}/control", json={"action": "seek", "to_ms": 80 * 60_000})
    seeker.finished.wait(STREAM_TIMEOUT_S)
    syncs = [m.data for m in seeker.of("sync")]
    after_seek = syncs[-1] if syncs else {}
    seek_goals = [i for i in after_seek.get("insights", []) if i["insight"]["type"] == "goal"]
    seek_score = (
        after_seek.get("state", {}).get("home", {}).get("score"),
        after_seek.get("state", {}).get("away", {}).get("score"),
    )
    report.check(
        after_seek and seek_score == (2, 1) and len(seek_goals) == 3,
        "seek: sync rebuilds the fan's feed at 80' (2-1)",
        f"score {seek_score}, {len(seek_goals)} goals in feed",
    )
    live_goals = [i["insight"]["headline_key"] for i in insights_of(seeker, "goal")]
    report.check(
        live_goals[-2:] == ["goal.equaliser", "goal.go_ahead"],
        "seek: remaining goals arrive live",
        live_goals[-2:],
    )

    # ---------------------------------------------------------------- latency
    stages = client.get(f"/matches/{mid}/latency").json()["stages"]
    e2b = stages.get("event_to_broadcast", {})
    report.check(
        e2b.get("p95_ms", 1e9) < 50, "latency: event -> broadcast p95 < 50 ms", f"{e2b.get('p95_ms')} ms"
    )
    report.check(
        "personalize" in stages and "narration" in stages,
        "latency: personalise and narration stages recorded",
        {k: v["p95_ms"] for k, v in stages.items()},
    )

    # ---------------------------------------------------------------- agents (offline unless a model is set)
    ask = client.post(
        f"/matches/{mid}/ask", json={"question": "Who won?", "profile_id": "p-analyst-en"}, timeout=600
    ).json()
    report.check(
        ask.get("verdict") == "approve" and "2-3" in ask.get("answer", ""),
        "agents: Ask the Match gives a verified answer",
        ask.get("answer", ask)[:120],
    )
    recap = client.post(
        f"/matches/{mid}/recap", json={"stage": "ft", "profile_id": "p-casual-es"}, timeout=900
    ).json()
    report.check(
        recap.get("verdict") == "approve" and bool(recap.get("player_of_match")),
        "agents: Editor group chat writes an approved full-time recap",
        f"{recap.get('source')} / {recap.get('lang')}: {recap.get('headline')}",
    )
    kinds = [r["kind"] for r in client.get(f"/matches/{mid}/agents/trace").json()["runs"]]
    report.check(kinds[:2] == ["recap", "ask"], "agents: both runs in the trace", kinds)
    return report


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", help="test an already running API (…/api/v1) instead of booting one")
    args = parser.parse_args()
    server = None
    base = args.base_url
    if base is None:
        port = free_port()
        env = {**os.environ, "PITCHLENS_DATA_DIRS": tempfile.mkdtemp(prefix="pitchlens-e2e-")}
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "pitchlens.api.main:app",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            env=env,
        )
        base = f"http://127.0.0.1:{port}/api/v1"
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                httpx.get(f"{base}/health", timeout=1)
                break
            except httpx.TransportError:
                time.sleep(0.3)
    try:
        print(f"PitchLens end-to-end smoke test against {base}\n")
        report = run_smoke(base)
        report.print()
        return 0 if report.passed else 1
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
