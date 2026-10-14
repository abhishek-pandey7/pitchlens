# Rust core: plan

Status: **proposed, for review.** Nothing in this plan is built yet.

## 1. Decision

PitchLens stays a Python service. Only the CPU-bound loops that run over tracking data move into a Rust extension module, `pitchlens_core`, which Python calls in-process (PyO3 + maturin). We chose this over a full rewrite (asked on 2026-10-14) for three reasons:

- **The agents need Python.** Microsoft Agent Framework, the hackathon's hero tech, has Python and .NET SDKs only. The six agents, the evals and the MCP server stay where they are.
- **The deadline.** Submission closes 27 Oct. A full rewrite of about 13,700 lines plus 3,700 lines of tests would not finish in time.
- **Speed.** Measured below, almost all of the engine's CPU time sits in three loops of about 450 lines in total.

The pure-Python implementations stay in the repository as the reference and the fallback. The engine runs unchanged when the extension isn't built.

## 2. Where the time goes

Measured on the development laptop (Python 3.12, Windows) for the comeback sample, seed 7: 2,713 events, 29,418 frames at 5 Hz, and 50 tracked players.

| Work | Today | Runs when | Target in Rust |
|---|---|---|---|
| Simulate a match (`sim/simulator.py`) | 0.17 s | A new match is generated | Stays in Python: already fast |
| **Build tracking from events** (`sim/tracking.py`) | **5.4 s** | Each stored sample on first use, each generated match, the dataset build. Slower on CI runners (tens of seconds) | < 0.15 s |
| **Tracking frames as objects** (`Tracking.frame`, Pydantic) | **5.5 s** for a whole match, 0.19 ms per frame | Each live frame, goal clips, catching up after a seek | Whole match < 0.3 s, as JSON-ready dicts |
| **Physical metrics over a match** (`interpret/physical.py`) | **1.45 s** | Live per frame, plus all earlier frames when a replay starts mid-match | < 0.05 s |
| Live pipeline per event (state, metrics, detectors, cues) | 0.1–1.5 ms per stage | Every event | Stays in Python: far inside the 300 ms budget |

What this buys:

- **No more waiting for players on the pitch.** Stored matches get players and ball on the first replay. Today the first replay of an uncached match plays without them while a worker process rebuilds the tracking.
- **Instant seeks.** Seeking replays the physical metrics and frame history in milliseconds.
- **No worker process.** The `ProcessPoolExecutor` and the startup warm-up can go: a 0.15 s build runs inline in a thread.
- **Fast dataset rebuild.** Regenerating the 50-match dataset takes seconds instead of minutes.

## 3. Scope

Same tiers as the main plan: core first, the plus tier if time allows, stretch only with clear headroom.

**Core**
- `pitchlens_core.build_tracking(...)`: the tracking generator, including the team-shape helper it calls (`sim/pitch.py: team_shape`, `clamp`, `mirror`).
- Wiring: `generate_tracking` calls Rust when the extension is importable, and Python otherwise. The `PITCHLENS_CORE=python|rust|auto` setting forces either (default `auto`).
- Parity tests, CI build, and a Docker multi-stage build.

**Plus**
- `PhysicalTracker` in Rust, with the same Python-facing API (`apply`, `player`, `reset_period`). It also gets a bulk `apply_range(tracking, start, end)` for seeks.
- `frames_json(tracking, start, end)`: tracking frames as JSON-ready dicts or bytes for SSE and `GET /matches/{id}/clips/goal`, skipping Pydantic construction.

**Stretch (not planned)**
- Porting the simulator. It is not slow (0.17 s), and matching seeded results would mean reproducing CPython's Mersenne Twister and `random.gauss` call for call. Every pinned result depends on that: "seed 7 comeback ends 2-3", the dataset hashes, and the e2e score sequence. That is a lot of risk for no gain.

**Out of scope:** the agents, narration, providers, API, SSE, MCP server, evals and infrastructure code. These stay Python.

## 4. Design

### 4.1 Boundary

Rust receives plain arrays and numbers, not Pydantic models or the league object. Python keeps everything that knows about rosters, formations and schemas, and packs one input per match:

