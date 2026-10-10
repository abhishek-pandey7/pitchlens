# PitchLens Insights: what matters, and why

The **explain** stage turns metrics into `Insight`s. An insight is a moment worth telling someone about, with the evidence that shows why it matters. Detectors decide *that* something matters and tag *why* (`why_tags`). Every number an insight carries is copied from the interpret stage ([metrics.md](metrics.md)); the explain stage never computes new ones.

Source: `engine/pitchlens/explain/`. Entry points: `ExplainEngine.on_event / on_alert`, and `explain_match()` for stored matches.

## Insight anatomy

```json
{ "insight_id": "i-00041", "match_id": "SYN-NHR-SVC-7", "match_ms": 3930000, "clock_ms": 3720000, "period": 2,
  "type": "pressure_change", "salience": 0.61, "headline_key": "pressure_change.press_intensified",
  "entities": {"teams": ["SVC"], "players": []},
  "evidence": [
    {"metric": "pressing_actions_per_10_opp_passes", "team": "SVC", "from": 2.9, "to": 7.6, "window_min": 10},
    {"metric": "ppda", "team": "SVC", "from": 11.2, "to": 2.9, "window_min": 10},
    {"metric": "window_start_min", "team": "SVC", "value": 48.4}],
  "why_tags": ["pressure_change"] }
```

`headline_key` selects the instant template text in each language. The narrator agents may only verbalise `evidence`.

## Detectors

| Type | Fires when | Evidence | Cooldown |
|---|---|---|---|
| `goal` (`.go_ahead`, `.equaliser`, `.extends_lead`, `.pulls_back`) | goal event | score, scoring shot's xG and speed, assist-pass difficulty, win probability before → after; `late_drama` tag at 85'+ in a one-goal game | — |
| `career_milestone` (`.goals`, `.first_goal`, `.appearances`, `.debut`) | career goals reach 1 or a multiple of 25; appearances reach 1 or a multiple of 100 (from pre-match lineup totals) | the career total | — |
| `big_chance` (`.saved`, `.missed`, `.blocked`) | non-goal shot with xG ≥ 0.35 | xG, distance, shot speed | 1 min per team |
| `shot_speed_record` | fastest shot so far (≥ 95 km/h, after 3 shots), or any shot ≥ 110 km/h | speed, previous record | 5 min |
| `pass_of_note` (`.line_breaking`, `.difficult`) | completed pass with difficulty ≥ 62 (about the top 1%), or ≥ 50 while breaking 2+ lines | difficulty, distance, lines broken, into box | 2 min |
| `player_streak` (`.passes`, `.tackles`) | 20 / 30 / 45 consecutive completed passes; 3 tackles won in 5 min | streak length | 10 min (tackles) |
| `card` (`.yellow`, `.second_yellow`, `.red`) | card event | card, win probability before → after, players left on the pitch | — |
| `win_prob_swing` | a non-goal, non-card, non-shot event moves win probability ≥ 10 points | win probability before → after | 3 min |
| `momentum_shift` | momentum changes sides (|momentum| > 0.2) and holds for 3 min | momentum and field tilt, before → after | 8 min |
| `pressure_change` (`.press_intensified`, `.press_eased`) | sliding two-window Poisson likelihood-ratio test (below) | pressing actions per 10 opponent passes, PPDA, window start | 12 min per team |
| `control_chaos_flip` (`.to_control`, `.to_chaos`) | control index enters ≥ 60 or ≤ 40 and holds 4 min | index before → after, every component, controlling side's possession | 6 min |
| `rhythm_break` (`.faster`, `.slower`) | tempo z-score beyond ±1.8 for two samples (1 min) | tempo, match baseline, stoppage share | 10 min |
| `xg_swing` (`.lead_flipped`, `.gap_moved`) | xG lead changes hands (beyond ±0.3) or the gap moves ≥ 0.8 in 10 min | both teams' xG, gap before → after | 10 min |
| `sprint_threshold` / `distance_milestone` | tracking alerts: 33 km/h; 5 / 8 / 10 / 12 km; 10 / 20 / 30 sprints | value and threshold | once per player per threshold |

### Detecting a change in pressing

Pressing is the hardest pattern to call reliably, and our first two attempts were wrong in instructive ways:

1. **CUSUM on rolling PPDA:** PPDA rests on about 10 defensive actions per 10-minute window, so it is mostly noise. Overlapping rolling windows are also heavily autocorrelated, which breaks CUSUM's assumptions. Result: about 5 false alarms per match.
2. **Pressures per minute:** a successful press *wins the ball*, which leaves fewer opponent passes to press. Pressures per minute can stay flat even when the press doubles.

What works is counting the pressing team's **pressures, tackles and interceptions inside the opponent's own 60%, per opponent pass there** (PPDA's exposure, with about 4× the events). These go into independent one-minute bins, and each minute the last 10 minutes are tested against the 20 before:

`LLR = cA·ln(rA/p) + cB·ln(rB/p)`. A shift is reported when LLR ≥ 3 and the rate ratio is ≥ 1.6 (or ≤ 1/1.6).

On the `pressure_surge` storyline (away press switched on at 55') this fired between 57' and 64' in 10 of 10 seeds, with about 1.4 detections per organic match.

## Salience

```
salience = type_weight × (1 + 2·|ΔWP|) × rarity_mult × game_state_mult        (clipped to 0..1)
rarity_mult      0.75 .. 1.25   (from the detector: how extreme the value is)
game_state_mult  ×1.25 when 75'+ and within one goal; ×0.75 with a three-goal margin
```

Type weights: goal 0.5, red card 0.7, career milestone 0.6, win-probability swing 0.6, big chance 0.55, pressure intensified 0.5 (eased 0.35), momentum / xG swing / tactical note 0.45, control flip and pass of note 0.4, shot record and 33 km/h tag 0.35, rhythm break and streaks 0.3, yellow card 0.2, distance tags 0.12–0.2.

Example from the `comeback` storyline:

| Moment | Salience |
|---|---|
| 21' opener | 0.79 |
| 72' goal to make it 2–1 | 0.60 |
| 85' equaliser | 0.94 |
| 90+3 winner | 1.00 |

Personalisation (§11) multiplies on top of this per fan.

## Volume

An organic match produces about 30 insights:

| Type | Per match |
|---|---|
| Streaks | ~4 |
| Milestones | ~4 |
| Cards | ~4 |
| Control flips | ~3.5 |
| Rhythm breaks | ~3.4 |
| Passes of note | ~3 |
| Goals | ~3 |
| Momentum shifts | ~2 |
| Pressure changes | ~1.4 |

Analysts see all of them. A casual fan's feed is cut to the few with the highest personal salience.
