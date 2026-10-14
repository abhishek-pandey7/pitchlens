# Rust, phase 2: futures, the browser and the broadcast edge

Status: **proposed, for review.** Follows [rust-core-plan.md](rust-core-plan.md); that plan's tasks R-0 to R-7 are built.

## 1. Goal

Rust becomes the part of PitchLens that does the heavy, real-time and high-fan-out work. It runs in three places from one codebase:

```
                         ┌────────────────────── one Rust kernel (engine/core) ──────────────────────┐
                         │ tracking build · physical metrics · frame JSON · win probability · futures │
                         └───────────────┬──────────────────────┬──────────────────────┬──────────────┘
                                         │ PyO3                 │ wasm-bindgen          │ crate dependency
                           Python engine (agents, API)    Browser (web worker)    pitchlens-edge (axum)
                           on Azure Container Apps        what-if sandbox,        SSE fan-out to viewers,
                                                          full-match replay       a third Container App
```

Python stays where the hackathon's hero tech requires it: Microsoft Agent Framework agents, Foundry and Azure clients, the evals and the MCP server.

We picked three additions (asked on 2026-10-14) for what they show in the demo:

| # | Feature | Demo moment |
|---|---|---|
| F | **Futures**: 100,000 simulated endings of the match after every key event | "Southvale come back in 18% of futures; the most likely score is 2–2" |
| W | **The kernel in the browser** (WebAssembly) | A fan drags the score or sends a player off, and the futures recalculate instantly with no server; the whole match replays in 3D, built in the browser |
| E | **Rust broadcast edge** | One small container holds tens of thousands of live viewers; the load-test number goes on the slide |

## 2. Workspace layout (task X-1, needed by all three)

Today `engine/core` is a single PyO3 crate, and some kernels carry `#[pyfunction]` attributes. It becomes a Cargo workspace whose logic has no Python in it:

```
engine/core/
  Cargo.toml                 [workspace] members below; shared release profile
  kernel/                    pitchlens-kernel: pure Rust, no pyo3, no wasm-bindgen
    src/shape.rs tracking.rs physical.rs frames.rs win_prob.rs futures.rs rng.rs
  py/                        pitchlens-py: PyO3 bindings → Python module `pitchlens_core` (unchanged name/API)
  wasm/                      pitchlens-wasm: wasm-bindgen bindings → npm package `@pitchlens/kernel`
edge/                        pitchlens-edge: axum service (separate workspace member at repo root level)
```

- **Bindings only convert.** Bindings turn NumPy arrays, JS typed arrays and JSON into kernel types, and back. All the logic and all parity-sensitive maths live in `kernel`.
- **Python API unchanged.** `pitchlens_core` keeps every function it has today, so the parity tests, `build_core.py` and the Docker stage keep working; only paths change.
- **Two feature flags in `kernel`.** `parallel` enables rayon for the futures and is off for WASM. `wire-json` covers the frame writer.

## 3. F: Futures, a Monte Carlo match engine

### 3.1 Why not just a faster win probability

Today's win probability is a closed-form Poisson grid with constant scoring rates. Monte Carlo over that same model would only reproduce those numbers. The futures model simulates the remaining minutes step by step, which lets it capture what a formula can't:

- **Game state.** A side that is behind late on raises its attacking rate, and a side that is ahead sits deeper. The effect scales with the score difference and minutes left.
- **Momentum that fades.** Each side's current momentum and field tilt (already computed in `interpret/`) lift its rate now, and the effect decays with a half-life of a few minutes.
- **Discipline.** Further red cards arrive at each side's current foul and card rate, and each dismissal applies the existing ×0.7 / ×1.2 multipliers from that minute on.
- **Fatigue (optional).** If the fatigue detector ships, rates drift down in the last 20 minutes.

### 3.2 Model and output

- **Simulation:** 0.25-minute steps until full time, including expected added time. Goals come from per-step Poisson draws at each side's current rate, and the state is updated after every goal.
- **Size:** 100,000 paths by default, split across cores with rayon.
- **Reproducible:** the random generator is `rand_xoshiro::Xoshiro256PlusPlus`, seeded from `(match_id, event seq)`. A replay or a seek gives the same futures.

Output per run (the `Futures` schema):

