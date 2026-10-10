# PitchLens Metrics

Everything the **interpret** stage computes. Every number an insight, overlay or narration shows comes from one of these definitions. The AI layer never computes or invents numbers.

All metrics are incremental: `MatchState.apply(event)` and `MatchState.apply_frame(frame)` update them in about 11 µs per event. Geometry is evaluated in the acting team's **attacking frame** (goal at x = 105) on a 105 × 68 m pitch.

Source: `engine/pitchlens/interpret/`.

## Per event (`EnrichedEvent.metrics`)

| Metric | Events | Definition |
|---|---|---|
| `pass_distance_m` | passes, set pieces | Straight-line start → end |
| `pass_difficulty` | passes, set pieces | `100 × (1 − P(complete))`, from a logistic model on distance, forward component, pressure, opposition lines broken, aerial and into-box (`passes.py`). 0 = trivial, 100 = near impossible |
| `lines_broken` | passes | Opposition units (defence, midfield, attack; mean x from the **latest tracking frame, if under 1 s old**) the pass travels past. Omitted without fresh tracking, never guessed |
| `progressive` | passes | Completed, ends at least 25% closer to goal, and ends beyond 35 m |
| `into_box` | passes | Ends inside the penalty area having started outside it |
| `ball_speed_kmh` | passes | Ball speed of the pass (from the feed; also measured from tracking) |
| `xg` | shots | Logistic model on distance, goal-mouth angle, header, pressure and one-on-one; penalties = 0.76 (`shots.py`) |
| `shot_speed_kmh`, `shot_distance_m`, `on_target` | shots | From the event |
| `assist_pass_difficulty`, `assist_player_id` | shots | Difficulty of the completed pass the shooter received |
| `carry_distance_m`, `carry_speed_kmh` | carries | Distance and average speed (`duration_ms`) |
| `wp_before`, `wp_after` | goals, shots, cards | The acting team's win probability either side of the event |

## Players (`PlayerStats`)

Touches; passes, completion, distance and mean difficulty; hardest completed pass; progressive passes; lines broken; key passes; shots, on target, goals, assists, xG and top shot speed; tackles, interceptions, recoveries, pressures, clearances, blocks; fouls committed and won; dribbles attempted and won; carries and carry distance; cards; minutes on the pitch.

## Physical, from 5 Hz tracking (`physical.py`)

| Metric | Definition |
|---|---|
| Speed | Displacement over a sliding 1 s window (filters frame jitter), km/h |
| Distance | Sum of frame-to-frame displacement; never accumulates across half-time |
| High-speed running | Distance covered while moving ≥ 19.8 km/h |
| Sprints | Runs ≥ 25.2 km/h lasting ≥ 1 s (counted once per run); sprint distance |
| Top speed | Highest 1 s speed |
| Ball speed | Same windowed measure on the ball; match maximum kept |
| **Threshold alerts** | `PhysicalAlert` when a player first reaches **33 km/h**, **5 / 8 / 10 / 12 km**, or **10 / 20 / 30 sprints**. These drive player-tag overlays |

## Teams and match dynamics

| Metric | Window | Definition |
|---|---|---|
| Possession | match / 5 min | Time-weighted share between possession changes |
| **Field tilt** | 10 min | Team's final-third on-ball actions ÷ both teams' final-third on-ball actions |
| **PPDA** | 10 min | Opposition passes in their own 60% ÷ the team's tackles, interceptions and fouls beyond 42 m (capped at 40). **Lower = more intense press** |
| **Press rate** | 10 min | Pressures per minute inside the opponent's own 60% (higher = more aggressive). Pressing *changes* are detected on pressures, tackles and interceptions there **per opponent pass**, because a successful press wins the ball and leaves fewer passes to press (see [insights.md](insights.md)) |
| **Momentum** | half-life 3 min | Decayed threat (final-third entry 0.2, box entry 0.5, 3 × xG per shot); `(home − away) / (home + away + 0.5)`, in −1..1 |
| **Control ↔ chaos** | 5 min | 0 (chaos) … 100 (control). Logistic of the mean z-score of + passes per possession, + pass completion, − turnovers per min and − fouls and cards per min. Baselines are measured on 30 organic synthetic matches. Components are kept as evidence. ≥ 60 = control, ≤ 40 = chaos |
| **Tempo** | 5 min | On-ball actions per minute. A match-specific baseline (one sample per minute after 10') gives a z-score; \|z\| > 1.5 is a **rhythm break** |
| Stoppage share | 5 min | Share of time inside gaps of ≥ 8 s between events (ball dead) |
| **Win probability** | per event | Remaining goals ~ Poisson(rate × minutes left / 90). Rate = pre-match xG/90 blended with the live xG/90 (live weight rises as (minutes/90)^1.5 to 0.5 at full time). Red cards: ×0.7 for the short-handed side, ×1.2 for the opponent. Minutes left uses the announced added time once regulation ends, otherwise +5 |

`MatchState.readings()` returns all of these at once, and `MatchState.series` records them every 30 s of match time. The series feeds the analyst charts and the change-point detection behind *pressure change* insights.

## How we know they work

The test suite runs the metrics on scripted storylines and checks that they respond the way football says they should:

| Storyline | Check |
|---|---|
| `pressure_surge` | Away press rate rises ≥ 1.8× after the 55' press (median of 7 seeds, every seed > 1.2×), PPDA falls (median < 0.85×), and a *pressure change* insight fires between 55' and 72' in every tested seed |
| `comeback` | Away win probability < 12% at 60' and 100% at full time. The 90+3 winner adds > 40 points. Momentum swings towards the away side after the 62' press |
| `chaos_derby` | The control index is more than 15 points higher in the calm phases than in the chaotic ones |
| `red_card_turn` | The dismissal lowers the short-handed side's win probability |
| `star_player` | The scripted through ball rates ≥ 80 difficulty and breaks lines. The star's 33 km/h alert fires |

Organic matches are also checked against real-football ranges (see [dataset.md](dataset.md)): PPDA deciles 4–14, control-index deciles 21–79, and tempo of about 15 on-ball actions per minute.
