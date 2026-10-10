# PitchLens Synthetic Football Dataset

A reproducible, football-realistic synthetic dataset: match metadata, ordered event streams and 5 Hz tracking for a fictional 20-club league. It was built for the *Inside the Game* hackathon, where **no real Premier League data may be used or redistributed**. Every club, player, venue and referee is invented.

| | |
|---|---|
| Version | dataset 1.0 · roster 1.0 (seed 2026) · event schema 1.0 |
| Size | 50 matches (5 scripted storylines + 45 organic), ~2,650 events per match, ~29,000 tracking frames per match |
| License | Data: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) · Code: MIT |
| Rebuild | `python -m pitchlens.sim.cli dataset --count 50` (byte-for-byte reproducible; see `manifest.json` checksums) |

## Files

```
data/
├─ rosters/league.json          20 clubs x 25 players: attributes, top speeds, career stats, club styles
├─ samples/                     committed: the 5 storyline matches (events gzipped, no tracking)
│  ├─ manifest.json
│  └─ SYN-<HOME>-<AWAY>-<seed>/{meta.json, events.jsonl.gz}
└─ matches/                     generated locally (git-ignored): the full dataset
   ├─ manifest.json             seeds, fixtures, final scores, per-match stats, SHA-256 per file
   └─ SYN-<HOME>-<AWAY>-<seed>/{meta.json, events.jsonl, tracking.npz}
```

- **`meta.json`** (`MatchMeta`): competition, venue, kick-off, weather, referee, both teams (colours, formation), full 25-man lineups with starters, pre-match strengths (expected goals per 90, used as the win-probability prior), seed and storyline.
- **`events.jsonl`** (`MatchEvent`, one JSON object per line, ordered by `seq`): 28 event types, from `pass`, `carry`, `dribble`, `shot`, `pressure`, `tackle` and `interception` to `possession_change`, `foul`, `card`, `substitution`, set pieces and `var_check`.
- **`tracking.npz`** (numpy, compressed): `ms` (T), `period` (T), `ball` (T x 3: x, y, height), `player_ids` (N), `xy` (T x N x 2, NaN while a player is off the pitch). Load it with `pitchlens.sim.tracking.Tracking.load(path)`; `Tracking.frame(i)` returns the `TrackingFrame` schema.

JSON Schemas for every record live in [`web/lib/schemas/`](../web/lib/schemas); the source of truth is `engine/pitchlens/schema/`.

## Conventions

- **Pitch:** 105 x 68 m. Origin is the bottom-left corner. The home team attacks towards +x in period 1 and -x in period 2. Facing the opponent's goal, a team's right flank is low y.
- **Time:** `match_ms` is continuous elapsed play time (monotonic, used for rolling windows). `clock_ms` is the football clock: period 2 starts at 45:00, so stoppage time reads 45+2 or 90+4. `duration_ms` is ball flight time for passes and shots, or run time for carries.
- **Possession:** `possession_id` increments only on explicit `possession_change` events.
- **Passes:** `end` is where the ball actually stopped. For intercepted passes that is the interception point, as in common real-world event feeds. `outcome` is `complete`, `incomplete`, `out` or `offside`. `qualifiers` include `through_ball`, `cross`, `long_ball`, `switch`, `kick_off` and `set_piece`.
- **Shots:** `ball_speed_kmh` is the shot speed. `related_player_id` is the assister. Qualifiers include `one_on_one`, `counter`, `free_kick` and `set_piece`. A `goal` event follows the scoring shot, with `score:<home>-<away>`.

## How it is generated

1. **Rosters.** A seeded generator creates clubs with tactical styles (press intensity, tempo, directness, width) and squads with position-biased attributes, personal top speeds (26–36 km/h) and career stats. About one player in eight sits one short of a round milestone (e.g. 49 goals), so milestone moments occur naturally.
2. **Possession-chain simulator.** On each touch the carrier may be pressed. Pressing is stronger against teams building up under a high press, and every defence tightens near its own box. The carrier then passes, carries, dribbles, shoots or clears. Choices depend on pitch zone, attributes, team style and game state; for example, teams trailing late shoot more. Teams hold a dynamic formation shape relative to the ball.
3. **Shared models.** Pass completion and goals are sampled from the **same** pass-difficulty and xG models the PitchLens analytics use (`interpret/passes.py`, `interpret/shots.py`), adjusted for player skill. Metrics computed on the data are therefore internally consistent with how it was generated.
4. **Tracking.** The 5 Hz positions are reconstructed from the events. The ball passes exactly through every event location, with a parabolic height for aerial balls. Players follow a lagged team shape. Carriers, receivers and pressers move at up to their personal top speed, and nobody ever exceeds it.
5. **Storylines.** Scripted beats are executed with the same action code as organic play, so they look like any other moment in the data.

| Storyline | Fixture | What happens |
|---|---|---|
| `comeback` | NHR v SVC | Home lead 2-0 after 52'. Away switch to a high press at 62' and win 3-2 with a 90+3 winner |
| `red_card_turn` | strongest v weakest club | Underdog score at 27', go down to ten at 38', sit deep and win 1-0 |
| `pressure_surge` | NHR v SVC | Away switch to an aggressive high press at 55' (PPDA collapses) |
| `star_player` | club with a forward on 49/74/… goals | 34 km/h sprint at 12', a line-breaking 35 m pass at 33', and a milestone goal at 58' |
| `chaos_derby` | NHR v SVC | Alternating foul-heavy chaos and calm control phases (0', 22', 41', 68', 82') |

## Calibration (45 organic matches, per match, both teams)

| Statistic | Mean | Range | Real-football reference |
|---|---|---|---|
| Passes | 1,118 | 992–1,261 | 850–1,150 |
| Pass completion | 78.3% | 74.8–82.7% | 75–86% |
| Shots | 25.0 | 14–39 | 22–28 |
| Goals | 2.76 | 0–6 | 2.4–3.1 |
| xG | 2.71 | 1.26–4.69 | 2.3–3.2 |
| Fouls | 27.7 | 16–40 | 18–27 |
| Cards | 4.6 | 1–8 | 3–5 |
| Corners | 8.6 | 2–19 | 8–12 |
| Throw-ins | 43.4 | 24–57 | 35–50 |
| Offsides | 3.3 | 0–8 | 2–5 |

Shot distances: about 6% from inside 8 m and 18% from beyond 25 m. Full-match outfield players cover a median of about 10 km. The engine test suite asserts the calibration ranges, so drift is caught in CI.

## Known limitations

- Tracking is reconstructed from events rather than simulated physically. Off-ball movement follows a shape model, so there are no individual off-ball runs, offside-line battles or goalkeeper footwork.
- One pitch size, no weather effects on play, no penalties yet, and no injuries beyond rare stoppages.
- Player identities are synthetic. Attribute distributions are plausible but not fitted to any real league.
- Possession counts (~355 per match) are higher than typical provider definitions because every change of control, including brief loose-ball recoveries, starts a new possession.

## Citation

> PitchLens Synthetic Football Dataset v1.0 (2026). Fictional league, events and 5 Hz tracking. CC BY 4.0.