| Field | Meaning |
|---|---|
| `paths` | Paths simulated |
| `home`, `draw`, `away` | Final-result probabilities |
| `scores` | The 10 most likely final scores with probabilities, plus "other" |
| `comeback` | P(the side currently behind avoids defeat), P(it wins) |
| `next_goal` | P(home scores next), P(away scores next), P(no more goals); median minute of the next goal |
| `goals_left` | Expected remaining goals per side |
| `ms` | Compute time |

### 3.3 Where it runs

- **Live:** the session runs futures after goals, cards and substitutions, after a `momentum_shift` or `pressure_change` insight, and otherwise every 5 match-minutes. The result goes on `state.dynamics.futures` and is pushed in the next `state` message. Runs happen off the event loop and are skipped while a previous run is still in flight.
- **Explain:** a new `futures_swing` insight fires when P(comeback) or P(win) moves by 15 points or more. Its evidence comes from the futures, so narration stays grounded.
- **What-if:** `explain/counterfactual.py` adds futures for the hypothetical, for example "if that chance had gone in: home win 61%, most likely 2–1", next to today's closed-form number.
- **API:** `GET /matches/{id}/futures?paths=` returns the current futures. `POST /matches/{id}/futures/whatif` takes a hypothetical score, minute, red cards and momentum.
- **Agents:** the Analyst and Ask the Match get a `get_futures` tool, so "Can Southvale still win?" is answered from simulated futures, with citations.
- **Web:**
  - A "Futures" panel on /watch and /analyst: a result bar, the most likely scores as a small ranked list, and the comeback odds.
  - A futures-over-time chart on /analyst.
  - A "Futures" overlay template (`futures_bar`) for broadcast.

### 3.4 Win probability in Rust

The headline win probability stays closed-form, because it is stable and many tests depend on it. It is ported to the kernel bit for bit, removing the largest Python cost left (about 140 ms of the 424 ms needed to run a whole match through the pipeline).

### 3.5 Without the Rust core

A NumPy version of the same model runs 10,000 paths, vectorised over paths. Its results agree with the Rust version statistically, within Monte Carlo error, rather than bit for bit. The futures feature therefore works in either mode.

### 3.6 Verification

| Check | Pass condition |
|---|---|
| Calibration | With game-state, momentum and discipline turned off, futures reproduce the closed-form win probability within 4σ of Monte Carlo error at 50 match states |
| Determinism | Same seed → identical output; different event seq → different paths |
| Sense checks | 0–0 at 89′ gives draw > 0.85; 3–0 at 60′ gives P(trailing side wins) < 0.02; a red card lowers that side's win probability |
| Python fallback | NumPy results within 3σ of Rust at 20 states |
| Speed | 100k paths from kick-off < 30 ms on a 2-vCPU Container App; from 80′ < 10 ms |
| Win-probability port | Bit-identical to `win_prob.py` over every state of 5 matches |

## 4. W: the kernel in the browser

### 4.1 Package

- **Build:** `wasm-pack build --target web --release` on `engine/core/wasm`, then `wasm-opt -O3`. The target size is under 400 KB gzipped.
- **Packaging:** the output goes to `web/lib/kernel/`. It is git-ignored, built by `npm run build:kernel`, and built in CI and the web Docker image (a Rust stage, as for the engine).
- **Worker:** it loads in a web worker (`web/lib/kernel.worker.ts`), so a 100k-path run never blocks the page.
- **Threads:** none, so the `parallel` feature is off. Browser threads would need cross-origin isolation headers across the whole app, which is not worth it. The target is 50k paths in under 150 ms in Chrome on a mid-range laptop.

### 4.2 What-if sandbox (/watch and /analyst)

- **Controls:** score steppers, a minute slider, a red-card toggle per side, and a momentum slider ("what if City push for the last 10 minutes?").
- **Live updates:** each change reruns futures in the browser, debounced to 100 ms, and updates the Futures panel live with no network.
- **Reset:** "Back to live" restores the match's real state. The sandbox is clearly labelled as a hypothetical so it can't be mistaken for live numbers.

### 4.3 Full-match replay built in the browser

