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
    assert [s["agent"] for s in body["steps"]][:4] == ["guard", "safety", "analyst", "fact_checker"]

    assert (
        httpx.post(f"{base}/matches/{match_id}/ask", json={"question": "x", "profile_id": "nope"}).status_code
        == 404
    )
    assert httpx.post(f"{base}/matches/nope/ask", json={"question": "x"}).status_code == 404
    assert httpx.post(f"{base}/matches/{match_id}/ask", json={"question": "x" * 400}).status_code == 422

    runs = httpx.get(f"{base}/matches/{match_id}/agents/trace").json()["runs"]
    assert runs[0]["question"] == "¿Quién gana?" and runs[0]["profile_id"] == "p-casual-es"


def test_recap_endpoint(server: tuple[str, Hub]) -> None:
    base, _ = server
    match_id = httpx.post(
        f"{base}/matches", json={"seed": 8, "storyline": "comeback", "tracking": False}, timeout=120
    ).json()["match_id"]

    r = httpx.post(
        f"{base}/matches/{match_id}/recap", json={"stage": "ft", "profile_id": "p-casual-es"}, timeout=120
    )
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "approve" and body["stage"] == "ft" and body["player_of_match"]["player"]
    assert [s["agent"] for s in body["steps"]][:3] == ["editor", "editor", "analyst"]
    again = httpx.post(f"{base}/matches/{match_id}/recap", json={"stage": "ft", "profile_id": "p-casual-es"})
    assert again.json()["cached"] and again.json()["headline"] == body["headline"]
    ht = httpx.post(f"{base}/matches/{match_id}/recap", json={"stage": "ht"}, timeout=120).json()
    assert ht["minute"].startswith("45") and ht["source"] == "editor"

    runs = httpx.get(f"{base}/matches/{match_id}/agents/trace").json()["runs"]
    assert [r["kind"] for r in runs] == ["recap", "recap"]  # cached answers are not logged again

    assert httpx.post(f"{base}/matches/{match_id}/recap", json={"stage": "et"}).status_code == 422
    assert httpx.post(f"{base}/matches/nope/recap", json={"stage": "ft"}).status_code == 404
    httpx.post(f"{base}/matches/{match_id}/replay", json={"speed": 1})
    assert httpx.post(f"{base}/matches/{match_id}/recap", json={"stage": "ht"}).status_code == 409


def test_unsafe_display_names_are_refused(server: tuple[str, Hub]) -> None:
    base, _ = server
    bad = httpx.post(
        f"{base}/profiles", json={"persona": "casual", "language": "en", "display_name": "I will kill him"}
    )
    assert bad.status_code == 422 and "display name" in bad.json()["detail"]
    ok = httpx.post(
        f"{base}/profiles", json={"persona": "casual", "language": "en", "display_name": "Southvale ultra"}
    )
    assert ok.status_code == 201


def test_readiness_probe(server: tuple[str, Hub]) -> None:
    base, _ = server
    ready = httpx.get(f"{base}/health/ready")
    body = ready.json()
    assert ready.status_code == 200 and body["status"] == "ready"
    assert body["checks"]["content_safety"] == "local" and body["checks"]["llm"]["provider"] == "mock"
    assert body["checks"]["agent_budget"]["per_match"] > 0


def test_exports(server: tuple[str, Hub]) -> None:
    base, _ = server
    match_id = httpx.post(
        f"{base}/matches", json={"seed": 4, "storyline": "red_card_turn", "tracking": False}, timeout=120
    ).json()["match_id"]
    chapters = httpx.get(f"{base}/matches/{match_id}/export/chapters", params={"lang": "fr"}, timeout=120)
    assert chapters.status_code == 200 and chapters.headers["content-type"].startswith("text/vtt")
    assert chapters.text.startswith("WEBVTT") and "Coup d'envoi" in chapters.text
    captions = httpx.get(f"{base}/matches/{match_id}/export/captions", params={"profile_id": "p-casual-ar"})
    assert captions.status_code == 200 and "-->" in captions.text
    cues = httpx.get(f"{base}/matches/{match_id}/export/cues", params={"profile_id": "p-casual-es"}).json()
    assert cues["time_base"] == "match_ms" and cues["cues"]
    assert httpx.get(f"{base}/matches/{match_id}/export/everything").status_code == 422
    assert httpx.get(f"{base}/matches/nope/export/chapters").status_code == 404