```
TrackingInput
  match_id: str
  sides[2]:            club index, is_home, xi (player indices, slot-aligned), anchors [(x, y)],
                       slots [position code], press, width
  players[N]:          player_id (sorted, defines the xy column order), top_speed_kmh
  events (columns):    type code u8, match_ms i64, period u8, team idx i8, player idx i16,
                       related idx i16, recipient idx i16, start (x, y) | NaN, end (x, y) | NaN,
                       duration_ms i32, high u8, outcome code u8
```

`build_tracking(input) -> (ms: i64[T], period: i8[T], ball: f32[T,3], xy: f32[T,N,2])` returns NumPy arrays via `rust-numpy` without copying. `Tracking` keeps its current fields, so `save`, `load`, `frame`, `index_at`, the cache and every consumer are unchanged.

### 4.2 Crate layout

```
engine/core/                     Cargo workspace member, built with maturin
  Cargo.toml                     pyo3 (abi3-py312), numpy, no other runtime dependencies
  pyproject.toml                 maturin build backend; module name pitchlens_core
  src/lib.rs                     #[pymodule]: build_tracking, PhysicalTracker, frames_json
  src/tracking.rs                ball path + per-tick player movement (port of sim/tracking.py)
  src/shape.rs                   team_shape / clamp / mirror (port of sim/pitch.py)
  src/physical.rs                1 s window speeds, HSR, sprints, thresholds (port of physical.py)
  src/frames.rs                  frame slices to dicts/JSON
  tests/                         cargo unit tests for shape, ball path, window speed
```

The Python side gets a small `pitchlens/core.py` facade. It tries `import pitchlens_core`, reads `PITCHLENS_CORE`, exposes `RUST: bool`, and packs `TrackingInput` from `(meta, events, league)`.

### 4.3 Matching the Python behaviour

The port is line for line, in the same order of operations, with f64 internally and f32 only at storage, as today:

- **`np.interp`** becomes a binary search plus linear interpolation, with the same clamping at both ends.
- **Ball path.** It keeps the interrupted-flight rule: a later event drops the planned remainder of a flight.
- **Substitutions.** A substitute takes over the replaced player's position and slot.
- **Red cards.** A dismissed player's slot and anchor are removed from the shape.
- **Per tick:** the shape reference follows a smoothed ball position (`SHAPE_LAG_S`). Each player's target comes from the team shape, overridden for the carrier, the pass recipient and pressers. The step is capped at the player's top speed, as now.

Bit-identical floats aren't a goal: `hypot` and summation order can differ in the last ulp. Parity is checked against tolerances that are far below anything visible or measurable (§5).

## 5. Verification

| Check | Pass condition |
|---|---|
| Tracking parity | All 11 stored samples plus 20 generated seeds × 5 storylines: identical `ms`, `period` and `player_ids`, the same NaN pattern in `xy` (who is on the pitch), and max abs difference ≤ 1e-3 m for ball and players |
| Physical parity | Over every sample, per player: distance within 0.1 %, top speed within 0.05 km/h, the same sprint counts, and the same alert sequence (kind, player, frame) |
| Frames parity | `frames_json` equals `[Tracking.frame(i).model_dump(mode="json") ...]` for every frame of one sample |
| Existing suites | The engine pytest suite and Playwright e2e pass with `PITCHLENS_CORE=rust`, and again with `PITCHLENS_CORE=python` |
| Speed | `scripts/bench_core.py` meets the §2 targets on CI (logged, not asserted, because runner speed varies) |
| Fallback | Without the extension installed, the engine starts and every test passes (the CI job `engine-python-only`) |

The parity tests live in `engine/tests/unit/test_core_parity.py` and are skipped when the extension isn't built.

## 6. Build, CI and deploy