- **New endpoint:** `GET /matches/{id}/tracking-input` returns the packed tracking input that Python already builds for Rust (event columns, side and player tables). It is about 150 KB, versus about 5 MB of tracking.
- **Build in the browser:** the worker runs `build_tracking` in about 60–120 ms, so the browser holds the whole match's positions.
- **Scrubber:** a timeline under the 3D pitch on finished matches. Drag anywhere and the 3D view jumps there instantly, with no frames streamed.
- **Home page:** the home-page goal replay uses the same path. It no longer needs the clip endpoint, which stays for other consumers.
- **Parity test (Node/Playwright):** WASM tracking equals the engine's tracking arrays for one sample, byte for byte.

## 5. E: the Rust broadcast edge

### 5.1 Why

Every viewer holds an SSE connection. Today each one is a Python generator on the engine, and the engine serialises each message for every viewer in the same profile. The edge separates the two:

- **The engine** produces each channel's stream once.
- **The edge** fans it out from a single read.

### 5.2 Design (`pitchlens-edge`, axum + tokio)

- **Same URL shape:** `GET /api/v1/matches/{id}/stream?profile_id=&frames=&events=`, the same SSE event names and the same `Last-Event-ID` semantics. The web app only gains `NEXT_PUBLIC_STREAM_BASE`, which defaults to the API base when unset.
- **Channels:** one channel per `(match, profile, frames, events)`.
  - On the first viewer, the edge opens one upstream SSE connection to the engine for that channel.
  - It closes the upstream 30 s after the last viewer leaves.
- **Fan-out:** each channel is a `tokio::sync::broadcast` of pre-encoded SSE byte chunks (`Bytes`, reference-counted). Sending to 10,000 viewers costs 10,000 pointer copies, not 10,000 serialisations.
- **Joining mid-match:**
  1. The edge fetches a fresh snapshot from the engine: new `GET /matches/{id}/sync?profile_id=`, which returns the same `sync` payload with its last id.
  2. It sends the snapshot to the newcomer.
  3. It attaches the newcomer to the broadcast, skipping buffered messages with id ≤ the snapshot id.
- **Resume:** a ring buffer of the last 2,000 messages per channel serves `Last-Event-ID` resumes locally. Older ids fall back to a snapshot.
- **Slow viewers:** a viewer that lags the broadcast gets a fresh snapshot (`sync`) instead of an unbounded queue, and frames are dropped first.
- **Operations:**
  - **Health and stats:** `/health`, plus `/stats` (channels, viewers, messages in and out per second, p50/p95 fan-out delay).
  - **Logs and shutdown:** JSON logs, graceful shutdown, and CORS from the same allow-list as the engine.
- **Engine changes:** only the `/sync` snapshot endpoint. The engine's own `/stream` stays for direct use and tests.

### 5.3 Load test (`edge/loadgen`, a Rust binary)

- **Load:** it opens N SSE connections across M profiles, against a live replay at 10× with frames on.
- **Measurement:**
  - Delivery delay per message: the engine stamps `sent_at` on state messages, and the generator compares it with arrival time.
  - Dropped and resynced viewers.
  - Edge CPU and memory.
