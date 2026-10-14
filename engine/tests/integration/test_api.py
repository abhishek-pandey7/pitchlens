"""End-to-end API tests against a real uvicorn server (SSE needs a real streaming transport)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx

from pitchlens.sim.simulator import simulate


@dataclass
class Sse:
    event: str
    data: dict = field(default_factory=dict)
    id: int | None = None


def sse_events(response: httpx.Response) -> Iterator[Sse]:
    current: dict[str, str] = {}
    for line in response.iter_lines():
        if not line:
            if "event" in current:
                yield Sse(
                    current["event"],
                    json.loads(current.get("data", "{}")),
                    int(current["id"]) if "id" in current else None,
                )
            current = {}
        elif not line.startswith(":") and ":" in line:
            key, _, value = line.partition(":")
            current[key] = value[1:] if value.startswith(" ") else value


def create(base: str, **body: object) -> dict:
    r = httpx.post(f"{base}/matches", json={"tracking": False, **body}, timeout=60)
    assert r.status_code == 201, r.text
    return r.json()


def test_rest_basics(server) -> None:
    base, _ = server
    assert httpx.get(f"{base}/health").json()["status"] == "ok"
    match = create(base, seed=7, storyline="comeback")
    assert match["final_score"] == "2-3"
    assert any(m["match_id"] == match["match_id"] for m in httpx.get(f"{base}/matches").json())
    assert httpx.get(f"{base}/matches/nope").status_code == 404
    no_tracking = httpx.get(f"{base}/matches/{match['match_id']}/clips/goal")
    assert no_tracking.status_code == 404 and "no tracking" in no_tracking.text
    assert httpx.post(f"{base}/matches", json={"storyline": "nope"}).status_code == 422
    assert "comeback" in httpx.get(f"{base}/matches/storylines").json()
    assert httpx.get(f"{base}/schema/insight").json()["type"] == "object"
    profiles = httpx.get(f"{base}/profiles").json()
    assert {"p-casual-es", "p-analyst-en"} <= {p["profile_id"] for p in profiles}
    created = httpx.post(f"{base}/profiles", json={"persona": "tactician", "language": "fr"}).json()
    assert (
        created["language"] == "fr"
        and httpx.get(f"{base}/profiles/{created['profile_id']}").status_code == 200
    )
    created["favorite_clubs"] = ["SVC"]
    assert httpx.put(f"{base}/profiles/{created['profile_id']}", json=created).json()["favorite_clubs"] == [
        "SVC"
    ]


def test_live_stream_with_seek_sync_and_goal_insights(server) -> None:
    base, _ = server
    match = create(base, seed=7, storyline="comeback")
    mid = match["match_id"]
    received: list[Sse] = []
    with httpx.stream(
        "GET", f"{base}/matches/{mid}/stream", params={"profile_id": "p-casual-es"}, timeout=60
    ) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        events = sse_events(r)
        first = next(events)
        assert first.event == "sync"  # every new connection starts with the full picture
        # Start 80 minutes in: the first 80 minutes are applied instantly, then play continues live.
        resp = httpx.post(f"{base}/matches/{mid}/replay", json={"speed": 600, "from_ms": 80 * 60_000})
        assert resp.status_code == 202
        for msg in events:
            received.append(msg)
            if msg.event == "control" and msg.data.get("action") == "finished":
                break
    kinds = [m.event for m in received]
    assert kinds[0] == "reset" and "sync" in kinds
    sync = next(m for m in received if m.event == "sync")
    assert sync.data["state"]["home"]["score"] == 2  # both home goals came during the catch-up
    live_events = [m for m in received if m.event == "event"]
    assert live_events and min(m.data["event"]["match_ms"] for m in live_events) > 80 * 60_000
    goals = [
        m.data["insight"] for m in received if m.event == "insight" and m.data["insight"]["type"] == "goal"
    ]
    assert [g["headline_key"] for g in goals] == ["goal.equaliser", "goal.go_ahead"]
    ids = [m.id for m in received if m.id is not None]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    state = httpx.get(f"{base}/matches/{mid}/state").json()
    assert state["finished"] and (state["home"]["score"], state["away"]["score"]) == (2, 3)
    latency = httpx.get(f"{base}/matches/{mid}/latency").json()["stages"]
    assert latency["event_to_broadcast"]["p95_ms"] < 50


def test_last_event_id_resumes_without_gaps(server) -> None:
    base, _ = server
    mid = create(base, seed=3)["match_id"]
    httpx.post(f"{base}/matches/{mid}/replay", json={"speed": 600, "from_ms": 85 * 60_000})
    seen: list[int] = []
    with httpx.stream("GET", f"{base}/matches/{mid}/stream", timeout=60) as r:
        for msg in sse_events(r):
            if msg.id is not None:
                seen.append(msg.id)
            if len(seen) >= 40:
                break
    last = seen[-1]
    resumed: list[int] = []
    with httpx.stream(
        "GET", f"{base}/matches/{mid}/stream", headers={"Last-Event-ID": str(last)}, timeout=60
    ) as r:
        for msg in sse_events(r):
            assert msg.event != "sync"  # a resumable reconnect replays the gap, not a snapshot
            if msg.id is not None:
                resumed.append(msg.id)
            if len(resumed) >= 20:
                break
    assert resumed[0] == last + 1
    assert resumed == list(range(last + 1, last + 1 + len(resumed)))


def test_sync_snapshot_matches_the_stream(server) -> None:
    """`/sync` returns the very `sync` a new subscriber gets, with the id of the last message it covers."""
    base, _ = server
    mid = create(base, seed=11)["match_id"]
    assert httpx.get(f"{base}/matches/nope/sync").status_code == 404
    assert httpx.get(f"{base}/matches/{mid}/sync", params={"profile_id": "nope"}).status_code == 404
    for params in ({}, {"profile_id": "p-casual-es", "frames": "true", "events": "false"}):
        r = httpx.get(f"{base}/matches/{mid}/sync", params=params)
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
        body = r.json()
        assert set(body) == {"last_id", "sync"} and body["last_id"] == 0  # nothing streamed yet
        assert set(body["sync"]) == {"meta", "profile", "state", "insights"}
        assert (body["sync"]["profile"] is None) == ("profile_id" not in params)
        raw = r.text[len('{"last_id":0,"sync":') : -1]  # the payload exactly as written
        with httpx.stream("GET", f"{base}/matches/{mid}/stream", params=params, timeout=30) as s:
            data = next(line for line in s.iter_lines() if line.startswith("data:"))
        assert data == f"data: {raw}"  # byte for byte what the stream sends a new subscriber


def test_snapshot_id_contract(server) -> None:
    """After a snapshot with id N, joining the live stream and dropping ids <= N (here: resuming from N)
    gives exactly what a direct subscriber sees after N, with no gap and no duplicate, and the snapshot
    holds exactly what the messages up to N carried."""
    base, _ = server
    mid = create(base, seed=7, storyline="comeback")["match_id"]
    params = {"profile_id": "p-casual-es", "events": "true"}
    direct: list[Sse] = []
    live = threading.Event()

    def collect(out: list[Sse], headers: dict[str, str], *, started: threading.Event | None = None) -> None:
        url = f"{base}/matches/{mid}/stream"
        with httpx.stream("GET", url, params=params, headers=headers, timeout=60) as r:
            for msg in sse_events(r):
                out.append(msg)
                if started is not None and msg.event == "sync" and msg.id is not None:
                    started.set()  # the seek's catch-up is over: live play from here
                if msg.event == "control" and msg.data.get("action") == "finished":
                    return

    threads = [threading.Thread(target=collect, args=(direct, {}), kwargs={"started": live})]
    threads[0].start()
    time.sleep(0.3)  # subscribed before the replay starts
    started = httpx.post(f"{base}/matches/{mid}/replay", json={"speed": 300, "from_ms": 60 * 60_000})
    assert started.status_code == 202
    assert live.wait(20)
    snapshots: list[tuple[int, dict, list[Sse]]] = []
    for _ in range(4):
        time.sleep(0.8)
        body = httpx.get(f"{base}/matches/{mid}/sync", params=params).json()
        joined: list[Sse] = []
        snapshots.append((body["last_id"], body["sync"], joined))
        thread = threading.Thread(target=collect, args=(joined, {"Last-Event-ID": str(body["last_id"])}))
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(timeout=60)
    assert direct[-1].event == "control"  # the whole match was seen

    with_ids = [m for m in direct if m.id is not None]
    assert [m.id for m in with_ids] == list(range(with_ids[0].id, with_ids[-1].id + 1))  # no gap, no dup
    states = [m for m in direct if m.event == "state"]
    assert states and all(abs(m.data["sent_at"] - time.time() * 1000) < 120_000 for m in states)
    assert len({n for n, _, _ in snapshots}) > 1  # the snapshots were taken at different points
    for n, snap, joined in snapshots:
        after = [(m.id, m.event, m.data) for m in with_ids if m.id > n]
        assert [(m.id, m.event, m.data) for m in joined] == after
        # What the snapshot covers: the last sync up to N, then every insight and event after it up to N.
        upto = [m for m in with_ids if m.id <= n]
        base_sync = max((i for i, m in enumerate(upto) if m.event == "sync"), default=None)
        assert base_sync is not None
        expected = [i["insight"]["insight_id"] for i in upto[base_sync].data["insights"]]
        expected += [m.data["insight"]["insight_id"] for m in upto[base_sync + 1 :] if m.event == "insight"]
        assert [i["insight"]["insight_id"] for i in snap["insights"]] == expected
        last_event = next((m for m in reversed(upto) if m.event == "event"), None)
        if last_event is not None:
            score = (snap["state"]["home"]["score"], snap["state"]["away"]["score"])
            assert score == (last_event.data["home_score"], last_event.data["away_score"])
            assert snap["state"]["match_ms"] == last_event.data["event"]["match_ms"]


def test_replay_control(server) -> None:
    base, _ = server
    mid = create(base, seed=5)["match_id"]
    assert httpx.post(f"{base}/matches/{mid}/control", json={"action": "pause"}).status_code == 409
    httpx.post(f"{base}/matches/{mid}/replay", json={"speed": 10})
    time.sleep(0.3)
    assert httpx.post(f"{base}/matches/{mid}/control", json={"action": "pause"}).json()["state"] == "paused"
    assert (
        httpx.post(f"{base}/matches/{mid}/control", json={"action": "speed", "speed": 60}).status_code == 200
    )
    assert httpx.post(f"{base}/matches/{mid}/control", json={"action": "resume"}).json()["state"] == "playing"
    assert httpx.post(f"{base}/matches/{mid}/control", json={"action": "bogus"}).status_code == 422


def test_external_feed_ingest(server) -> None:
    base, hub = server
    match = simulate(hub.store.league, "KMA", "ASU", seed=9)
    meta = match.meta.model_copy(update={"match_id": "EXT-KMA-ASU"})
    events = [e.model_copy(update={"match_id": "EXT-KMA-ASU"}) for e in match.events]
    r = httpx.post(f"{base}/matches/external", json={"meta": meta.model_dump(mode="json")})
    assert r.status_code == 201 and r.json()["source"] == "external"
    batch = [e.model_dump(mode="json", exclude_none=True) for e in events[:600]]
    batch.append({"event_id": "bad", "match_id": "EXT-KMA-ASU", "seq": 9999, "type": "teleport"})
    result = httpx.post(f"{base}/ingest", json={"match_id": "EXT-KMA-ASU", "events": batch}).json()
    assert result["accepted"] == 600 and len(result["rejected"]) == 1
    deadline = time.time() + 5
    while time.time() < deadline:
        state = httpx.get(f"{base}/matches/EXT-KMA-ASU/state").json()
        if state["match_ms"] == events[599].match_ms:
            break
        time.sleep(0.05)
    assert state["match_ms"] == events[599].match_ms
    assert httpx.post(f"{base}/ingest", json={"match_id": "nope", "events": []}).status_code == 404


def test_goals_push_state_immediately(server) -> None:
    """Every goal is followed directly by a state message carrying the new score (no throttling)."""
    base, _ = server
    mid = create(base, seed=7, storyline="comeback")["match_id"]
    received: list[Sse] = []
    with httpx.stream("GET", f"{base}/matches/{mid}/stream", params={"events": "true"}, timeout=60) as r:
        events = sse_events(r)
        next(events)  # initial sync
        httpx.post(f"{base}/matches/{mid}/replay", json={"speed": 600, "frames": False})
        for msg in events:
            received.append(msg)
            if msg.event == "control" and msg.data.get("action") == "finished":
                break
    for i, msg in enumerate(received):
        if msg.event == "event" and msg.data["event"]["type"] == "goal":
            after = next(m for m in received[i + 1 :] if m.event == "state")
            score = (after.data["home"]["score"], after.data["away"]["score"])
            assert score == (msg.data["home_score"], msg.data["away_score"])