- **Local (Windows):** install Rust with `rustup` (stable, MSVC target). The Visual Studio C++ build tools are already needed by some Python wheels. Then run `pip install maturin` and `maturin develop -r` inside `engine/core`. The Makefile and README get a `make core` target.
- **CI:**
  - The `engine` job adds `dtolnay/rust-toolchain@stable` plus a Swatinem cargo cache. It builds the wheel with `maturin build -r` and installs it, then runs pytest twice (`PITCHLENS_CORE=rust` and `PITCHLENS_CORE=python`).
  - `cargo test` and `cargo clippy -- -D warnings` run alongside.
  - The `web-e2e` job uses the Rust build.
- **Docker:** the engine image gets a multi-stage build. A `rust:1-slim` builder stage builds the abi3 wheel, and the existing `python:3.12-slim` stage installs it. The image stays non-root on port 8080, and the runtime image doesn't grow by more than a few MB.
- **Azure:** no change, because the extension ships inside the engine image.

## 7. Tasks

Branch and PR per task, merged with a merge commit after green CI, as usual. Estimates assume one developer.

| ID | Task | Depends on | Done when | Est. |
|---|---|---|---|---|
| R-0 | Toolchain: rustup on the dev machine; maturin in the engine dev extras | none | `cargo --version` and `maturin --version` work; documented in README | 0.25 d |
| R-1 | Crate scaffold: `engine/core` with a PyO3 module exposing `version()`; `pitchlens/core.py` facade with `PITCHLENS_CORE`; CI builds the wheel and runs pytest in both modes | R-0 | CI green with an empty Rust module, and the Python-only job green | 0.5 d |
| R-2 | Port `team_shape` / `clamp` / `mirror` and the ball path, with cargo unit tests against values captured from Python | R-1 | Cargo tests pass; shape for every formation × possession × press matches Python within 1e-9 | 0.5 d |
| R-3 | Port the per-tick loop (`build_tracking`) and pack `TrackingInput` in Python | R-2 | Tracking parity (§5) on all samples and 100 generated matches | 1 d |
| R-4 | Switch to Rust: `generate_tracking` uses Rust under `auto`; stored samples build inline on first replay; remove the worker pool and startup warm-up when Rust is present (kept for the Python fallback) | R-3 | First replay of an uncached sample shows players within 1 s; e2e green; benchmark meets target | 0.5 d |
| R-5 | Docker multi-stage build and CI cargo cache; docs: `docs/architecture` note, README "Building the Rust core" | R-4 | The `containers` job is green; the image runs the e2e smoke test with the extension loaded (health reports `core: rust`) | 0.5 d |
| R-6 *(plus)* | `PhysicalTracker` in Rust plus `apply_range`; sessions use it under `auto` | R-4 | Physical parity (§5); seek to 80′ completes in < 100 ms | 1 d |
| R-7 *(plus)* | `frames_json` for SSE frames and goal clips | R-4 | Frames parity; clip endpoint < 20 ms | 0.5 d |

**Total:** core (R-0 to R-5) about 3.25 days; with the plus tier about 4.75 days.

## 8. Timeline against the deadline

| Dates | Work |
|---|---|
| 14–17 Oct | R-0 to R-5 (core) |
| 17–18 Oct | Early Azure deploy, as already planned: the Rust core ships inside the same image |
| 18–19 Oct | R-6, R-7 if the deploy is healthy; otherwise skip them |
| 20 Oct, 12:00 PT | Registration deadline (needs you) |
| 20–25 Oct | Docs, demo script, video, evals on Foundry |
| 26 Oct | Submit (closes 27 Oct, 23:59 PT) |

Cut line: if R-3 parity isn't met by 17 Oct, stop. Keep Python and keep the branch for after the hackathon. Nothing downstream depends on the Rust core.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Tracking diverges subtly (ordering, NaN handling, interrupted flights) | Port line for line; parity on 111 matches before switching; the Python fallback stays one setting away |
| Windows build friction (MSVC, linker) | abi3 wheel with one toolchain; CI on Linux is the source of truth; local dev can run `PITCHLENS_CORE=python` |
| CI time grows | Cargo cache; release build only once per job; `cargo test` runs in parallel with pytest |
| A judge or teammate can't build Rust | The engine runs without it, and the Docker image and Azure deploy include it prebuilt |
| Time taken from the demo and deploy | Hard cut line on 17 Oct (§8); the plus tier is optional |
