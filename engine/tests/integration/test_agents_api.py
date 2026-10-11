"""Ask the Match over HTTP: the real server, offline agents (no credentials in CI)."""

from __future__ import annotations

import httpx

from pitchlens.live.hub import Hub


def test_ask_endpoint_and_trace(server: tuple[str, Hub]) -> None:
    base, _ = server
    match_id = httpx.post(
        f"{base}/matches", json={"seed": 7, "storyline": "comeback", "tracking": False}, timeout=120
    ).json()["match_id"]

    r = httpx.post(
        f"{base}/matches/{match_id}/ask",
        json={"question": "¿Quién gana?", "profile_id": "p-casual-es"},
        timeout=60,
    )
    assert r.status_code == 200
    body = r.json()
    assert "2-3" in body["answer"] and body["verdict"] == "approve"
    assert [s["agent"] for s in body["steps"]][:3] == ["guard", "analyst", "fact_checker"]

    assert (
        httpx.post(f"{base}/matches/{match_id}/ask", json={"question": "x", "profile_id": "nope"}).status_code
        == 404
    )
    assert httpx.post(f"{base}/matches/nope/ask", json={"question": "x"}).status_code == 404
    assert httpx.post(f"{base}/matches/{match_id}/ask", json={"question": "x" * 400}).status_code == 422

    runs = httpx.get(f"{base}/matches/{match_id}/agents/trace").json()["runs"]
    assert runs[0]["question"] == "¿Quién gana?" and runs[0]["profile_id"] == "p-casual-es"
