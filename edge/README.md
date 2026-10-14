# pitchlens-edge

The PitchLens broadcast edge: a small Rust service (axum + tokio) between the engine and viewers. The engine produces each channel's live stream once; the edge reads it over one connection and fans it out to every viewer of that channel. Plan: [docs/rust-expansion-plan.md](../docs/rust-expansion-plan.md) §5. Load-test numbers: [docs/eval.md](../docs/eval.md#broadcast-edge).

## What it serves

| Path | What |
|---|---|
| `GET /api/v1/matches/{id}/stream?profile_id=&frames=&events=` | The engine's live stream: same URL, query, SSE event names, ids and `Last-Event-ID` semantics ([docs/api.md](../docs/api.md#live-stream-sse)) |
| `GET /health` | Liveness, channel and viewer counts |
| `GET /stats` | Channels, viewers, messages in and out per second, fan-out delay p50/p95/p99 over the last 10–20 s, totals (local resumes, snapshot joins, resyncs, upstream connects), busiest channels |

## How it works

- **Channels.** One per `(match, profile, frames, events)`. The first viewer opens a single upstream SSE connection to the engine; it is closed 30 s after the last viewer leaves.
- **Fan-out.** Each upstream message is encoded once into `Bytes` and sent through a `tokio::sync::broadcast`, so 10,000 viewers cost 10,000 reference-count bumps, not 10,000 serialisations.
- **Joining mid-match.** The edge fetches a snapshot from the engine's `GET /matches/{id}/sync` (`{last_id, sync}`), sends the `sync` to the newcomer, then attaches it to the broadcast and the channel's ring buffer together, under the lock publishing takes, and skips every id ≤ `last_id`. Nothing published between the snapshot and the attach can be lost or repeated. Viewers joining together share one snapshot (at most one engine call per channel per second). A new channel's upstream starts with `Last-Event-ID: last_id`, so it carries on exactly where the snapshot ends.
- **Resume.** The last 2,000 messages with an id are kept per channel. A reconnect with `Last-Event-ID` inside that window is served from memory. An older or unknown id gets a fresh snapshot instead.
- **Slow viewers.** A viewer more than 1,024 messages behind the broadcast is moved onto a fresh snapshot; the edge never queues without bound.
- **Upstream.** Reconnects with `Last-Event-ID` (the engine replays the gap). If the engine can't resume (it answers with a fresh `sync`, or its ids go backwards after a restart), every viewer of the channel takes a new snapshot.
- **Operations.** Pings every 15 s (as the engine), `X-Accel-Buffering: no`, CORS from the same allow-list format as the engine, JSON logs (`RUST_LOG`), graceful shutdown on SIGTERM / Ctrl-C (streams end, upstreams close).

## Configuration

| Variable | Default | What |
|---|---|---|
| `ENGINE_URL` | `http://localhost:8000` | The engine's origin (no `/api/v1`) |
| `PORT` | `8080` | Listening port |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated browser origins, as for the engine (`*` allows any) |
| `CHANNEL_IDLE_SECS` | `30` | Upstream kept open after the last viewer leaves |
| `RING_SIZE` | `2000` | Messages kept per channel for resumes |
| `BROADCAST_CAPACITY` | `1024` | How far a viewer may fall behind before it is resynced |
| `SNAPSHOT_TTL_MS` | `1000` | How long one snapshot is shared by joining viewers |
| `PING_SECS` | `15` | Keep-alive comment interval |
| `RUST_LOG` | `info` | Log filter |

The web app streams through the edge when it is built with `NEXT_PUBLIC_STREAM_BASE=http://<edge>/api/v1` ([web/README.md](../web/README.md#environment)).

## Build, test, run

```bash
cd edge
cargo test --workspace                       # unit tests + integration tests against a fake engine
ENGINE_URL=http://localhost:8000 PORT=8080 cargo run --release
docker build -t pitchlens-edge .             # static musl binary on distroless, non-root
```

`tls` (rustls, for an `https://` engine) is a default feature. Its crypto crate needs a C compiler, so on a Windows machine without one build with `--no-default-features` (plain HTTP to a local engine is all that needs).

**Windows with the GNU toolchain and no MinGW:** tokio's Windows bindings use `raw-dylib`, for which rustc calls GNU `dlltool`, and that needs GNU `as`. Without MinGW binutils, point rustc at LLVM's dlltool instead (`rustup component add llvm-tools`, copy `llvm-ar.exe` to `llvm-dlltool.exe`, and wrap it so the GNU-only flags `-f <x>`, `--temp-prefix <x>` and `--no-leading-underscore` are dropped), then build with `RUSTFLAGS="-C dlltool=<wrapper>"`. Linux, macOS and MSVC builds need none of this.

## Load generator

`loadgen/` opens N SSE viewers across M profiles and reports delivery delay (from the engine's `sent_at` on `state` messages) p50/p95/p99, messages per second, and dropped or resynced viewers:

```bash
cargo run --release -p pitchlens-loadgen -- --url http://127.0.0.1:8080/api/v1 --match <id> \
  --viewers 500 --profiles raw,p-casual-es,p-analyst-en --frames true --events false \
  --ramp 10 --duration 60 --engine http://127.0.0.1:8000/api/v1 --replay 10 --json out.json
```

Point `--url` at the engine itself to measure the direct `/stream` for comparison.
