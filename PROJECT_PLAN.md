# PitchLens: Synthetic Match Insights Engine
### Inside the Game: Developer Hackathon (Microsoft × Premier League), "Synthetic Match Insights Engine for Premier League Studio" challenge: full project documentation and build plan

> **Status:** documentation approved; awaiting go-ahead to start coding. Task IDs in §19 are the working checklist, so tick them off as work lands.
> **Rev 2:** updated with the official submission rules and judging rubric (§1A) and a multi-agent architecture (§10A). The demo video is now < 2 min.

---

## Table of contents
1. Context & goals
1A. Hackathon rules, submission requirements & judging rubric
2. Requirements traceability (challenge → feature → module)
3. Architecture overview
4. Tech stack & rationale
5. Repository layout
6. Data model & schemas
7. Synthetic data generator
8. Interpret layer: metrics catalog
9. Explain layer: moment detection & "why it matters"
10. Narrative layer: LLM, grounding, recaps
10A. Agent layer: multi-agent orchestration (Microsoft Agent Framework + MCP)
11. Personalization layer
12. Render layer: overlay cue contract
13. Frontend applications
14. API specification
15. Azure deployment
16. Configuration & environment variables
17. Latency budget & performance
18. Testing & verification
19. Task breakdown (checklist with acceptance criteria)
20. Team split & timeline
21. Demo script
22. Risks & mitigations
23. Expanded feature set (Plus tiers P1/P2 + stretch)
24. Submission checklist

---

## 1. Context & goals

**Challenge.** The challenge is to build an AI insights engine on Azure that takes *synthetic*, football-realistic match events and turns them into explainable match intelligence: real-time narratives, key moments, recaps, and synchronized on-screen overlays. All of it must be personalized for each fan (persona, language, favorite club or player, focus metric) and usable in studio, broadcast and streaming settings.

**Starting point.** `E:\projects\premhacka` is empty, so this is a new build.

**Decisions already made**
| Decision | Choice |
|---|---|
| Team / time | Small team (2–3), **3 weeks**, ambitious scope: Core + "Plus" features (§23) planned in, CV stays stretch |
| Stack | Python 3.12 (FastAPI, asyncio) engine + Next.js (App Router, TypeScript, Tailwind) frontend |
| Agents | **Microsoft Agent Framework (Python)** for agent definitions and workflows; models served from **Microsoft Foundry** (§10A) |
| LLM | Pluggable model client: `mock` (offline, deterministic), `azure_openai`, `foundry_claude` (Claude on Microsoft Foundry) |
| Hosting | **Azure Container Apps** (primary). AKS is optional and documented as a scale-out path. Matches the official reference architecture session (AKS + ACA) |
| Video auto-eventing (CV) | Stretch goal only |

**Goals**
- One pipeline with five stages, **Ingest → Interpret → Explain → Render → Personalize**, running live on Azure.
- **Facts first, words second.** Deterministic code computes every number. The LLM only puts facts into words, and a grounding validator rejects any number it makes up.
- One Insight produces two different experiences: a casual fan in Spanish and an analyst in English.
- Overlay output is timed and machine-readable, so a broadcast rendering partner could consume it directly.

**Non-goals**
- Real Premier League data, real club or player names, real badges.
- Production-grade CV. It's a stretch proof-of-concept at most.
- User accounts or auth beyond a simple profile ID. Profiles are anonymous.

---

## 1A. Hackathon rules, submission requirements & judging rubric

**Event:** *Inside the Game: Developer Hackathon* by Microsoft and the Premier League. The project must use **synthetic, football-realistic data only**; no real Premier League match data is used or redistributed.

**Key date:** the Microsoft Reactor session *"Build AI-powered soccer insights apps with AKS and ACA"* (Brian Benz) is on **Wed 14 Oct 2026, 16:00–17:00 UTC**. Attend it, and align the infra with its reference architecture.
**To confirm:** the exact submission deadline on the hackathon site, then pin it in §20.

**Required deliverables**
| Requirement | How we meet it | Task |
|---|---|---|
| Newly built project meeting the challenge requirements | Everything in this repo, built during the hackathon | all |
| Pitch / description: what we built, **which Microsoft & Azure technologies**, what it does, what problem it solves | `docs/pitch.md` + project page text (draft in §21A) | T-84 |
| Demo video **< 2 minutes**, showing the project running on its platform, public on YouTube/Vimeo | 1:55 script in §21; screen capture of the live Azure deployment | T-82 |
| No third-party trademarks or copyrighted music in the video | Fictional clubs/players only; no PL logos or kits; no music, or our own/licensed royalty-free with proof | T-82 |
| **Public** GitHub repository | **Private during development, made public before submission (T-83)**; secret scanning + gitleaks in CI; no keys in code (managed identity) | T-00, T-75 |
| Follow the hackathon rules, Code of Conduct and Security Policy | `SECURITY.md`, `CODE_OF_CONDUCT.md`, no confidential info, responsible-AI notes | T-00, T-81 |

**Judging rubric: how PitchLens scores on each criterion (20% each)**
| Criterion | What judges look for | PitchLens answer | Evidence to show |
|---|---|---|---|
| **1. Technological implementation** | Quality engineering, effective use of hero technologies, structured, documented, maintainable code | Typed schemas end to end, pure-function metrics with tests, CI/CD, IaC (`azd up`), OpenTelemetry. Hero tech: **Microsoft Foundry, Microsoft Agent Framework, Azure Container Apps, Event Hubs, Cosmos DB, AI Speech, AI Content Safety, App Insights, GitHub Actions, GitHub Copilot** | README badges (CI, coverage), architecture diagram, `docs/` |
| **2. Agentic design & innovation** | Creative agentic patterns, multi-agent orchestration, novel AI | **7 cooperating agents** (§10A): sequential, concurrent fan-out, reflection (Fact-Checker), handoff, group-chat recap, and a human-in-the-loop Director. A **PitchLens MCP server** lets any external agent query match intelligence. Grounded "facts-first" design | Agent trace view in `/studio`, Foundry tracing screenshots, `docs/agents.md` |
| **3. Real-world impact & applicability** | Significance of the problem, production readiness, impact | Plugs into broadcast (timed overlay cue contract, WebVTT chapters/captions), streaming (personalized second screen) and studio (pundit sheet). Production-ready: autoscaling, health probes, managed identity, Key Vault, Content Safety, cost guardrails, latency SLOs | Overlay contract doc, SLO dashboard, deployment guide |
| **4. User experience & presentation** | Intuitive UX, clear value in demo, balanced frontend and backend | Broadcast-grade overlays, mobile second screen, `/compare` split view; a demo that delivers the "same moment, two fans" point in < 2 min | Video, live URL |
| **5. Adherence to category** | Fits the challenge brief | Full traceability table (§2) covering all five pipeline stages and requirements (ii)–(vi) | §2 table in README |

---

## 2. Requirements traceability

