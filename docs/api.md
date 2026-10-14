# PitchLens API

FastAPI service, base path `/api/v1`. Interactive docs are at `/docs` when the server is running.

```bash
cd engine && .venv/Scripts/activate   # or source .venv/bin/activate
uvicorn pitchlens.api.main:app --reload --port 8000
```

## Typical flow

```bash
B=http://localhost:8000/api/v1
curl -X POST $B/matches -H 'content-type: application/json' -d '{"seed":7,"storyline":"comeback"}'
curl -N "$B/matches/SYN-NHR-SVC-7/stream?profile_id=p-casual-es&frames=true" &   # live SSE
curl -X POST $B/matches/SYN-NHR-SVC-7/replay -H 'content-type: application/json' -d '{"speed":10}'
curl -X POST $B/matches/SYN-NHR-SVC-7/control -H 'content-type: application/json' -d '{"action":"seek","to_ms":3600000}'
```

## Endpoints

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness: status, version, bus, LLM provider |
| `GET /health/ready` | Readiness: `200` when the configured model provider has what it needs, else `503`; reports loaded matches, content-safety mode (`azure` or `local`) and the agent token budget |
| `GET /matches` | Stored, generated and external matches, with replay state |
| `GET /matches/storylines` | Available scripted storylines |
| `POST /matches` | Simulate a match: `{seed, storyline?, home?, away?, tracking=true}` |
| `GET /matches/{id}` | Metadata (teams, lineups, pre-match strengths) and summary |
| `GET /matches/{id}/tracking-input` | The match packed for the tracking build (event columns as codes, the two sides, players' top speeds; points `[x, y]` or `null`). About 150 KB against about 5 MB of frames: the browser builds the whole match's 5 Hz positions from it with the Rust kernel compiled to WebAssembly (`web/lib/kernel.ts`). 404 for external feeds |
| `GET /matches/{id}/clips/goal` | Tracking frames around a goal, for animations that are not live (the home page's 3D replay). Query: `index` (0 the first goal, -1 the last), `before_ms` (default 10000), `after_ms` (default 4000), 60 s at most. 404 while a stored sample's tracking is still being rebuilt |
| `POST /matches/{id}/replay` | Start or restart: `{speed (≤600), from_ms, frames}` |
| `POST /matches/{id}/control` | `{action: pause \| resume \| seek \| speed, to_ms?, speed?}` |
| `GET /matches/{id}/stream` | **SSE** live stream (below). Query: `profile_id`, `frames` |
| `GET /matches/{id}/state` | Current score, clock, team stats and dynamics readings |
| `GET /matches/{id}/insights` | Insights so far. Query: `since_ms`, `min_salience` |
| `GET /matches/{id}/series` | 30 s time series (win probability, xG, momentum, tilt, PPDA, press rate, control, tempo) |
| `GET /matches/{id}/players/{player_id}` | Player card: event stats and physical metrics |
| `GET /matches/{id}/players/{pid}/focus?profile_id=` | **Player-focus card**: involvement, attack, defence and running numbers, a 6×4 touch map in the player's attacking direction, and their moments in the fan's language |
| `GET /matches/{id}/latency` | Per-stage latency p50 / p95 / max (studio HUD) |
| `POST /matches/external` | Register an externally fed match: `{meta}` |
| `POST /ingest` | Push `events.v1` batches into an external match. Each event is validated independently, and bad events are returned in `rejected` |
| `GET/POST /profiles`, `GET/PUT /profiles/{id}` | Fan profiles (demo presets included) |
| `GET /schema/{name}` | JSON Schema of a public model |
| `POST /matches/{id}/ask` | **Ask the Match**: `{question, profile_id}` → a verified answer in the fan's language, with every agent step (see [agents.md](agents.md)) |
| `POST /matches/{id}/recap` | **Editor recap**: `{stage: "ht"\|"ft", profile_id}` → `{headline, body, player_line, player_of_match, lang, source, verdict, steps, cached}`. Written by the Editor's group chat and adapted to the fan. 409 before the whistle (see [agents.md](agents.md)) |
| `GET /matches/{id}/director` | The directed programme: `mode`, `profile_id`, `pending` cues (with the Director's proposals), `on_air`, `log` |
| `PUT /matches/{id}/director` | `{mode: off\|auto\|approve, profile_id}`: make that fan channel the programme and set the Director's mode |
| `POST /matches/{id}/director/cues/{cue_id}` | The producer's call: `{action: air\|hold\|drop\|approve}` (approve takes the Director's proposal). Airing still follows the scheduler's rules |
| `GET /matches/{id}/export/chapters?lang=` | **WebVTT chapters**: kick-off, goals, red cards, half-time, the biggest other moments, full time; titles in `lang` |
| `GET /matches/{id}/export/captions?profile_id=&lang=` | **WebVTT captions**: the narration that fan's feed shows, in their language (or `lang`) |
| `GET /matches/{id}/export/cues?profile_id=` | **Overlay cues** (JSON) for the whole match, scheduled under the live rules. Timestamps in all three are match time (`match_ms`); a live match exports what has happened so far |
| `GET /matches/{id}/agents/trace` | Recent agent runs for the match, newest first (`kind`: ask, tactical_note, recap) |

**MCP:** the engine also serves the PitchLens MCP server at `/mcp` (outside `/api/v1`; streamable HTTP). It exposes the analyst tools to GitHub Copilot, Claude or any MCP client. See [agents.md](agents.md).

## Live stream (SSE)

| Event | When | Data |
|---|---|---|
| `sync` | first message of every new connection, and after a seek completes | `{meta, state, insights}` |
| `reset` | a replay starts or seeks | `{reason, to_ms?}` |
| `event` | every match event | `EnrichedEvent` (event + metrics + score + possession) |
| `insight` | when a detector fires | `Insight` |
| `alert` | physical thresholds | `PhysicalAlert` |
| `state` | about 2 Hz | state snapshot including `dynamics` |
| `frame` | about 25 Hz, only with `frames=true` | `TrackingFrame` |
| `control` | replay finished, etc. | `{action}` |

Every message except `frame` has an `id`. Reconnect with `Last-Event-ID: <id>` to receive exactly the messages you missed (the server keeps the last 5,000). If the gap is older than that, you get a fresh `sync`.

During a seek, the skipped stretch of the match is applied instantly but not broadcast: clients get `reset`, then one `sync` with the caught-up state, then live messages.

## Latency

Measured during a 300× replay of the comeback storyline on a laptop:

| Stage | p50 | p95 |
|---|---|---|
| Interpret | 0.10 ms | 0.28 ms |
| Explain | 0.03 ms | 0.07 ms |
| Broadcast | 0.07 ms | 0.20 ms |
| **Event → broadcast** | **0.21 ms** | **0.59 ms** |

The §17 budget for event → overlay cue is 300 ms. Stages are also OpenTelemetry spans; set `APPLICATIONINSIGHTS_CONNECTION_STRING` (with the `azure` extra installed) to export them to Application Insights.

## Limits and safety

- **Rate limits** per client and minute: `POST …/ask` and `…/recap` 20, other writes 120, `/ingest` 600,
  `/mcp` 120. Over a limit the API answers `429` with `Retry-After`. Reads and streams are not limited.
- **Agent budget:** when the per-match or daily token cap is reached, `/ask` and `/recap` answer `503`
  ("the agents' budget … is used up"); the live pipeline is unaffected.
- **Content safety:** a blocked question gets `200` with `source: "safety"` and a friendly message in the
  fan's language; an unsafe display name gets `422`. See [responsible-ai.md](responsible-ai.md).