- **Runs:**
  - Locally: 5,000 viewers (Windows' socket limit).
  - On Azure: the edge on a 0.5 vCPU / 1 GiB Container App, with the generator on a separate container, stepping 1k → 10k → 25k → 50k viewers.
- **Headline:** the largest viewer count at p95 delay < 250 ms with no drops. Before/after numbers against the Python engine's direct `/stream` go in `docs/eval.md` and the README.

### 5.4 Deploy

- **Bicep:** a new `edge` Container App with external ingress, scaling on concurrent HTTP connections (KEDA), min 1 replica. Its image is built from `edge/Dockerfile`, a static musl binary on `distroless`, under 20 MB.
- **azd:** `azure.yaml` gains the `edge` service.
- **Engine ingress:** the engine keeps public ingress for REST, and the web app points its stream base at the edge.

## 6. Tasks

Branch and PR per task, merged with a merge commit after green CI, as usual. Tracks F and E are independent, so they can run as parallel agents in worktrees, as in phase 1. Track W follows F.

| ID | Task | Depends on | Done when | Est. |
|---|---|---|---|---|
| X-1 | Workspace split: `kernel` / `py` / `wasm` crates; bindings only convert; CI builds all three | R-4 merged | Python API unchanged; all parity tests and both CI modes green; `cargo test -p pitchlens-kernel` runs without Python | 0.5 d |
| F-1 | Kernel: win probability ported bit-exact; futures model, seeded RNG, rayon behind `parallel` | X-1 | Calibration, determinism, sense and speed checks (§3.6) | 1 d |
| F-2 | Python: binding, NumPy fallback, session runs and `state.dynamics.futures`, `futures_swing` insight, what-if futures, API endpoints, `get_futures` agent tool, schemas and TS types | F-1 | Engine suites green in both modes; fallback within 3σ; Ask the Match answers "can X still win?" citing futures | 1 d |
| F-3 | Web: Futures panel (/watch, /analyst), futures chart, `futures_bar` overlay | F-2 | Playwright: the panel updates during a live replay; screenshots reviewed | 0.5 d |
| W-1 | WASM build: wasm-pack package, worker, `npm run build:kernel`, CI and web Docker stage | X-1 | Bundle < 400 KB gz; CI web and web-e2e green with the kernel built | 0.5 d |
| W-2 | What-if sandbox on /watch and /analyst | W-1, F-3 | 50k paths < 150 ms in Chrome; Playwright drives the sandbox; values match the engine's `/futures/whatif` within Monte Carlo error | 1 d |
| W-3 | `tracking-input` endpoint, in-browser full-match build, 3D scrubber, home page on the same path | W-1 | WASM tracking byte-identical to the engine's for one sample (Playwright); scrubbing jumps instantly | 1 d |
| E-1 | Engine `/sync` snapshot endpoint and `sent_at` on state messages | none | API tests for the snapshot id contract | 0.25 d |
| E-2 | `pitchlens-edge`: channels, broadcast, joining via snapshot, ring-buffer resume, slow-viewer resync, stats, CORS, graceful shutdown | E-1 | Rust integration tests against a fake upstream; web e2e green with `NEXT_PUBLIC_STREAM_BASE` pointed at the edge | 1.5 d |
| E-3 | Load generator; local run to 5k; results in `docs/eval.md` | E-2 | Repeatable script; p95 and drop numbers recorded | 0.5 d |
| E-4 | Edge Dockerfile (musl, distroless), Bicep app plus KEDA, azd service, CI container smoke test | E-2 | CI `containers` builds and smoke-tests the edge; `az bicep lint` clean | 0.5 d |
| E-5 | Azure load test 1k → 50k on the deployed edge; headline in README | E-4 and the Azure deploy | Numbers in `docs/eval.md` | 0.5 d |

**Total:** about 9.25 days of work. With F and E run as parallel agents (as in phase 1), that is about 5–6 calendar days.

## 7. Timeline (13 days to submission)

| Dates | Engine and kernel | Web | Edge | Needs you |
|---|---|---|---|---|
| 14–15 Oct | R-2 to R-4 PR; X-1 | — | E-1 | — |
| 15–17 Oct | F-1, F-2 | F-3 | E-2 | — |
| 17–19 Oct | — | W-1, W-2 | E-3, E-4 | Azure for Students active, `azd` installed |
| 19–20 Oct | First Azure deploy (engine, web, edge) | W-3 | — | **Register by 20 Oct 12:00 PT** |
| 21–22 Oct | Evals on Foundry | — | E-5 load test on Azure | — |
| 22–25 Oct | Docs, README, pitch, demo script | — | — | Record the video |
| 26 Oct | Submit (closes 27 Oct 23:59 PT) | — | — | Submit form |

**Rule:** deploy the moment Azure access is ready, and keep building on top of a working deployment. Each feature is shippable on its own, and the demo works with any subset.

## 8. Risks

| Risk | Mitigation |
|---|---|
| The futures model gives implausible numbers | Calibration against the closed form, sense checks, and effect sizes kept small and documented; the headline win probability stays closed-form |
| WASM bundle size or speed | No threads, `wasm-opt`, lazy-loaded worker; falls back to the engine's `/futures/whatif` when WASM fails to load |
| Edge correctness (gaps, duplicates on join) | Snapshot-id contract tested; the web e2e suite runs through the edge; the engine's direct `/stream` stays as a fallback via env |
| Windows socket limits in local load tests | Local runs stop at 5k; the headline number comes from Azure |
| Azure quota or time | Edge on the smallest Container App size; load tests short and scheduled; deploy early (§7) |
| Scope | Each feature works on its own; cut order if late: W-3, then E-5, then W-2 |