| Challenge requirement | Feature | Module(s) |
|---|---|---|
| (i-a) Ingest live events | Simulator → Bus → engine; external `POST /ingest` | `sim/`, `ingest/` |
| (i-b) Interpret into stats & patterns | Incremental MatchState + metrics | `interpret/` |
| (i-c) Explain why it matters | Detectors + evidence + WP delta + change-points | `explain/` |
| (i-d) Render on screen | Overlay cue contract + `/overlay` OBS page | `render/`, `web/app/overlay` |
| (i-e) Personalize | Profiles, ranker, persona tone, player-focus mode | `personalize/`, `web/app/watch` |
| (ii) Player identification + speed/distance thresholds | Player tags triggered by sprint / top-speed / distance thresholds | `interpret/physical.py`, `PlayerTag` |
| (ii) Pass quality | Distance, accuracy, difficulty 0–100 | `interpret/passes.py`, `PassCard` |
| (ii) Speed metrics | Ball speed, shot speed | `interpret/shots.py`, `ShotSpeedCard` |
| (ii) Auto-eventing from video | Stretch: CV POC → `POST /ingest` | `cv/` (stretch) |
| (iii) Narratives, milestones, recaps | Live feed, milestones, HT/FT recap, player of the match | `narrate/` |
| (iv) Explainability: control vs chaos, pressure, rhythm | Control/Chaos index, PPDA CUSUM, tempo breaks | `interpret/control_chaos.py`, `rhythm.py`, `explain/detectors.py` |
| (v) Data innovation | Open synthetic dataset + generator + schema docs | `sim/`, `data/`, `docs/dataset.md` |
| (vi) Multi-language | Native-language LLM generation + glossary + template fallback | `narrate/templates/`, `personalize/glossary/` |
| "Runs on Azure" | Container Apps, Event Hubs, Foundry/OpenAI, Cosmos, App Insights | `infra/` |
| "AI agents / workflows" (brief + rubric #2) | Multi-agent workflow + MCP server | `agents/`, `mcp/` (§10A) |

---

## 3. Architecture overview

```
                    ┌──────────────── Azure Container Apps ────────────────┐
[Simulator/Replay] ─┤                                                       │
[CV POC (stretch)] ─┼─► Bus (memory | Event Hubs) ─► Interpret ─► Explain ──┼─► Insight stream
[External POST]    ─┘        events.v1                 MatchState   Detectors│        │
                                                       Metrics      Evidence │        ▼
                                                                             │  Personalize (per profile)
                                                                             │   rank → template text (instant)
                                                                             │   → LLM narration (async, cached)
                                                                             │   → overlay cues
                                                                             │        │
                                                     FastAPI SSE ◄───────────┘────────┘
                                                          │
                        Next.js: /overlay  /watch  /analyst  /studio
Persistence: Cosmos DB (profiles, match archive, insight + narration cache)
Telemetry:  App Insights (per-stage latency spans)
```

**Data flow per event**
1. Event arrives on the bus, is validated against `events.v1`, and gets a sequence number.
2. `MatchState.apply(event)` updates possession, score, clock, and per-player and per-team accumulators.
3. Metric modules compute derived values (pass difficulty, xG, speeds, windows) and attach them to an `EnrichedEvent`.
4. Detectors scan the state and emit zero or more `Insight`s with evidence and salience.
5. For each connected profile, the ranker filters and re-weights. Template text renders right away, and an LLM task is queued if the insight passes the persona threshold.
6. The overlay cue generator schedules cues according to priority and collision rules.
7. SSE pushes `state`, `insight`, `cue`, and `narration` messages to subscribed clients.

---

## 4. Tech stack & rationale

| Layer | Choice | Why |
|---|---|---|
| Engine | Python 3.12, FastAPI, Pydantic v2, asyncio, numpy, scipy | Fast to iterate on data and stats code; Pydantic gives typed schemas and JSON Schema export |
| Streaming to clients | Server-Sent Events (sse-starlette) | One-way push, works through proxies, very simple; WebSocket only if bidirectional is needed |
| Bus | `Bus` protocol: in-memory asyncio queue (local) / Azure Event Hubs (cloud, `azure-eventhub`) | Swap-in without code change |
| LLM | `LLMProvider` protocol; `openai` SDK (Azure) / Foundry inference endpoint / mock | Decide later based on credits; mock keeps tests offline |
| Frontend | Next.js 16 App Router, TypeScript, Tailwind, Framer Motion, visx or Recharts | Overlay animations + charts; SSR not needed for overlay pages |
| Types shared | JSON Schema exported from Pydantic → `json-schema-to-typescript` → `web/lib/types.ts` | One source of truth for schemas |
| Storage | Cosmos DB serverless (NoSQL API) | Cheap, JSON-native |
| Infra | Bicep + Azure Developer CLI (`azd up`) | One-command deploy for judges |
| Observability | OpenTelemetry → Application Insights | Per-stage latency proof |
| Tests | pytest, pytest-asyncio, hypothesis (properties), Playwright (overlay smoke) | |
| Agents | Microsoft Agent Framework (Python) + Microsoft Foundry models | Multi-agent workflows, tool use, tracing; hero tech for rubric #1/#2 |
| Agent interop | MCP Python SDK (streamable HTTP) | Exposes PitchLens tools to Copilot, Foundry and other agents |
| Safety | Azure AI Content Safety | Screens fan input and generated output |
| CI/CD | GitHub Actions (lint, type-check, test, build, `azd deploy`), Dependabot, gitleaks, CodeQL | Production readiness (rubric #1/#3) |
| Dev productivity | GitHub Copilot (agent mode) used during the build | Mentioned in the pitch |

---

## 5. Repository layout

```
premhacka/
├─ PROJECT_PLAN.md                 ← this document
├─ README.md                       quickstart, architecture image, demo link
├─ azure.yaml                      azd service map
├─ engine/
│  ├─ pyproject.toml
│  ├─ pitchlens/
│  │  ├─ schema/        events.py  insights.py  overlays.py  profiles.py  export_jsonschema.py
│  │  ├─ sim/           rosters.py  pitch.py  simulator.py  storylines.py  tracking.py  replay.py  cli.py
│  │  ├─ ingest/        bus.py  memory_bus.py  eventhubs_bus.py  validator.py
│  │  ├─ interpret/     state.py  passes.py  shots.py  physical.py  momentum.py
│  │  │                 control_chaos.py  rhythm.py  win_prob.py  windows.py
│  │  ├─ explain/       detectors.py  salience.py  evidence.py  changepoint.py  milestones.py
│  │  ├─ narrate/       provider.py  mock.py  azure_openai.py  foundry_claude.py
│  │  │                 prompts/  templates/  grounding.py  recap.py  cache.py  worker.py
│  │  ├─ personalize/   profiles.py  ranker.py  player_focus.py  glossary/{en,es,fr,pt,ar,hi}.yaml
│  │  ├─ render/        cues.py  scheduler.py
│  │  ├─ agents/        clients.py  tools.py  analyst.py  narrator.py  localizer.py  fact_checker.py
│  │  │                 director.py  editor.py  companion.py  workflows.py  safety.py  budgets.py
│  │  ├─ mcp/           server.py (pitchlens-mcp, streamable HTTP)
│  │  ├─ store/         cosmos.py  memory.py
│  │  ├─ telemetry.py
│  │  └─ api/           main.py  streams.py  routes/{matches,profiles,recap,ingest,health}.py
│  └─ tests/            unit/  integration/  snapshots/
├─ web/
│  ├─ app/  overlay/  watch/  analyst/  studio/  compare/  (+ layout, page)
│  ├─ components/  PitchCanvas  PlayerTag  PassCard  ShotSpeedCard  MomentumBar
│  │               LowerThird  ChaosMeter  WinProbChart  PassNetwork  InsightFeed  ProfilePicker
│  ├─ lib/  types.ts (generated)  sse.ts  clock.ts  i18n.ts
│  └─ tests/  overlay.spec.ts
├─ data/  rosters/  matches/*.jsonl  README.md
├─ docs/  dataset.md  schemas.md  metrics.md  overlay-contract.md  demo-script.md  architecture.png
└─ infra/ main.bicep  modules/{containerapps,eventhubs,cosmos,appinsights,foundry}.bicep
```

---

## 6. Data model & schemas
All schemas are versioned (`schema_version: "1.0"`). Coordinates use metres on a 105 × 68 pitch, origin at the bottom-left from the home team's perspective, and attacking direction is normalised per team in the derived fields. Time is `match_ms` (ms since kick-off, including stoppage time) plus `period`.

### 6.1 Match metadata
```json
{ "match_id": "SYN-2026-0001", "competition": "Synthetic Premier Division", "venue": "Riverside Park",
  "kickoff_utc": "2026-10-18T14:00:00Z", "weather": "light rain", "referee": "R. Hale",
  "home": {"club_id": "NHR", "name": "Northbridge Harriers", "colors": ["#0B3D91","#FFFFFF"], "formation": "4-3-3"},
  "away": {"club_id": "SVC", "name": "Southvale City", "colors": ["#C8102E","#000000"], "formation": "4-2-3-1"},
  "lineups": {"NHR": [{"player_id":"NHR-09","name":"Tomás Reyes","pos":"ST","shirt":9}], "SVC": []},
  "pre_match": {"home_strength": 1.55, "away_strength": 1.20} }
```

### 6.2 Event (`events.v1`)
```json
{ "schema_version":"1.0", "event_id":"e-000412", "match_id":"SYN-2026-0001", "seq":412,
  "type":"pass", "period":1, "match_ms":1834200, "team_id":"NHR", "player_id":"NHR-08",
  "possession_id":57, "start":{"x":38.2,"y":22.5}, "end":{"x":61.0,"y":40.1},
  "outcome":"complete", "recipient_id":"NHR-10", "under_pressure":true, "pressure_player_id":"SVC-06",
  "body_part":"right_foot", "height":"ground", "qualifiers":["through_ball"],
  "ball_speed_kmh":null, "ts_utc":"2026-10-18T14:30:34.200Z" }
```
**Event types:** `match_start, period_start, period_end, match_end, pass, carry, dribble, shot, save, goal, own_goal, tackle, interception, clearance, block, ball_recovery, pressure, foul, card, substitution, offside, corner, free_kick, throw_in, goal_kick, possession_change, injury_stop, var_check`.

### 6.3 Tracking frame (sampled 5 Hz, ball + players)
```json
{ "match_id":"SYN-2026-0001", "match_ms":1834200, "ball":{"x":38.2,"y":22.5,"z":0.1},
  "players":[{"player_id":"NHR-08","x":38.0,"y":22.1}] }
```

### 6.4 Insight
```json
{ "insight_id":"i-0091", "match_id":"SYN-2026-0001", "match_ms":3921000, "type":"momentum_shift",
  "salience":0.82, "entities":{"teams":["SVC"],"players":["SVC-06"]},
  "headline_key":"momentum_shift.press_intensified",
  "evidence":[
    {"metric":"ppda","team":"SVC","from":14.2,"to":6.1,"window_min":8},
    {"metric":"field_tilt","team":"SVC","from":0.38,"to":0.61},
    {"metric":"win_prob","team":"SVC","from":0.21,"to":0.29}],
  "why_tags":["pressure_change","territory_swing"], "related_events":["e-000880","e-000893"] }
```
**Insight types:** `goal, big_chance, xg_swing, win_prob_swing, momentum_shift, pressure_change, control_chaos_flip, rhythm_break, pass_of_note, shot_speed_record, sprint_threshold, distance_milestone, career_milestone, player_streak, tactical_note`.

### 6.5 Overlay cue: see §12.

### 6.6 Profile
```json
{ "profile_id":"p-casual-es", "persona":"casual", "language":"es",
  "favorite_clubs":["SVC"], "favorite_players":["SVC-10"], "focus_metric":"pass_difficulty",
  "player_focus_mode":{"enabled":false,"player_id":null},
  "spoiler_safe":false, "verbosity":"short", "max_overlays_per_min":3 }
```

### 6.7 Narration
```json
{ "narration_id":"n-0091-p-casual-es", "insight_id":"i-0091", "profile_id":"p-casual-es",
  "lang":"es", "text":"¡Southvale aprieta arriba y el partido cambia de dueño!", "source":"llm|template",
  "grounding":{"passed":true,"numbers_checked":0}, "latency_ms":1240 }
```

---

## 7. Synthetic data generator (`sim/`), the "data innovation" deliverable

**Rosters.** There are 20 fictional clubs and 25 players each, with attributes for pace, passing, finishing, work rate and aggression. Each player also has synthetic career stats (appearances, goals) so milestones can fire, e.g. a 100th appearance or a 50th goal. Names are diverse and fictional, and there are no real club names, badges or colours copied from PL clubs.

**Match engine: possession-chain model.**
- State: ball position zone (a 12×8 grid), possessing team, phase (build-up, progression, final third, transition, set piece).
- Each step picks an action (pass, carry, dribble, shot, or loss) from a transition matrix that depends on zone, phase, player attributes, opponent pressure intensity and game state. A losing team in the last 15 minutes gets more risk.
- Pass target: sampled from teammates' formation-anchored positions plus noise. Completion is Bernoulli with probability from the pass model in §8.1. This makes the data internally consistent with our own metrics.
- Shots: outcome sampled from xG (§8.2), adjusted by the finisher's attribute. Shot speed is drawn from N(95, 15) km/h scaled by finishing and distance.
- Defensive actions: pressures generated with probability set by the defending team's `press_intensity` parameter, which storylines can change.
- Tracking: players move toward formation anchors shifted by ball position. Speeds are capped by the pace attribute and sprint bursts happen on transitions. Positions are sampled at 5 Hz, and speeds and distances are derived from them.
- The clock advances from action durations plus stoppages, giving about 1,600–2,000 events per match, which is realistic.

**Storylines (seeded scripts that guarantee demo moments)**
| Storyline | Script |
|---|---|
| `comeback` | Home leads 2–0 at 60'; away raises `press_intensity` at 62'; goals at 71', 84', 90+3' |
| `red_card_turn` | Red card at 38' changes formation and momentum; underdog holds on |
| `pressure_surge` | Clean PPDA collapse at 55' to exercise the CUSUM detector |
| `star_player` | One player hits a 34 km/h sprint, a 30 m pass with difficulty 85+, and a career milestone |
| `chaos_derby` | High fouls and turnovers that flip the Control/Chaos index several times |

**CLI**
```
python -m pitchlens.sim.cli generate --seed 42 --storyline comeback --out data/matches/
python -m pitchlens.sim.cli replay data/matches/SYN-...jsonl --speed 10 --bus memory
```
**Dataset deliverable:** 50 generated matches (JSONL + tracking parquet) plus `docs/dataset.md` (schema, generation method, known limits, license CC-BY).

---

## 8. Interpret layer: metrics catalog (`interpret/`)

| # | Metric | Definition |
|---|---|---|
| 8.1 | **Pass distance** | Euclidean distance from start to end (m) |
| | **Pass accuracy** | completed / attempted, per player and team, overall and in a rolling 5-min window |
| | **Pass difficulty (0–100)** | `100 × (1 − P(complete))`, where `P = σ(β0 + β1·dist + β2·forward_angle + β3·under_pressure + β4·lines_broken + β5·aerial + β6·into_box)`. Coefficients are hand-set first, then refit on simulator output with logistic regression |
| | Lines broken | Count of opposition "lines" (defenders, midfielders by x-band) the pass crosses |
| 8.2 | **xG** | `σ(α0 + α1·dist_to_goal + α2·goal_angle + α3·header + α4·under_pressure + α5·one_on_one)`; goal_angle = angle subtended by the posts |
| | **Shot speed** | Initial ball speed (km/h) from the event, or from the first two tracking frames after the shot |
| | **Ball speed** | Smoothed `‖Δpos‖/Δt` of ball frames; max and current per action |
| 8.3 | **Distance covered** | Σ‖Δpos‖ per player |
| | **Top speed / sprints** | Smoothed speed; sprint = > 25.2 km/h for ≥ 1 s; high-speed run = > 19.8 km/h |
| | **Threshold triggers** | Configurable: `top_speed > 33 km/h`, `distance crosses 10 km`, `sprint_count crosses 30` → `sprint_threshold` / `distance_milestone` |
| 8.4 | **Possession %** | Time-weighted by possession_id durations; overall + rolling 5 min |
| | **Field tilt** | Team final-third touches ÷ all final-third touches (rolling 10 min) |
| | **PPDA** | Opponent passes in their defensive 60% ÷ (tackles + interceptions + fouls + pressures-to-regain) in that zone (rolling 10 min) |
| | **Momentum** | Exponentially decayed (half-life 3 min) threat score: final-third entries ×0.2 + box entries ×0.5 + xG ×3; home − away, in [−1, 1] |
| 8.5 | **Control ↔ Chaos index (0–100)** | `z`-weighted: + mean passes per possession, + pass completion, − turnovers/min, − transitions/min, − fouls & cards/min, − sequence-length variance; squashed to 0 (chaos) – 100 (control). Each component is exposed as evidence |
| 8.6 | **Rhythm / tempo** | Events per min, passes per possession, stoppage share; a **rhythm break** fires when 5-min tempo deviates > 1.5σ from the match baseline |
| 8.7 | **Win probability** | Remaining goals per team ~ Poisson(λ·t_rem/90). λ blends pre-match strength with live xG rate (weight grows with minutes played) and is multiplied ×0.7 / ×1.2 on red cards. P(win/draw/loss) is summed over a 0–8 goal grid. Recomputed per event |

All metrics live in pure functions with no I/O, so they can be unit-tested. `windows.py` provides the shared rolling-window accumulators.

---

## 9. Explain layer (`explain/`)

**Detectors.** Each one is a function `(state, enriched_event) → list[Insight]`, with cooldowns to prevent spam.
| Detector | Fires when | Evidence attached |
|---|---|---|
| goal / big_chance | goal event; shot xG ≥ 0.35 not scored | xG, shot speed, assist pass difficulty, WP before/after |
| xg_swing | cumulative xG lead changes sign or gap moves ≥ 0.8 in 10 min | xG totals, window |
| win_prob_swing | \|ΔWP\| ≥ 0.10 from one event | WP from/to, cause event |
| momentum_shift | momentum crosses 0 and holds 3 min | momentum series, field tilt |
| pressure_change | CUSUM change-point on PPDA or field tilt (`changepoint.py`) | PPDA from/to, window |
| control_chaos_flip | index crosses 40 or 60 and holds 4 min | component breakdown |
| rhythm_break | tempo z-score > 1.5 | tempo from/to, stoppage share |
| pass_of_note | difficulty ≥ 80 completed, or ≥ 3 lines broken | distance, difficulty, lines broken |
| shot_speed_record | fastest shot of match / > 110 km/h | speed, previous record |
| sprint_threshold / distance_milestone | §8.3 thresholds | speed / distance |
| career_milestone | event takes a player across a career stat boundary | stat, count |
| player_streak | e.g. 15 consecutive completed passes, 3 tackles won in 5 min | streak data |

**Salience.** `salience = type_weight × (1 + 2·|ΔWP|) × rarity × game_state_mult × recency_decay`, clipped to [0, 1]. `type_weight` comes from a config table. `rarity` = 1 + percentile rarity versus the match baseline. `game_state_mult` is higher late in close games. Personalization applies its own multiplier afterwards (§11).

**"Why it matters."** Every insight gets `why_tags` (e.g. `pressure_change`, `territory_swing`, `game_state_critical`, `record`) that map to explanation templates. The evidence is the explanation, and the LLM only verbalizes it.

---

## 10. Narrative layer (`narrate/`)

**Provider interface**
```python
class LLMProvider(Protocol):
    async def generate(self, system: str, user: str, *, max_tokens: int, temperature: float,
                       model_tier: Literal["fast","quality"]) -> str: ...
```
Implementations: `MockProvider` (fills templates deterministically), `AzureOpenAIProvider`, `FoundryClaudeProvider`. Selected with `LLM_PROVIDER`. `fast` tier is for live lines and `quality` tier for recaps.

**Two-speed output**
1. **Template text, instant.** `templates/{lang}.yaml` keyed by `headline_key` with slots filled from evidence. This is always available and renders in < 50 ms.
2. **LLM text, async.** A prompt containing persona, language, glossary terms, favorite entities and the evidence as JSON. When the result arrives it replaces the template text in the UI.

**Prompt contract (system prompt summary).** "You are a football broadcaster. Use ONLY facts in EVIDENCE. Do not introduce numbers, names or events not present. Persona: {casual | analyst | tactician}. Language: {lang}, use glossary terms. Max {n} words. Return JSON {text, emphasis_entities}."

**Grounding validator (`grounding.py`).** It extracts all numerals and number words in each language, normalizes units and percentages, and checks each against the evidence values (with tolerance for rounding). It also checks that player and team names are in `entities`. If the text fails, it retries once at lower temperature and then falls back to the template text. Pass rate is logged to App Insights.

**Cache.** The key is `(insight_id, persona, language, favorite-hash)`, stored in memory with Cosmos write-through. A worker pool bounds concurrency and drops low-salience jobs when the queue backs up (backpressure).

**Recaps (`recap.py`)**
- **HT / FT recap:** the top-N insights by salience plus the score, xG, possession and WP trajectory, sent to the quality-tier LLM to write a 120/250-word recap per persona and language, grounded the same way.
- **Player of the match:** a composite score (goal contributions + xG/xA + pass difficulty sum + defensive actions + distance), with a narrative that cites the evidence.
- **Personal recap:** a "Your match" recap centred on the profile's favorite club or player.

**Languages (launch set):** en, es, fr, pt, ar (RTL check in the UI), hi. The glossary YAML maps canonical terms such as `pressing`, `through ball` and `xG` to their idiomatic forms.

---

## 10A. Agent layer: multi-agent orchestration (`agents/`, `mcp/`)

**Design rule: a deterministic hot path with an agentic warm path.** Overlay cues never wait on an agent. The deterministic pipeline (§3) emits template-backed cues in < 300 ms. Agents run alongside it to add reasoning, wording, curation and conversation, so that live play stays responsive while the agent layer does the work agentic judging looks for.

**Framework:** **Microsoft Agent Framework (Python)** for agent definitions, tools, multi-agent workflows, thread memory and OpenTelemetry tracing. Models come from **Microsoft Foundry**: a fast tier for live work and a quality tier for recaps. The model client stays pluggable (`mock` / Azure OpenAI / Claude on Foundry). *Pin the exact package version and API at T-120 against current docs; Agent Framework is moving fast.*

### Agents
| Agent | Role | Tools | Model tier |
|---|---|---|---|
| **Analyst Agent** | Looks at rolling windows every ~2 min and at every high-salience insight. Proposes **tactical_note** insights the rule detectors can't express ("the away team is overloading the left half-space: 9 of their last 12 entries"). Every claim must cite a tool result | `get_metric`, `get_window`, `get_pass_network`, `get_zone_entries`, `compare_periods`, `win_prob_at`, `what_if` | fast |
| **Narrator Agent** | Turns an Insight plus a persona into commentary. One call per persona-language group (concurrent fan-out) | glossary lookup, `get_player_profile` | fast |
| **Localization Agent** | Transcreation rather than literal translation: idioms, glossary terms, RTL-safe text, number formats per locale. Used when the Narrator writes in a pivot language for rare locales | glossary, locale formatter | fast |
| **Fact-Checker Agent** (critic) | **Reflection loop**: runs the deterministic `grounding.validate` tool, then judges whether the claims follow from the evidence. Returns `approve`, `revise` (with reasons) or `reject` (fall back to the template). At most 1 revision round on the live path, 2 for recaps | `grounding.validate`, `get_evidence` | fast |
| **Director Agent** (broadcast producer) | Curates what goes on air: picks which cues each feed shows, resolves clashes, paces density, holds stories for replays and breaks. **Human-in-the-loop**: in `/studio` the producer can switch between `auto` and `approve` mode, and approve or reject the Director's proposals | `list_pending_cues`, `schedule_cue`, `hold_cue`, `get_match_phase` | fast |
| **Editor Agent** (recaps & pundit sheet) | **Group chat**: the Editor plans an outline, asks the Analyst for supporting stats, has the Narrator draft per persona and language, and has the Fact-Checker approve. Produces HT/FT recaps, player of the match, the pundit sheet and chapter titles | all analyst tools + `get_top_insights` | quality |
| **Fan Companion Agent** ("Ask the Match") | Chat for each fan with **memory of their profile** (club, player, language, persona). **Hands off** to the Analyst for deep stats questions and to the Editor for "recap my team's game". Input and output pass through **Azure AI Content Safety** | handoff + analyst tools + `get_profile` | fast |

### Orchestration patterns used (rubric #2)
| Pattern | Where |
|---|---|
| **Event-driven workflow** | Insight stream → workflow trigger (Agent Framework workflow) |
| **Sequential** | Analyst → Narrator → Fact-Checker → Director, for agent-authored tactical notes |
| **Concurrent fan-out / fan-in** | Narrator × N persona-language groups per insight, results gathered by the Director |
| **Reflection / critic** | Fact-Checker approve, revise or reject loop |
| **Handoff** | Fan Companion → Analyst / Editor |
| **Group chat (manager-led)** | Editor-led recap authoring |
| **Human-in-the-loop** | Director approval mode in `/studio` |
| **Tool use over deterministic code** | Every number comes from a tool, never from the model |

### PitchLens MCP server (`mcp/`)
The analyst tools are also exposed as a **Model Context Protocol server** (`pitchlens-mcp`): `list_matches`, `get_match_state`, `get_insights`, `get_metric`, `get_player_card`, `what_if`, `export_cues`. Any external agent, such as **GitHub Copilot**, a Foundry agent or Claude, can then plug into live match intelligence. It runs as a separate Container App over streamable HTTP. This is our "platform, not just an app" story for real-world impact.

### Agent observability & safety
- Agent Framework OpenTelemetry → App Insights / Foundry tracing. Every agent run, tool call, token count and latency is visible.
- `/studio` **Agent Trace panel**: a live timeline showing which agent did what, with which tool results, and the Fact-Checker's verdict. This is shown in the demo.
- Budgets: a max-tokens and max-tool-calls cap per run, a per-match cost cap, and a circuit breaker that falls back to `mock`/template output if the provider errors.
- **Azure AI Content Safety** on all fan-supplied text (Companion questions, profile nicknames) and on generated output before display.
- Prompt-injection guard: fan input is never placed in system prompts, tool arguments are schema-validated, and the agents have no write tools except the Director's cue scheduling, which runs within scheduler rules.

### Agent evaluation (extends T-101)
A scripted suite: 30 Companion questions with expected cited metrics, 50 insights checked for Fact-Checker precision and recall against seeded hallucinations, and an analysis of how Director pacing compares with the rate limits. Results go in `docs/eval.md`.

---

## 11. Personalization layer (`personalize/`)

| Persona | Gets | Tone | Overlay density |
|---|---|---|---|
| **casual** | Goals, big moments, favorite-player moments, simple "who's on top" bar | Emotional, short, no jargon | Low (≤ 2/min) |
| **analyst** | All metrics, evidence tables, WP/xG charts, pass difficulty | Precise, numeric | High |
| **tactician** | Pressure changes, control/chaos, shape and rhythm notes, pass networks | Explanatory, tactical vocabulary | Medium |

**Ranker.** `personal_salience = salience × persona_type_weight[type] × (1.6 if favorite club involved) × (2.0 if favorite player) × (1.5 if insight touches focus_metric)`. Each persona has its own threshold for emitting an overlay or feed item. If `spoiler_safe` is on, score and goal content is suppressed until the user taps to reveal.

**Player-focus mode (`player_focus.py`).** It keeps a running card for one player: touches, passes and average difficulty, distance, top speed, heat zone, involvement timeline, and the latest moment. That player's insights get a ×3 boost, and the overlay shows a persistent `PlayerTag` with live speed when they're near the ball.

**Focus metric mode.** The viewer picks one metric (pass difficulty, top speed, xG, PPDA, control/chaos). A persistent mini-widget tracks it, and the feed prioritizes insights that touch it.

---

## 12. Render layer: overlay cue contract (`render/`)

```json
{ "cue_id":"c-0310", "insight_id":"i-0091", "profile_id":"p-analyst-en",
  "start_ms":3921400, "duration_ms":6000, "priority":70,
  "region":"lower_third|top_left|top_right|player_anchor|full_bar",
  "template":"pass_card|shot_speed|player_tag|momentum_bar|lower_third|chaos_meter|wp_swing",
  "anchor":{"player_id":"SVC-06"},
  "data":{"distance_m":31.4,"difficulty":86,"lines_broken":3},
  "text":{"lang":"en","headline":"Line-breaking pass","body":"..."},
  "transitions":{"in":"slide_up","out":"fade"} }
```
**Scheduler rules:** one cue per region at a time. A higher priority cue preempts a lower one, and the lower one is shortened or dropped. A per-profile `max_overlays_per_min` applies. Goals always preempt. Cues are timed to `match_ms` so a renderer can line them up with the video clock. A JSON Schema is published in `docs/overlay-contract.md` for partner renderers.

---

## 13. Frontend applications (`web/`)

| Route | Audience | Contents |
|---|---|---|
| `/overlay?match=&profile=` | Broadcast / OBS browser source | Transparent background, 1920×1080 safe areas, renders cues over an optional 2D pitch backdrop (`?pitch=1`) |
| `/watch?match=&profile=` | Fan second screen (mobile-first) | Profile picker (persona, language, club, player, focus metric), live feed, PitchCanvas, cards, player-focus panel, "Your recap" |
| `/analyst?match=` | Analysts | WP chart, xG timeline, momentum bar, pass network, PPDA and field-tilt charts, control/chaos breakdown, evidence inspector per insight |
| `/studio?match=` | Producer | Event timeline, insight queue (auto / manual approve), overlay preview per profile, replay controls (seed, storyline, speed), HT/FT recap generation, latency HUD |
| `/compare?match=&a=&b=` | **Demo money shot** | Two profiles side by side on the same stream |
| `/matchday?id=` | Fans following several games | 3 live mini-pitches, cross-match alerts, live league table (P2) |
| `/studio/pundit` | Studio presenters | Pre-match / HT / FT talking-points sheet, print view (P1) |

**Shared components:** `PitchCanvas` (canvas, 5 Hz frames interpolated to 60 fps), `PlayerTag`, `PassCard`, `ShotSpeedCard`, `MomentumBar`, `LowerThird`, `ChaosMeter`, `WinProbChart`, `PassNetwork`, `InsightFeed`, `ProfilePicker`. `lib/clock.ts` syncs a client match clock to server `state.match_ms` with drift correction. `lib/sse.ts` handles reconnecting SSE with `Last-Event-ID` resume.

**Design:** a dark broadcast palette with club colours injected from match metadata, large legible type for TV, RTL support for Arabic, and `prefers-reduced-motion` respected.

---

## 14. API specification (FastAPI, `/api/v1`)

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness + provider/bus status |
| `GET /matches` | List generated/archived matches |
| `POST /matches` | Generate `{seed, storyline, home, away}` → match_id |
| `POST /matches/{id}/replay` | Start replay `{speed: 1|10|60, from_ms}` |
| `POST /matches/{id}/control` | `pause | resume | seek` |
| `GET /matches/{id}/state` | Current MatchState snapshot (score, clock, metrics) |
| `GET /matches/{id}/insights?since_ms=` | Insights list |
| `GET /matches/{id}/stream?profile_id=` | **SSE**: events `state` (1 Hz), `event`, `insight`, `cue`, `narration`, `frame` (5 Hz, optional `?frames=1`) |
| `POST /matches/{id}/recap` | `{kind: ht|ft|player_of_match|personal, profile_id}` → narration |
| `GET /matches/{id}/players/{pid}/focus` | Player-focus card |
| `GET/PUT /profiles/{id}` · `POST /profiles` | Profile CRUD |
| `POST /matches/{id}/ask` | "Ask the Match": `{question, profile_id}` → streamed grounded answer with evidence citations |
| `GET /matches/{id}/whatif/{insight_id}` | Counterfactual WP for a moment |
| `GET /matches/{id}/export/{chapters|captions|cues}?lang=` | WebVTT chapters / captions, full cue JSON |
| `GET /narrations/{id}/audio` | TTS audio (cached MP3/Opus) |
| `GET /matches/{id}/pundit-sheet?stage=pre|ht|ft` | Studio talking points |
| `GET /matchday/{id}/stream?profile_id=` | Multi-match SSE + cross-match alerts + league table |
| `POST /ingest` | External producers (CV POC, partners) push `events.v1` batches |
| `GET /schema/{name}` | JSON Schema for events/insights/cues/profiles |

---

## 15. Azure deployment (`infra/`, `azd up`)

| Resource | Use |
|---|---|
| Azure Container Apps environment | `engine` (FastAPI, min 1 replica, sticky sessions for SSE) + `agents` (Agent Framework workers, KEDA-scaled on queue depth) + `mcp` (pitchlens-mcp) + `web` (Next.js). Health/readiness probes, revision-based blue/green |
| Azure Container Registry | Images built by GitHub Actions |
| Azure Key Vault | Any non-MI secrets (e.g. third-party keys); referenced by Container Apps secrets |
| Azure AI Content Safety | Fan-input and output moderation |
| *(Optional)* AKS | `docs/deploy-aks.md`: same images and Helm chart, as a scale-out path matching the Reactor reference architecture |
| Azure Event Hubs (Standard, 1 hub `match-events`, 2 partitions) | Ingest bus in cloud mode |
| Azure AI Foundry project / Azure OpenAI | LLM deployments: a fast tier and a quality tier |
| Azure Cosmos DB (serverless, NoSQL) | Containers: `profiles`, `matches`, `insights`, `narrations` |
| Application Insights + Log Analytics | OpenTelemetry traces, per-stage latency |
| Azure AI Speech | TTS commentary per persona/language (P1) |
| Azure Blob Storage | Exported cues/WebVTT, TTS audio cache, dataset download |
| Managed identity | Engine → Event Hubs / Cosmos / Foundry with no secrets in code |

---

## 16. Configuration & environment variables

```
LLM_PROVIDER=mock|azure_openai|foundry_claude
AZURE_OPENAI_ENDPOINT=  AZURE_OPENAI_FAST_DEPLOYMENT=  AZURE_OPENAI_QUALITY_DEPLOYMENT=
FOUNDRY_ENDPOINT=       FOUNDRY_FAST_MODEL=            FOUNDRY_QUALITY_MODEL=
BUS=memory|eventhubs    EVENTHUBS_NAMESPACE=           EVENTHUBS_NAME=match-events
STORE=memory|cosmos     COSMOS_ENDPOINT=               COSMOS_DB=pitchlens
APPLICATIONINSIGHTS_CONNECTION_STRING=
LANGS=en,es,fr,pt,ar,hi
NEXT_PUBLIC_API_BASE=http://localhost:8000/api/v1
```
Local default: `mock` + `memory` + `memory`, so everything runs offline with zero cloud setup.

---

## 17. Latency budget & performance

| Stage | Budget (p95) |
|---|---|
| Bus ingest → MatchState applied | 20 ms |
| Metrics + detectors | 30 ms |
| Ranking + template text + cue scheduling | 20 ms |
| SSE delivery to client | 100 ms |
| **Event → overlay cue on screen** | **< 300 ms** |
| LLM narration (fast tier) | < 3 s |
| FT recap (quality tier) | < 15 s |

Techniques: metrics are pure and incremental (no re-scans), LLM calls happen per (insight × distinct persona-lang group) rather than per user, cache plus backpressure, and at most one in-flight LLM job per insight per group.

---

## 18. Testing & verification

| Layer | Test |
|---|---|
| Schemas | Round-trip JSON, JSON Schema export, TS types compile |
| Simulator | Seed determinism (identical JSONL hash); distribution sanity (passes/match 800–1100, shots 20–30, completion 75–88%, goals avg 2.5–3.0 over 50 matches) |
| Metrics | Hand-built event fixtures: known distances; difficulty monotonic in distance and pressure (hypothesis); xG decreasing with distance; speed from frames; PPDA hand-calculated; WP sums to 1 and → certainty at 90' |
| Detectors | `pressure_surge` storyline must fire `pressure_change` within ±3 min of the scripted change; cooldowns prevent duplicates |
| Personalization | Same match under casual-es vs analyst-en: top-10 insight overlap < 50%, language correct, density within limits |
| Grounding | Full match with real LLM: ≥ 99% narrations pass; failures fall back to the template |
| API/integration | Replay at 60× through memory bus → SSE client receives ordered, gap-free `seq`; resume with Last-Event-ID |
| Latency | Replay at 10×; p95 per §17, read from App Insights in cloud |
| Frontend | Playwright: `/overlay` renders a cue within 500 ms of SSE message; `/compare` shows two different texts for the same insight; RTL snapshot for `ar` |
| Manual E2E | `uvicorn pitchlens.api.main:app --reload` + `npm run dev`; load `/overlay` as an OBS browser source; run the `comeback` storyline; trigger FT recap in `/studio` |

---

## 19. Task breakdown (checklist)
Tracks: **E** = Engine/Data · **A** = AI/Narrative · **F** = Frontend/Overlay · **I** = Infra/DevOps. The first deps column lists blocking tasks.

### Phase 0: Foundations (days 1–2)
- [x] **T-00 (all)** Save this document as `PROJECT_PLAN.md`; `git init`; README skeleton; `.gitignore`; MIT license. *Done when:* repo committed.
- [x] **T-01 (E)** Engine scaffold: `pyproject.toml` (uv), ruff, pytest, package layout. *Done when:* `pytest` runs green on an empty suite.
- [x] **T-02 (F)** Web scaffold: Next.js 16 + TS + Tailwind + ESLint, route stubs. *Done when:* `npm run dev` serves all routes.
- [x] **T-03 (E)** Pydantic schemas (§6) + JSON Schema export + TS type generation. Deps: T-01, T-02. *Done when:* `web/lib/types.ts` is generated and compiles.

### Phase 1: Data & ingest (days 2–5)
- [ ] **T-10 (E)** Rosters: 20 fictional clubs × 25 players, attributes, career stats. *Done when:* YAML/JSON in `data/rosters/`, validated.
- [ ] **T-11 (E)** Simulator core: possession-chain model, event emission. Deps: T-03, T-10. *Done when:* a seeded match produces 1,600–2,000 valid events.
- [ ] **T-12 (E)** Tracking generator (5 Hz) consistent with events. Deps: T-11. *Done when:* ball position matches event locations within 1 m.
- [ ] **T-13 (E)** Storylines (5 from §7). Deps: T-11. *Done when:* each storyline's scripted moments appear in output.
- [ ] **T-14 (E)** Bus protocol + memory bus + replay (1/10/60×, pause/seek). Deps: T-03. *Done when:* replay emits in order at the requested speed.
- [ ] **T-15 (E)** Dataset: generate 50 matches + `docs/dataset.md` + distribution sanity test. Deps: T-12, T-13.

### Phase 2: Interpret (days 4–8)
- [ ] **T-20 (E)** MatchState (clock, score, possession, cards, subs, per-player accumulators). Deps: T-11.
- [ ] **T-21 (E)** Pass metrics (distance, accuracy, lines broken, difficulty) + fit coefficients. Deps: T-20.
- [ ] **T-22 (E)** Shot metrics (xG, shot speed, ball speed). Deps: T-20, T-12.
- [ ] **T-23 (E)** Physical metrics + threshold triggers. Deps: T-12.
- [ ] **T-24 (E)** Rolling windows, possession %, field tilt, PPDA, momentum. Deps: T-20.
- [ ] **T-25 (E)** Control/Chaos index + rhythm/tempo. Deps: T-24.
- [ ] **T-26 (E)** Win probability model. Deps: T-22.
- *Done when (Phase 2):* all §18 metric tests pass; `docs/metrics.md` written.

### Phase 3: Explain (days 7–10)
- [ ] **T-30 (E)** Detector framework + cooldowns + evidence builder. Deps: T-20.
- [ ] **T-31 (E)** All detectors from §9 (incl. CUSUM change-point, milestones). Deps: T-21…T-26, T-30.
- [ ] **T-32 (E)** Salience scoring + config table. Deps: T-31. *Done when:* storyline tests fire the expected insights with sensible salience ordering.

### Phase 4: API & streaming (days 5–9, parallel)
- [ ] **T-40 (E)** FastAPI app, routes §14, SSE stream with Last-Event-ID, in-memory store. Deps: T-14, T-20.
- [ ] **T-41 (E)** Pipeline orchestrator: bus → state → metrics → detectors → personalization → cues → SSE fan-out per profile. Deps: T-32, T-40.
- [ ] **T-42 (E)** OpenTelemetry spans per stage. Deps: T-41.

### Phase 5: Narrative & personalization (days 8–13)
- [ ] **T-50 (A)** LLMProvider interface + MockProvider. Deps: T-03.
- [ ] **T-51 (A)** Azure OpenAI provider + Foundry Claude provider (fast/quality tiers). Deps: T-50.
- [ ] **T-52 (A)** Template text packs for 6 languages + glossary YAML. Deps: T-31.
- [ ] **T-53 (A)** Persona prompts (casual/analyst/tactician) + JSON output parsing. Deps: T-50.
- [ ] **T-54 (A)** Grounding validator (multilingual numerals, entity check) + retry/fallback. Deps: T-53. *Done when:* adversarial test texts with invented numbers are rejected.
- [ ] **T-55 (A)** Narration worker: cache, concurrency limit, backpressure. Deps: T-54, T-41.
- [ ] **T-56 (A)** Profiles + ranker + spoiler-safe + focus metric. Deps: T-32.
- [ ] **T-57 (A)** Player-focus mode card + boost. Deps: T-56, T-23.
- [ ] **T-58 (A)** Recaps: HT, FT, player of the match, personal recap. Deps: T-54.

### Phase 6: Render & frontend (days 6–15)
- [ ] **T-60 (E)** Overlay cue generator + scheduler (regions, priority, preemption, rate limit). Deps: T-56.
- [ ] **T-61 (F)** `lib/sse.ts`, `lib/clock.ts`, `lib/i18n.ts` (incl. RTL). Deps: T-03.
- [ ] **T-62 (F)** PitchCanvas with 60 fps interpolation. Deps: T-61.
- [ ] **T-63 (F)** Overlay components: PlayerTag, PassCard, ShotSpeedCard, MomentumBar, LowerThird, ChaosMeter, WP swing. Deps: T-61.
- [ ] **T-64 (F)** `/overlay` route (transparent, OBS-ready). Deps: T-60, T-63.
- [ ] **T-65 (F)** `/watch` fan app + ProfilePicker + player-focus panel + personal recap. Deps: T-56, T-57.
- [ ] **T-66 (F)** `/analyst` charts + evidence inspector. Deps: T-24…T-26.
- [ ] **T-67 (F)** `/studio` console (replay controls, insight queue, recap triggers, latency HUD). Deps: T-40, T-58.
- [ ] **T-68 (F)** `/compare` side-by-side view. Deps: T-65.

### Phase 7: Azure (days 12–17)
- [ ] **T-70 (I)** Bicep modules + `azure.yaml`; Dockerfiles for engine and web. *Done when:* `azd up` deploys from clean.
- [ ] **T-71 (I)** Event Hubs bus implementation + config switch. Deps: T-14.
- [ ] **T-72 (I)** Cosmos store implementation (profiles, matches, insights, narrations). Deps: T-40.
- [ ] **T-73 (I)** Foundry/OpenAI deployments + managed identity wiring. Deps: T-51.
- [ ] **T-74 (I)** App Insights dashboard: per-stage latency, grounding pass rate, LLM cost. Deps: T-42.

### Phase 8: Quality, demo, submission (days 15–21)
- [ ] **T-80 (all)** Full test pass per §18; latency run; fix regressions.
- [ ] **T-81 (all)** Docs: README quickstart, architecture diagram, `overlay-contract.md`, `schemas.md`.
- [ ] **T-82 (all)** Demo script rehearsal + 3-min video recording (§21).
- [ ] **T-83 (all)** Submission (§24).
- [ ] **T-84 (all)** Pitch / description (`docs/pitch.md`, draft in §21A): what it is, Microsoft & Azure tech used, what it does, what problem it solves.

### Phase 7B: Production readiness (runs alongside Phase 7)
- [ ] **T-75 (I)** GitHub Actions: lint (ruff, eslint), type-check (mypy/pyright, tsc), tests, Docker build → ACR, `azd deploy` on main; gitleaks, CodeQL, Dependabot. *Done when:* a green pipeline deploys main to Azure.
- [ ] **T-76 (I)** Content Safety integration (`agents/safety.py`) on fan input and generated output. *Done when:* a harmful test question is blocked with a friendly message.
- [ ] **T-77 (I)** Hardening: Key Vault, rate limiting on public endpoints, CORS allow-list, cost caps, health probes, `SECURITY.md`, `CODE_OF_CONDUCT.md`, responsible-AI note in `docs/responsible-ai.md`.

### Phase 10: Agent layer (§10A), days 6–16, owned by track A
- [ ] **T-120 (A)** Agent Framework setup: pin version, Foundry model clients (fast/quality) behind the pluggable provider, `mock` client for tests, tracing → App Insights. Deps: T-50. *Done when:* a hello-world agent with one tool runs locally (mock) and in Azure.
- [ ] **T-121 (A)** Analyst tools (`agents/tools.py`) wrapping interpret/explain functions with typed schemas. Deps: T-26, T-31.
- [ ] **T-122 (A)** Analyst Agent + periodic and salience-triggered runs → `tactical_note` insights with citations. Deps: T-121.
- [ ] **T-123 (A)** Narrator + Localization agents with concurrent fan-out per persona-language group. Deps: T-120, T-52. *Replaces the direct-LLM path in T-55; the template fallback is kept.*
- [ ] **T-124 (A)** Fact-Checker reflection loop (approve / revise / reject) using `grounding.validate`. Deps: T-54, T-123. *Done when:* ≥ 95% of seeded hallucinations are caught and < 5% of good narrations are rejected.
- [ ] **T-125 (A/F)** Director Agent + `/studio` approve/auto mode. Deps: T-60, T-67.
- [ ] **T-126 (A)** Editor Agent group chat for HT/FT recap, player of the match, pundit sheet and chapter titles. Deps: T-58, T-122, T-124. *Supersedes the single-call recap in T-58.*
- [ ] **T-127 (A/F)** Fan Companion Agent with handoff, profile memory and Content Safety. Deps: T-121, T-76. *This is T-91, implemented as an agent.*
- [ ] **T-128 (E)** `pitchlens-mcp` server + Container App + README snippet for connecting from GitHub Copilot / VS Code. Deps: T-121.
- [ ] **T-129 (F)** `/studio` Agent Trace panel (live agent/tool timeline + Fact-Checker verdicts). Deps: T-120, T-67.
- [ ] **T-130 (A)** Agent eval suite (§10A) → `docs/eval.md`. Deps: T-124, T-127.

### Phase 9: Plus features (§23), worked in parallel from mid-week 2
**P1**
- [ ] **T-90 (A)** TTS commentary: Azure AI Speech provider, per-language neural voice map, SSML emphasis on goals/WP swings, audio cache; `/watch` toggle + `/overlay?audio=1`. Deps: T-55. *Done when:* goal narration plays in es and en within 4 s of the event.
- [ ] **T-91 (A)** "Ask the Match" agent: tool schemas over metric/insight functions, provider tool-use loop, grounded citations, chat panel in `/watch` + `/analyst`. Deps: T-51, T-41. *Done when:* 10 scripted questions answered correctly with cited evidence.
- [ ] **T-92 (E)** Counterfactual WP (`explain/counterfactual.py`) for big chances, saves and red cards; added as evidence + "What if" card. Deps: T-26, T-31.
- [ ] **T-93 (E/F)** WebVTT chapters + caption export per language, full-match cue JSON export, `GET /matches/{id}/export/{chapters|captions|cues}`. Deps: T-58, T-60.
- [ ] **T-94 (A/F)** Studio pundit sheet: pre-match preview, HT talking points, FT talking points; print view in `/studio`. Deps: T-58, T-10.
- [ ] **T-95 (F)** Shareable moment cards via `next/og`, per language and club colours. Deps: T-65.

**P2**
- [ ] **T-96 (E)** Fatigue model in simulator + fatigue-alert detector. Deps: T-12, T-23.
- [ ] **T-97 (E)** Duel tracker detector + narration templates. Deps: T-31.
- [ ] **T-98 (E/F)** Matchday mode: 3 concurrent matches, cross-match personalized alerts, live synthetic league table. Deps: T-41.
- [ ] **T-99 (A)** Personas: kids, fantasy (synthetic fantasy points), accessibility. Deps: T-56.
- [ ] **T-100 (A)** Narrative arcs tracker threaded into feed and recaps. Deps: T-58.
- [ ] **T-101 (A)** LLM eval harness (grounding, persona-fit, language quality via LLM-as-judge) + results in `docs/eval.md`. Deps: T-54.

**Stretch (Tier S)**
- [ ] **T-110** CV auto-eventing POC → `POST /ingest`.
- [ ] **T-111** Fan prediction mini-game.
- [ ] **T-112** Extra languages (de, it, ja, id, sw).

---

## 20. Team split & timeline

| Week | Engine/Data (E) | AI/Narrative (A) | Frontend/Overlay (F) — also owns Infra (I) |
|---|---|---|---|
| 1 (days 1–7) | T-00, T-01, T-03, T-10–T-15, T-20–T-24 | T-50, T-52, T-53, T-56, **T-120** | T-02, T-61–T-63 on mock data; T-70 + T-75 (CI/CD + first `azd up` early to flush out quota issues) |
| 2 (days 8–14) | T-25, T-26, T-30–T-32, T-40–T-42, T-60, T-92, **T-121, T-128** | T-51, T-54, T-57, **T-122, T-123, T-124**, T-90 | T-64–T-67, T-71–T-73, T-76 |
| 3 (days 15–21) | T-93, T-96, T-80 | **T-125, T-126, T-127, T-130**, T-94 | T-68, **T-129**, T-74, T-77, T-95, T-81–T-84 |

**Priority order when cutting (judging-weighted):** Core pipeline > agent layer (T-120–T-130) > P1 > production readiness polish > P2 > stretch. Of P2, the remaining items (T-97 duels, T-98 matchday, T-99 personas, T-100 arcs) are pulled in only if a track finishes early.

**Milestones (gates)**
- **Day 5:** a seeded match replays through the memory bus, and the raw feed and pitch animate in `/watch`. CI is green.
- **Day 6 (Wed 14 Oct):** attend the Reactor AKS/ACA session; reconcile `infra/` with its reference architecture.
- **Day 10:** insights + personalization + overlay cues work end to end locally; Narrator, Fact-Checker and the reflection loop work with the real model.
- **Day 14:** deployed on Azure via CI; Analyst, Companion and MCP live; TTS working. **Feature freeze for Core.**
- **Day 18:** Director + Editor + Agent Trace panel done; P1 done; anything unfinished gets cut, not rushed.
- **Days 19–21:** test + eval pass, docs, pitch, < 2-min video, submission. No new features.

---

## 21. Demo script (**1:55, hard limit < 2:00**)
Recorded from the **live Azure deployment** (show the URL bar once). No music, or our own track only. Fictional clubs only. Captions burned in. Each beat maps to a rubric criterion (R1–R5).

| Time | Beat | Rubric |
|---|---|---|
| 0:00–0:10 | Hook: "Stats tell you *what*. PitchLens tells you *why*, for every fan." Title card with the Azure logo row | R5 |
| 0:10–0:30 | `/overlay` over the pitch at 10×: a player tag at 34 km/h, then a pass card "31 m · difficulty 86 · 3 lines broken"; a latency chip shows ~200 ms | R4, R5 |
| 0:30–0:55 | 62': the **Analyst Agent** flags that the away press intensified (PPDA 14→6) and WP moved 21%→29%. Cut to the `/studio` **Agent Trace**: Analyst → Narrator → Fact-Checker (one revision caught) → Director approves | **R2** |
| 0:55–1:20 | `/compare`: the equaliser shown to a casual Spanish fan of the away club vs an English analyst. Same insight, two experiences. Spanish voice commentary plays | R4, R5 |
| 1:20–1:35 | Ask the Match: "Why did Southvale take over?" Handoff to the Analyst; cited answer + What-if card | R2 |
| 1:35–1:45 | FT recap in Hindi (Editor group chat), WebVTT chapters in a player; GitHub Copilot querying `pitchlens-mcp` | R2, R3 |
| 1:45–1:55 | Architecture card (Foundry · Agent Framework · ACA · Event Hubs · Cosmos · Speech · Content Safety), eval stats, repo URL | R1, R3 |

## 21A. Pitch / description (draft for T-84)
> **PitchLens** is a multi-agent match-intelligence engine built on Azure. It turns synthetic football events into explainable insights, live narratives, overlays and recaps, personalized for every fan. A deterministic pipeline on **Azure Container Apps** and **Event Hubs** computes pass difficulty, shot and sprint speeds, xG, win probability, pressure changes and a control-vs-chaos index in under 300 ms. Seven agents built with the **Microsoft Agent Framework** on **Microsoft Foundry** models then analyze, narrate, localize, fact-check, direct the broadcast and answer fans' questions. Every number is grounded in tool output, and a Fact-Checker agent rejects anything that isn't. The same moment becomes an emotional Spanish voice line for a casual fan and an evidence-rich breakdown for an analyst, while broadcasters get a timed overlay cue feed and WebVTT chapters. **Azure AI Speech** voices the commentary, **Azure AI Content Safety** guards fan chat, **Cosmos DB** stores profiles, and **App Insights** traces every agent step. An MCP server lets any agent, including **GitHub Copilot**, query the match live. It solves broadcast's one-size-fits-all problem: every match feels made for every fan, in any market.

---

## 22. Risks & mitigations
| Risk | Mitigation |
|---|---|
| LLM latency or rate limits during the live demo | Template-first rendering; cache; batch by persona-lang group; `mock` fallback switch |
| LLM hallucinated numbers | Grounding validator + template fallback |
| Synthetic data looks unrealistic | Distribution sanity tests calibrated to public aggregate norms; storylines for drama |
| Scope creep with 3 people | Phases gated by milestones; stretch tasks only after T-82 |
| Azure credits / quota | Develop on `mock` + memory locally; deploy late in week 2; serverless Cosmos |
| IP / branding | Fictional clubs, players and colours only; no PL marks or real data |
| SSE through proxies | Container Apps ingress supports streaming; heartbeat every 15 s |
| Agent Framework API churn | Pin the version at T-120; keep agents behind our own thin `agents/clients.py` wrapper; `mock` client for tests |
| Agents slow the live path | Agents are warm-path only; overlays never wait on them; budgets + circuit breaker |
| Video runs over 2 min | Script timed to 1:55; rehearse with a stopwatch; cut beats, not speed |
| Secret leaked in a public repo | Managed identity, Key Vault, gitleaks pre-commit + CI, GitHub secret scanning + push protection |

---

## 23. Expanded feature set ("Plus"): planned into the 3 weeks
These go beyond the minimum brief. Each one reuses the Core pipeline: it is either another consumer of Insights and MatchState, or a cheap extension of the simulator. That's why they fit in 3 weeks. They are tiered so we can cut from the bottom if we fall behind.

**Tier P1: high demo value, low cost (must attempt)**
| Feature | What it does | Builds on |
|---|---|---|
| **AI voice commentary** | Azure AI Speech TTS reads LLM narration per persona/language (neural voices, SSML excitement on goals); toggle in `/watch` and an audio track in `/overlay` | narrate worker, cache |
| **"Ask the Match" agent** | Chat panel: "Why did City take over after 60'?" or "How fast was Reyes' sprint?" The LLM answers by **tool-calling metric functions** (`get_metric`, `get_insights`, `compare_players`, `win_prob_at`) and cites evidence. Answers in the user's language | interpret/, explain/, provider tool-use |
| **What-if / counterfactual** | For every big chance and pivotal moment: "If this 0.41 xG chance had gone in, home win probability would have been 78% instead of 52%." Computed by re-running WP with the hypothetical score. Pure maths, very explainable | win_prob.py |
| **Streaming chapters + captions** | Auto-generated **WebVTT** chapter markers (key moments) and a caption track of narrations per language, so a streaming player can consume them directly. Also exports the timed **cue JSON** for the full match | render/, narrate/ |
| **Studio pundit sheet** | Pre-match preview (form, key duels, storylines to watch, from rosters plus synthetic season stats), half-time "3 talking points", and FT "talking points + stat of the match". Printable or PDF view in `/studio` | recap.py |
| **Shareable moment cards** | One-click PNG cards for socials (score, moment, key stat, club colours) via `next/og`, generated per language | web, insights |

**Tier P2: depth in explainability and personalization (attempt in week 3)**
| Feature | What it does | Builds on |
|---|---|---|
| **Fatigue model + alerts** | Simulator adds fatigue (speed decay, more errors late). Engine detects a "fatigue alert" when a player's sprint speed and high-speed runs fall > 15% versus their first-half baseline: "Substitution likely" | sim, physical.py |
| **Duel tracker** | Detects recurring 1v1 battles (same attacker vs defender ≥ 4 times) and narrates the running score: "Reyes 3–1 Okafor in duels" | detectors |
| **Matchday mode (multi-match)** | Run 3 simulated matches at once; cross-match alerts personalized to the fan: "Your rival just conceded at Elmford", plus a live synthetic league table | orchestrator per match_id |
| **More personas** | `kids` (simple words, fun facts, emoji allowed), `fantasy` (synthetic fantasy points per player, captain-pick alerts), `accessibility` (audio-description style, high contrast, no motion) | personalize/ |
| **Narrative arcs** | Tracks match-long storylines (comeback in progress, keeper's day, a debutant's match) and threads them through the feed and recap, so commentary refers back to earlier moments | explain/ + recap |
| **LLM quality eval harness** | Offline eval: grounding pass rate, persona-fit and language-quality scored by LLM-as-judge on 200 sampled narrations; results table in docs. A strong signal for judges | narrate/ |

**Tier S: stretch (only after T-82 demo recording)**
- CV auto-eventing POC: YOLO + ByteTrack on a self-recorded or licensed clip → ball speed and pass events → `POST /ingest`.
- Fan prediction game ("next goal?"), scored against the WP model.
- More languages (de, it, ja, id, sw).

---

## 24. Submission checklist
**Mandatory (from the official rules)**
- [ ] **Public** GitHub repo URL on the project page (README, quickstart `make dev` / `azd up`, license)
- [ ] Project pitch / description naming the **Microsoft & Azure technologies** used, what it does and what problem it solves (`docs/pitch.md`, §21A)
- [ ] Demo video **under 2:00**, showing the project running on Azure, uploaded publicly to **YouTube or Vimeo**, URL on the project page
- [ ] Video contains no third-party trademarks or copyrighted music (fictional clubs, no PL marks, no unlicensed music)
- [ ] Project is newly built during the hackathon (git history shows it)
- [ ] Synthetic data only; a statement in the README that no real PL data is used or redistributed

**Strongly recommended (rubric)**
- [ ] Live Azure URLs for `/watch`, `/compare`, `/overlay`, `/studio`
- [ ] README: architecture diagram, rubric-mapping table (§1A), hero-tech list, agent diagram (§10A)
- [ ] `docs/`: dataset, schemas, metrics, overlay contract, agents, eval results, responsible AI, deploy (ACA + optional AKS)
- [ ] CI badge green; tests + eval numbers quoted in README
- [ ] `SECURITY.md`, `CODE_OF_CONDUCT.md`; no secrets in history (gitleaks clean)
- [ ] Project linked to the "Synthetic Match Insights Engine for Premier League Studio" challenge on the hackathon site
