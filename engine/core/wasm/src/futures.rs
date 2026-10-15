//! Futures in the browser: the kernel's Monte Carlo model, single-threaded (results are identical to
//! the engine's multi-threaded runs for the same input, seed and paths).

use serde::{Deserialize, Serialize};
use wasm_bindgen::prelude::*;

use pitchlens_kernel::futures::{simulate, Effects, FuturesInput};
use pitchlens_kernel::win_prob;

/// `pitchlens.schema.futures.FuturesInput` as JSON (`GET /matches/{id}/futures/input`).
#[derive(Deserialize)]
struct Input {
    score: [u32; 2],
    minute: f64,
    minutes_left: f64,
    rate_90: [f64; 2],
    reds: [u32; 2],
    momentum: f64,
    field_tilt: f64,
    red_rate_90: [f64; 2],
    effects: EffectsInput,
    paths: u32,
    seed: u64,
}

#[derive(Deserialize)]
struct EffectsInput {
    game_state: bool,
    momentum: bool,
    discipline: bool,
}

/// What the Python binding returns (`simulate_futures`), as an object.
#[derive(Serialize)]
struct Output {
    paths: u32,
    home: f64,
    draw: f64,
    away: f64,
    scores: Vec<(u32, u32, f64)>,
    other: f64,
    comeback: Option<(&'static str, f64, f64)>,
    next_goal: (f64, f64, f64, Option<f64>),
    goals_left: (f64, f64),
}

/// The rest of the match simulated `input.paths` times (see `engine/core/kernel/src/futures.rs`).
#[wasm_bindgen(js_name = simulateFutures)]
pub fn simulate_futures(input: JsValue) -> Result<JsValue, JsError> {
    let i: Input = serde_wasm_bindgen::from_value(input)?;
    let input = FuturesInput {
        score: i.score,
        minute: i.minute,
        minutes_left: i.minutes_left,
        rate_90: i.rate_90,
        reds: i.reds,
        momentum: i.momentum,
        field_tilt: i.field_tilt,
        red_rate_90: i.red_rate_90,
        effects: Effects {
            game_state: i.effects.game_state,
            momentum: i.effects.momentum,
            discipline: i.effects.discipline,
        },
        paths: i.paths,
        seed: i.seed,
    };
    let f = simulate(&input).map_err(|e| JsError::new(&e.to_string()))?;
    let out = Output {
        paths: f.paths,
        home: f.home,
        draw: f.draw,
        away: f.away,
        scores: f.scores.iter().map(|s| (s.home, s.away, s.p)).collect(),
        other: f.other,
        comeback: f.comeback.map(|c| {
            (
                if c.side == 0 { "home" } else { "away" },
                c.avoid_defeat,
                c.win,
            )
        }),
        next_goal: (
            f.next_goal.home,
            f.next_goal.away,
            f.next_goal.none,
            f.next_goal.median_minute,
        ),
        goals_left: (f.goals_left[0], f.goals_left[1]),
    };
    // JSON-compatible: `None` becomes `null` (not `undefined`), as in the engine's JSON.
    Ok(out.serialize(&serde_wasm_bindgen::Serializer::json_compatible())?)
}

/// Minutes left including expected added time, as the engine computes them for a what-if minute.
#[wasm_bindgen(js_name = remainingMinutes)]
pub fn remaining_minutes(clock_minute: f64, period: u32) -> f64 {
    win_prob::remaining_minutes(clock_minute, period, None)
}
